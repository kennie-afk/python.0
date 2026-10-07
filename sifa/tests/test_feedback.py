from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from sifa.serving.api import app, get_platform
from sifa.serving.platform import Platform
from sifa.simulation.world import build_world

KEY = "sifa-test-api-key-of-sufficient-length"


def fresh(mode: str) -> Platform:
    return Platform(world=build_world(n_users=60, n_items=150, seed=11), outcome_mode=mode)


@pytest.fixture
def real() -> Platform:
    return fresh("feedback")


@pytest.fixture
def client_for() -> Iterator[object]:
    clients: list[TestClient] = []

    def make(platform: Platform) -> TestClient:
        app.dependency_overrides[get_platform] = lambda: platform
        client = TestClient(app, headers={"X-Api-Key": KEY})
        clients.append(client)
        return client

    yield make
    app.dependency_overrides.clear()


def test_an_impression_is_stored_with_what_was_served(real: Platform) -> None:
    feed = real.recommend(real.world.users[0])
    stored = real.store.get_impression(feed["request_id"])

    assert stored is not None
    assert stored.model_version == feed["model_version"]
    assert stored.variant == feed["variant"]
    assert stored.items == tuple(item["item_id"] for item in feed["items"])
    assert feed["outcome_source"] == "feedback"


def test_in_feedback_mode_a_feed_is_a_trial_and_a_click_is_the_only_success(real: Platform) -> None:
    feeds = [real.recommend(user) for user in real.world.users[:30]]
    trials = real.counters.control_trials + real.counters.treatment_trials
    assert trials > 0
    assert real.counters.control_successes + real.counters.treatment_successes == 0

    target = next(feed for feed in feeds if feed["variant"] != "holdout")
    result = real.record_feedback(target["request_id"], target["items"][0]["item_id"], True)

    assert result["credited"] is True
    assert real.counters.control_successes + real.counters.treatment_successes == 1
    assert real.live_window.clicks + real.canary_window.clicks == 1


def test_a_second_click_on_the_same_request_does_not_count_twice(real: Platform) -> None:
    feed = next(
        f for f in (real.recommend(u) for u in real.world.users) if f["variant"] != "holdout"
    )
    first, second = feed["items"][0]["item_id"], feed["items"][1]["item_id"]

    real.record_feedback(feed["request_id"], first, True)
    again = real.record_feedback(feed["request_id"], second, True)

    assert again["recorded"] is True and again["credited"] is False
    assert real.counters.control_successes + real.counters.treatment_successes == 1


def test_feedback_is_idempotent_per_request_and_item(real: Platform) -> None:
    feed = real.recommend(real.world.users[1])
    item = feed["items"][0]["item_id"]
    topic = real.world.catalogue.topic[item]

    first = real.record_feedback(feed["request_id"], item, True)
    arm_after_first = list(real.pipeline.sampler.export()[topic])
    repeat = real.record_feedback(feed["request_id"], item, True)

    assert first["recorded"] is True
    assert repeat == {"recorded": False, "duplicate": True, "credited": False}
    assert real.pipeline.sampler.export()[topic] == arm_after_first
    assert real.store.feedback_summary()["feedback_events"] == 1


def test_a_click_reaches_the_thompson_sampler(real: Platform) -> None:
    feed = real.recommend(real.world.users[2])
    item = feed["items"][0]["item_id"]
    topic = real.world.catalogue.topic[item]

    real.record_feedback(feed["request_id"], item, True)
    assert real.pipeline.sampler.export()[topic] == [2.0, 1.0]

    other = feed["items"][1]["item_id"]
    other_topic = real.world.catalogue.topic[other]
    before = real.pipeline.sampler.export().get(other_topic, [1.0, 1.0])
    real.record_feedback(feed["request_id"], other, False)
    assert real.pipeline.sampler.export()[other_topic][1] == before[1] + 1.0


def test_in_simulated_mode_feedback_trains_the_bandit_but_not_the_counters() -> None:
    platform = fresh("simulated")
    feed = platform.recommend(platform.world.users[0])
    before = (platform.counters.control_successes, platform.counters.treatment_successes)

    platform.record_feedback(feed["request_id"], feed["items"][0]["item_id"], True)

    assert (platform.counters.control_successes, platform.counters.treatment_successes) == before
    assert platform.pipeline.sampler.export()


def test_switching_mode_starts_the_comparison_again(real: Platform) -> None:
    for user in real.world.users[:20]:
        real.recommend(user)
    real.set_outcome_mode("simulated")

    assert real.counters.control_trials + real.counters.treatment_trials == 0
    assert real.live_window.impressions == 0
    assert real.outcome_mode == "simulated"


def test_feedback_over_http_and_its_errors(real: Platform, client_for) -> None:  # type: ignore[no-untyped-def]
    client = client_for(real)
    feed = client.get(f"/v1/feed/{real.world.users[0]}").json()
    item = feed["items"][0]["item_id"]
    body = {"request_id": feed["request_id"], "item_id": item, "clicked": True}

    assert client.post("/v1/feedback", json=body).json()["recorded"] is True
    assert client.post("/v1/feedback", json=body).json()["duplicate"] is True
    unknown = client.post("/v1/feedback", json={**body, "request_id": "nope"})
    assert unknown.status_code == 404
    elsewhere = client.post("/v1/feedback", json={**body, "item_id": "i-not-served"})
    assert elsewhere.status_code == 400
    assert client.post("/v1/feedback", json={"request_id": "x"}).status_code == 422


def test_a_click_arriving_after_a_rollback_is_recorded_but_not_credited_to_a_new_window(
    real: Platform,
) -> None:
    real.promote_candidate()
    canary_feeds = [
        f for f in (real.recommend(u) for u in real.world.users) if f["model_stage"] == "canary"
    ]
    real.rollback("test")

    result = real.record_feedback(
        canary_feeds[0]["request_id"], canary_feeds[0]["items"][0]["item_id"], True
    )

    assert result["recorded"] is True
    assert real.canary_window.clicks == 0
