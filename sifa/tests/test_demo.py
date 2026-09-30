from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from sifa.registry.models import Stage
from sifa.serving import api as api_module
from sifa.serving.api import app, get_platform
from sifa.serving.demo import TAG, warm_up
from sifa.serving.platform import Platform
from sifa.simulation.world import build_world

TEST_API_KEY = "sifa-test-api-key-of-sufficient-length"

@pytest.fixture(scope="module")
def platform() -> Platform:
    return Platform(world=build_world(n_users=70, n_items=180, seed=41))

@pytest.fixture(scope="module")
def client(platform: Platform) -> Iterator[TestClient]:
    app.dependency_overrides[get_platform] = lambda: platform
    with TestClient(app, headers={"X-Api-Key": TEST_API_KEY}) as test_client:
        yield test_client
    app.dependency_overrides.clear()

def test_warming_up_serves_real_traffic_and_tags_its_release_history(platform: Platform) -> None:
    served = warm_up(platform, 60)

    assert served == 60
    assert platform.counters.control_trials + platform.counters.treatment_trials > 0
    versions = platform.registry.versions("ranker")
    assert [v.version for v in versions] == [1, 2, 3]
    assert [v.stage for v in versions] == [Stage.ARCHIVED, Stage.LIVE, Stage.ROLLED_BACK]
    reasons = [reason for v in versions[1:] for _, _, reason in v.history if reason != "registered"]
    assert reasons and all(reason.endswith(TAG) for reason in reasons)

def test_warming_up_twice_does_not_duplicate_the_history(platform: Platform) -> None:
    warm_up(platform, 5)
    assert len(platform.registry.versions("ranker")) == 3
    assert len([v for v in platform.registry.versions("ranker") if v.stage is Stage.LIVE]) == 1

def test_with_no_shift_every_feature_reads_identical_to_its_reference(client: TestClient) -> None:
    reports = client.get("/v1/drift").json()
    assert reports
    assert all(report["severity"] == "stable" for report in reports)
    # A 0/1 flag compared with an identical copy of itself must not show a KS difference.
    assert all(report["ks_statistic"] < 0.1 for report in reports), reports

def test_an_injected_shift_is_still_caught(client: TestClient) -> None:
    reports = client.get("/v1/drift?shift=2").json()
    assert any(report["drifted"] for report in reports)

def test_a_second_benchmark_is_refused_while_one_is_running(client: TestClient) -> None:
    assert api_module._benchmark_lock.acquire(blocking=False)
    try:
        response = client.get("/v1/retrieval/benchmark?corpus=1000")
        assert response.status_code == 409
        assert "already running" in response.json()["detail"]
    finally:
        api_module._benchmark_lock.release()

def test_the_benchmark_lock_is_released_after_a_refusal_and_after_a_run(client: TestClient) -> None:
    assert client.get("/v1/retrieval/benchmark?corpus=100").status_code == 422  # below the minimum
    assert api_module._benchmark_lock.acquire(blocking=False)
    api_module._benchmark_lock.release()
