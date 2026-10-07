from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from sifa.core.errors import SifaError
from sifa.serving.api import app, get_platform
from sifa.serving.platform import Platform
from sifa.simulation.world import build_world

TEST_API_KEY = "sifa-test-api-key-of-sufficient-length"

@pytest.fixture(scope="module")
def platform() -> Platform:
    return Platform(world=build_world(n_users=90, n_items=220, seed=31))

@pytest.fixture(scope="module")
def client(platform: Platform) -> Iterator[TestClient]:
    app.dependency_overrides[get_platform] = lambda: platform
    with TestClient(app, headers={"X-Api-Key": TEST_API_KEY}) as test_client:
        yield test_client
    app.dependency_overrides.clear()

def test_the_platform_trains_every_stage(platform: Platform) -> None:
    assert platform.tower.is_trained is True
    assert platform.ranker.is_trained is True
    assert 0 < len(platform.retriever.index) <= len(platform.world.items)

def test_only_items_with_interactions_are_retrievable(platform: Platform) -> None:
    interacted = {item for _, item in platform.world.interactions}
    assert len(platform.retriever.index) == len(interacted)

def test_the_overview_reports_the_real_index_size(platform: Platform) -> None:
    assert platform.health()["index_size"] == len(platform.retriever.index)

def test_the_ranker_learns_something_useful(platform: Platform) -> None:
    assert platform.training.holdout_auc > 0.6

def test_a_feed_comes_back_with_items(platform: Platform) -> None:
    user = platform.world.users[0]
    feed = platform.pipeline.recommend(user)
    assert len(feed.items) > 0
    assert feed.user_id == user

def test_a_feed_never_repeats_an_item(platform: Platform) -> None:
    feed = platform.pipeline.recommend(platform.world.users[3])
    ids = [item.item_id for item in feed.items]
    assert len(ids) == len(set(ids))

def test_a_feed_is_assigned_to_a_variant(platform: Platform) -> None:
    feed = platform.pipeline.recommend(platform.world.users[1])
    assert feed.variant in {"control", "treatment", "holdout"}

def test_a_feed_reports_its_latency_and_depth(platform: Platform) -> None:
    feed = platform.pipeline.recommend(platform.world.users[2])
    assert feed.latency_ms > 0.0
    assert feed.retrieved >= len(feed.items)

def test_a_feed_carries_its_diagnostics(platform: Platform) -> None:
    feed = platform.pipeline.recommend(platform.world.users[4])
    assert "diversity_lambda" in feed.diagnostics

def test_seen_items_are_not_served_again(platform: Platform) -> None:
    user = platform.world.users[5]
    first = platform.pipeline.recommend(user)
    seen = {item.item_id for item in first.items}
    second = platform.pipeline.recommend(user, seen=seen)
    assert not (seen & {item.item_id for item in second.items})

def test_the_feed_respects_the_author_cap(platform: Platform) -> None:
    feed = platform.pipeline.recommend(platform.world.users[6])
    counts: dict[str, int] = {}
    for item in feed.items:
        author = platform.world.catalogue.author[item.item_id]
        counts[author] = counts.get(author, 0) + 1
    assert max(counts.values()) <= 3

def test_recommendation_is_mostly_on_topic(platform: Platform) -> None:
    hits = 0
    served = 0
    for user in platform.world.users[:25]:
        result = platform.recommend(user)
        topic = platform.world.user_topic[user]
        for item in result["items"]:
            served += 1
            hits += platform.world.catalogue.topic[item["item_id"]] == topic
    assert served > 0
    assert hits / served > 0.4

def test_recommendation_reports_ranking_quality(platform: Platform) -> None:
    result = platform.recommend(platform.world.users[7])
    assert 0.0 <= result["ndcg_at_10"] <= 1.0
    assert 0.0 <= result["recall_at_15"] <= 1.0

def test_an_unknown_user_is_refused(platform: Platform) -> None:
    with pytest.raises(SifaError):
        platform.recommend("nobody-at-all")

def test_the_experiment_reports_a_decision(platform: Platform) -> None:
    for user in platform.world.users[:30]:
        platform.recommend(user)
    assert platform.experiment_state().samples >= 0

def test_drift_is_quiet_without_a_shift(platform: Platform) -> None:
    reports = platform.drift(live_shift=0.0)
    assert reports
    assert not any(report.drifted for report in reports)

def test_drift_fires_on_a_large_shift(platform: Platform) -> None:
    assert any(report.drifted for report in platform.drift(live_shift=3.0))

def test_health_is_reported(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_the_overview_summarises_the_platform(client: TestClient) -> None:
    body = client.get("/v1/overview").json()
    assert body["items"] == 220
    assert body["users"] == 90
    assert 0 < body["index_size"] <= body["items"]
    assert "guard_healthy" in body

def test_users_can_be_listed(client: TestClient) -> None:
    body = client.get("/v1/users?limit=5").json()
    assert len(body) == 5
    assert "user_id" in body[0]
    assert "topic" in body[0]

def test_the_user_limit_is_validated(client: TestClient) -> None:
    assert client.get("/v1/users?limit=0").status_code == 422

def test_a_feed_is_served_over_http(client: TestClient, platform: Platform) -> None:
    body = client.get(f"/v1/feed/{platform.world.users[0]}").json()
    assert body["items"]
    assert body["items"][0]["score"] >= body["items"][-1]["score"]

def test_an_unknown_user_returns_a_clean_error(client: TestClient) -> None:
    response = client.get("/v1/feed/nobody-at-all")
    assert response.status_code in {400, 404}
    assert "detail" in response.json()

def test_retrieval_can_be_inspected(client: TestClient, platform: Platform) -> None:
    body = client.get(f"/v1/retrieval/{platform.world.users[1]}").json()
    assert body["results"]
    assert 0.0 <= body["recall_vs_brute_force"] <= 1.0
    assert body["exact_ms"] > 0.0

def test_the_model_card_is_served(client: TestClient) -> None:
    body = client.get("/v1/model").json()
    assert body["holdout_auc"] > 0.0
    assert body["features"]
    assert body["tower"]["final_loss"] < body["tower"]["first_loss"]

def test_the_registry_lists_versions(client: TestClient) -> None:
    body = client.get("/v1/registry").json()
    assert body
    assert {"stage", "version"} <= set(body[0])

def test_a_promotion_moves_a_version_forward(client: TestClient) -> None:
    before = client.get("/v1/registry").json()
    response = client.post("/v1/registry/promote")
    assert response.status_code == 200
    after = client.get("/v1/registry").json()
    assert len(after) == len(before) + 1
    newest = max(after, key=lambda row: row["version"])
    assert newest["stage"] == "canary"
    assert 0.0 < newest["traffic"] < 1.0

def test_a_rollback_withdraws_the_canary_and_leaves_live_alone(client: TestClient) -> None:
    client.post("/v1/registry/promote")
    live_before = next(row for row in client.get("/v1/registry").json() if row["stage"] == "live")

    response = client.post("/v1/registry/rollback")

    assert response.status_code == 200
    rows = client.get("/v1/registry").json()
    assert response.json()["now_live"] == live_before["label"]
    assert not any(row["stage"] == "canary" for row in rows)
    assert any(row["stage"] == "rolled_back" for row in rows)

def test_drift_is_exposed(client: TestClient) -> None:
    quiet = client.get("/v1/drift?shift=0").json()
    shifted = client.get("/v1/drift?shift=2").json()

    assert quiet
    assert "psi" in quiet[0]
    assert not any(row["drifted"] for row in quiet)
    assert any(row["drifted"] for row in shifted)

def test_the_experiment_is_exposed(client: TestClient) -> None:
    body = client.get("/v1/experiment").json()
    assert "decision" in body

def test_a_simulation_can_be_driven(client: TestClient) -> None:
    body = client.post("/v1/simulate", params={"requests": 40}).json()
    assert body["requests"] == 40

def test_the_benchmark_reports_recall_and_latency(client: TestClient) -> None:
    started = client.post("/v1/retrieval/benchmark", params={"corpus": 1200, "k": 10})
    assert started.status_code == 202
    job = started.json()["job_id"]
    for _ in range(120):
        polled = client.get(f"/v1/retrieval/benchmark/{job}").json()
        if polled["status"] != "running":
            break
        time.sleep(0.5)
    assert polled["status"] == "done"
    body = polled["result"]
    assert body["corpus"] == 1200
    assert body["exhaustive_ms"] > 0.0
    assert [row["ef_search"] for row in body["curve"]] == [32, 64, 128, 256]
    recalls = [row["recall"] for row in body["curve"]]
    assert all(0.0 <= value <= 1.0 for value in recalls)
    assert recalls[-1] >= recalls[0]

def test_the_platform_refuses_a_caller_without_an_api_key(client: TestClient) -> None:
    assert client.get("/v1/overview", headers={"X-Api-Key": ""}).status_code == 401

def test_the_platform_refuses_a_caller_with_the_wrong_api_key(client: TestClient) -> None:
    wrong = {"X-Api-Key": "not-the-configured-key-but-long-enough"}
    assert client.get("/v1/overview", headers=wrong).status_code == 401

def test_promoting_a_model_needs_an_api_key(client: TestClient) -> None:
    assert client.post("/v1/registry/promote", headers={"X-Api-Key": ""}).status_code == 401

def test_health_stays_open_so_orchestrators_can_probe_it(client: TestClient) -> None:
    assert client.get("/healthz", headers={"X-Api-Key": ""}).status_code == 200

def test_the_recorded_movielens_evaluation_is_served_and_self_consistent(
    client: TestClient,
) -> None:
    body = client.get("/v1/evaluation/movielens").json()

    assert body["users"] > 100
    assert {"random", "popularity", "sifa_two_tower_exact", "sifa_two_tower_hnsw"} <= set(
        body["test"]
    )
    for scores in body["test"].values():
        keys = ("hit_rate_ci95_low", "hit_rate_at_10", "hit_rate_ci95_high")
        low, mid, high = (scores[key] for key in keys)
        assert low <= mid <= high
        assert 0.0 <= scores["ndcg_at_10"] <= scores["hit_rate_at_10"] + 1e-9
    assert body["test"]["random"]["hit_rate_at_10"] < body["test"]["popularity"]["hit_rate_at_10"]
    recalls = [row["sifa_recall_at_10"] for row in body["ann_index"]["sweep"]]
    assert recalls == sorted(recalls)

def test_the_evaluation_route_needs_the_api_key() -> None:
    with TestClient(app) as anonymous:
        assert anonymous.get("/v1/evaluation/movielens").status_code in (401, 403)
