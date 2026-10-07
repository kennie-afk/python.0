from __future__ import annotations

import pytest

from sifa.core.errors import RegistryError
from sifa.monitoring.guard import ServingWindow
from sifa.registry.models import ModelRegistry, Stage
from sifa.serving.platform import Platform
from sifa.simulation.world import build_world


@pytest.fixture
def platform() -> Platform:
    return Platform(world=build_world(n_users=90, n_items=220, seed=31))


def test_a_promotion_trains_a_different_model_from_the_live_one(platform: Platform) -> None:
    outcome = platform.promote_candidate()

    live = platform.registry.live("ranker")
    candidate = outcome["version"]
    assert live is not None
    assert candidate.payload is not live.payload
    assert candidate.metrics["seed"] != live.metrics.get("seed", 13.0)


def test_the_registry_decides_which_model_answers(platform: Platform) -> None:
    platform.promote_candidate()
    canary = platform.registry.canary("ranker")
    assert canary is not None

    stages = {platform.recommend(user)["model_stage"] for user in platform.world.users}
    versions = {platform.recommend(user)["model_version"] for user in platform.world.users}

    assert stages == {"canary", "live"}
    assert versions == {1, canary.version}


def test_a_user_keeps_the_same_model_while_a_canary_runs(platform: Platform) -> None:
    platform.promote_candidate()
    user = platform.world.users[0]

    seen = {platform.recommend(user)["model_version"] for _ in range(5)}

    assert len(seen) == 1


def test_canary_share_tracks_the_registry_traffic_split(platform: Platform) -> None:
    platform.promote_candidate()
    canary = platform.registry.canary("ranker")
    assert canary is not None

    served = [platform.recommend(user)["model_stage"] for user in platform.world.users]
    share = served.count("canary") / len(served)

    assert abs(share - canary.traffic) < 0.1


def test_a_bad_canary_is_rolled_back_by_the_guard(platform: Platform) -> None:
    platform.promote_candidate()
    platform.live_window = ServingWindow()
    platform.canary_window = ServingWindow()
    for _ in range(400):
        platform.live_window.record(True, 0.9, 5.0)
        platform.canary_window.record(False, 0.9, 5.0)

    platform._enforce_guard()

    assert platform.registry.canary("ranker") is None
    rolled = [v for v in platform.registry.versions("ranker") if v.stage is Stage.ROLLED_BACK]
    assert len(rolled) == 1
    assert "click through" in rolled[0].history[-1][2]
    assert platform.registry.live("ranker") is not None


def test_a_canary_with_too_little_traffic_cannot_advance(platform: Platform) -> None:
    platform.promote_candidate()

    with pytest.raises(RegistryError, match="needs"):
        platform.advance_canary()


def test_a_healthy_canary_with_enough_traffic_advances(platform: Platform) -> None:
    outcome = platform.promote_candidate()
    platform.live_window = ServingWindow()
    platform.canary_window = ServingWindow()
    for _ in range(400):
        platform.live_window.record(True, 0.95, 5.0)
        platform.canary_window.record(True, 0.95, 5.0)

    promoted = platform.advance_canary()

    assert promoted.version == outcome["version"].version
    assert platform.registry.live("ranker") is promoted
    assert platform.registry.canary("ranker") is None


def test_serving_records_real_latency_not_a_constant(platform: Platform) -> None:
    for user in platform.world.users[:30]:
        platform.recommend(user)

    latencies = set(platform.live_window.latencies)

    assert len(latencies) > 5
    assert 8.0 not in latencies


def test_two_canaries_cannot_run_at_once() -> None:
    registry = ModelRegistry()
    for _ in range(2):
        version = registry.register("m", object())
        registry.transition("m", version.version, Stage.SHADOW)
        if version.version == 1:
            registry.transition("m", 1, Stage.CANARY)

    with pytest.raises(RegistryError, match="already in canary"):
        registry.transition("m", 2, Stage.CANARY)


def test_a_restore_after_withdrawing_live_is_a_named_system_edge() -> None:
    registry = ModelRegistry()
    first = registry.register("m", object())
    for stage in (Stage.SHADOW, Stage.CANARY, Stage.LIVE):
        registry.transition("m", first.version, stage)
    second = registry.register("m", object())
    for stage in (Stage.SHADOW, Stage.CANARY, Stage.LIVE):
        registry.transition("m", second.version, stage)

    registry.rollback("m", "bad")

    assert registry.live("m") is first
    with pytest.raises(RegistryError):
        registry.transition("m", second.version, Stage.LIVE)
