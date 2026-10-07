from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sifa.serving import api as api_module
from sifa.serving.api import app, get_platform
from sifa.serving.platform import Platform
from sifa.serving.scheduler import Scheduler
from sifa.simulation.world import build_world

ADMIN = "admin-key-of-sufficient-length-1"
OPERATOR = "operator-key-of-sufficient-length"
VIEWER = "viewer-key-of-sufficient-length-1"
KEYS = f"{ADMIN}:admin,{OPERATOR}:operator,{VIEWER}:viewer"


@pytest.fixture(scope="module")
def platform() -> Platform:
    return Platform(world=build_world(n_users=60, n_items=150, seed=19))


@pytest.fixture
def secured(platform: Platform, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SIFA_API_KEYS", KEYS)
    app.dependency_overrides[get_platform] = lambda: platform
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def as_(client: TestClient, key: str) -> dict[str, str]:
    return {"X-Api-Key": key}


# -- S3: model card -----------------------------------------------------------------------------
def test_a_version_carries_a_model_card(platform: Platform, secured: TestClient) -> None:
    card = secured.get("/v1/registry/1", headers=as_(secured, VIEWER)).json()["card"]

    assert card["data_fingerprint"] == platform.fingerprint
    assert len(card["feature_schema_hash"]) == 64
    assert card["seeds"]["ranker"] == 13
    assert {"numpy", "scikit-learn", "scipy", "python"} <= set(card["libraries"])
    assert card["metrics"]["holdout_auc"] > 0.5
    assert card["training_rows"] > 0
    assert "git_sha" in card and "trained_at" in card and card["training_seconds"] >= 0
    assert card["config"]["feature_order"]


def test_an_unknown_version_is_a_404(secured: TestClient) -> None:
    assert secured.get("/v1/registry/99", headers=as_(secured, VIEWER)).status_code == 404


# -- S4: drift on traffic that was really served ---------------------------------------------------
def test_live_drift_waits_for_traffic_then_compares_what_was_served() -> None:
    fresh = Platform(world=build_world(n_users=60, n_items=150, seed=23))
    empty = fresh.live_drift()
    assert empty["ready"] is False and empty["reports"] == []

    for user in fresh.world.users:
        fresh.recommend(user)
    served = fresh.live_drift()

    assert served["ready"] is True and served["rows"] >= 100
    names = {report.feature for report in served["reports"]}
    assert {"user_ctr", "item_ctr", "retrieval_score"} <= names
    # The ranker is trained only on what serving supplies, so there is no skew to report. (It once
    # trained on the simulator's own topic_match and item_quality, which serving read as 0.)
    assert served["trained_but_not_served"] == []
    assert not {"topic_match", "item_quality"} & names


def test_a_real_change_in_serving_inputs_is_caught_without_any_injection() -> None:
    fresh = Platform(world=build_world(n_users=60, n_items=150, seed=23))
    later = datetime.now(UTC)
    for item in fresh.world.items:
        fresh.world.item_features.write(item, later, {"item_ctr": 0.97, "item_length": 5000.0,
                                                      "item_age_hours": 1.0})
    for user in fresh.world.users:
        fresh.recommend(user)

    drifted = {r.feature for r in fresh.live_drift()["reports"] if r.drifted}
    assert {"item_ctr", "item_length"} <= drifted


# -- S5: roles, actors, rate limits, rotation --
def test_roles_gate_what_a_key_may_do(secured: TestClient) -> None:
    assert secured.get("/v1/overview", headers=as_(secured, VIEWER)).status_code == 200
    assert secured.post("/v1/simulate?requests=1", headers=as_(secured, VIEWER)).status_code == 403
    assert secured.post("/v1/registry/promote", headers=as_(secured, OPERATOR)).status_code == 403
    assert secured.post("/v1/simulate?requests=1", headers=as_(secured, OPERATOR)).is_success
    assert secured.get("/v1/overview", headers={"X-Api-Key": "wrong" * 6}).status_code == 401


def test_a_bare_key_is_still_an_admin(platform: Platform, monkeypatch: pytest.MonkeyPatch) -> None:
    from sifa.serving.auth import Role, configured_principals

    monkeypatch.setenv("SIFA_API_KEYS", f"{ADMIN},{VIEWER}:viewer")
    assert configured_principals() == [(ADMIN, Role.ADMIN), (VIEWER, Role.VIEWER)]


def test_an_unknown_role_is_a_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from sifa.serving.auth import ConfigurationError, configured_principals

    monkeypatch.setenv("SIFA_API_KEYS", f"{ADMIN}:superuser")
    with pytest.raises(ConfigurationError, match="unknown role"):
        configured_principals()


def test_the_acting_key_is_recorded_in_the_registry_history(
    platform: Platform, secured: TestClient
) -> None:
    secured.post("/v1/registry/promote", headers=as_(secured, ADMIN))
    version = platform.registry.canary("ranker")
    assert version is not None
    actors = set(version.actors)

    assert any(actor.startswith("admin:") for actor in actors)
    assert ADMIN not in "".join(actors)  # a key id, never the key
    history = secured.get(f"/v1/registry/{version.version}", headers=as_(secured, VIEWER)).json()
    assert history["history"][-1]["actor"].startswith("admin:")
    secured.post("/v1/registry/rollback", headers=as_(secured, ADMIN))


def test_expensive_endpoints_are_rate_limited(
    secured: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIFA_EXPENSIVE_PER_MINUTE", "2")
    codes = [
        secured.post("/v1/simulate?requests=1", headers=as_(secured, OPERATOR)).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 429]
    other = secured.post("/v1/simulate?requests=1", headers=as_(secured, ADMIN)).status_code
    assert other == 200  # the limit is per key


def test_rotation_works_by_listing_both_keys(
    platform: Platform, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.dependency_overrides[get_platform] = lambda: platform
    old, new = "old-key-of-sufficient-length-001", "new-key-of-sufficient-length-001"
    try:
        with TestClient(app) as client:
            monkeypatch.setenv("SIFA_API_KEYS", old)
            assert client.get("/v1/users", headers={"X-Api-Key": new}).status_code == 401
            monkeypatch.setenv("SIFA_API_KEYS", f"{old},{new}")
            assert client.get("/v1/users", headers={"X-Api-Key": new}).status_code == 200
            assert client.get("/v1/users", headers={"X-Api-Key": old}).status_code == 200
            monkeypatch.setenv("SIFA_API_KEYS", new)
            assert client.get("/v1/users", headers={"X-Api-Key": old}).status_code == 401
    finally:
        app.dependency_overrides.clear()


# -- S6: alerts, scheduler, export -----------------------------------------------------------------
class Hook:
    def __init__(self, status: int = 200) -> None:
        self.received: list[dict[str, Any]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers["Content-Length"])
                outer.received.append(json.loads(self.rfile.read(length)))
                self.send_response(status)
                self.end_headers()

            def log_message(self, *_: object) -> None:
                return

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def hook() -> Iterator[Hook]:
    stub = Hook()
    yield stub
    stub.close()


def test_a_guard_rollback_is_delivered_to_the_webhook(hook: Hook) -> None:
    fresh = Platform(world=build_world(n_users=60, n_items=150, seed=29))
    fresh.promote_candidate()
    fresh.canary_window.impressions = 0
    canary_version = fresh.registry.canary("ranker")
    assert canary_version is not None
    # A canary that the guard must reject: plenty of traffic, no clicks, live is clicking.
    for _ in range(40):
        fresh.live_window.record(True, 0.9, 5.0)
    for _ in range(300 - 1):
        fresh.canary_window.record(False, 0.9, 5.0)
    users = [u for u in fresh.world.users if fresh._bucket(u) < 0.1]
    while fresh.registry.canary("ranker") is not None:
        fresh.recommend(users[0])

    outcome = Scheduler(fresh, webhook=hook.url).tick()

    assert outcome["delivered"] >= 1
    kinds = [message["kind"] for message in hook.received]
    assert "guard_rollback" in kinds
    assert all(message["source"] == "sifa" for message in hook.received)
    assert fresh.store.pending_alerts() == []


def test_an_undelivered_alert_stays_queued_and_is_retried() -> None:
    fresh = Platform(world=build_world(n_users=60, n_items=150, seed=29))
    fresh.enqueue_alert("drift", "x", {})
    dead = Scheduler(fresh, webhook="http://127.0.0.1:9/hook")
    assert dead.deliver() == 0
    assert len(fresh.store.pending_alerts()) == 1

    stub = Hook()
    try:
        assert Scheduler(fresh, webhook=stub.url).deliver() == 1
    finally:
        stub.close()
    assert fresh.store.pending_alerts() == []


def test_live_drift_raises_one_alert_not_one_per_tick(hook: Hook) -> None:
    fresh = Platform(world=build_world(n_users=60, n_items=150, seed=23))
    later = datetime.now(UTC)
    for item in fresh.world.items:
        fresh.world.item_features.write(item, later, {"item_ctr": 0.97, "item_length": 5000.0,
                                                      "item_age_hours": 1.0})
    for user in fresh.world.users:
        fresh.recommend(user)
    scheduler = Scheduler(fresh, webhook=hook.url)

    scheduler.tick()
    scheduler.tick()

    drift = [m for m in hook.received if m["kind"] == "drift"]
    assert len(drift) == 1
    assert "item_ctr" in drift[0]["payload"]["features"]


def test_scheduled_retraining_is_off_by_default_and_starts_only_a_canary() -> None:
    fresh = Platform(world=build_world(n_users=60, n_items=150, seed=29))
    assert Scheduler(fresh).tick()["retrained"] == 0
    assert len(fresh.registry.versions("ranker")) == 1

    due = Scheduler(fresh, retrain_every=3600)
    assert due.tick()["retrained"] == 1
    candidate = fresh.registry.canary("ranker")
    assert candidate is not None and candidate.version == 2
    assert candidate.actors[-1] == "system:scheduler"
    live = fresh.registry.live("ranker")
    assert live is not None and live.version == 1  # not promoted
    assert due.tick()["retrained"] == 0  # the interval has not elapsed


def test_the_webhook_must_be_http() -> None:
    fresh = Platform(world=build_world(n_users=60, n_items=150, seed=29))
    with pytest.raises(ValueError, match="http"):
        Scheduler(fresh, webhook="file:///etc/passwd")


def test_the_export_is_the_full_record(platform: Platform, secured: TestClient) -> None:
    platform.enqueue_alert("retrain", "for the export", {})
    body = secured.get("/v1/registry/export", headers=as_(secured, OPERATOR)).json()

    assert body["data_fingerprint"] == platform.fingerprint
    assert body["versions"][0]["card"]["data_fingerprint"] == platform.fingerprint
    assert body["versions"][0]["history"][0]["actor"]
    assert any(alert["message"] == "for the export" for alert in body["alerts"])
    assert secured.get("/v1/registry/export", headers=as_(secured, VIEWER)).status_code == 403


# -- S7: readiness and the benchmark job --
def test_readiness_is_separate_from_liveness(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_module, "_platform", None)
    with TestClient(app) as client:
        app.dependency_overrides.clear()
        assert client.get("/healthz").status_code == 200
        # lifespan started a background build because nothing overrides get_platform here
        if client.get("/readyz").status_code == 503:
            assert client.get("/readyz").json()["status"] == "starting"
            blocked = client.get("/v1/overview", headers={"X-Api-Key": VIEWER})
            assert blocked.status_code in (401, 503)
        deadline = time.time() + 60
        while time.time() < deadline and client.get("/readyz").status_code != 200:
            time.sleep(0.5)
        assert client.get("/readyz").status_code == 200
    if api_module._scheduler is not None:
        api_module._scheduler.stop()
    monkeypatch.setattr(api_module, "_boot_thread", None)


def test_the_benchmark_is_a_job_that_is_polled(secured: TestClient) -> None:
    started = secured.post("/v1/retrieval/benchmark?corpus=1000", headers=as_(secured, OPERATOR))
    assert started.status_code == 202
    job_id = started.json()["job_id"]

    first = secured.get(f"/v1/retrieval/benchmark/{job_id}", headers=as_(secured, VIEWER)).json()
    assert first["status"] in ("running", "done")

    deadline = time.time() + 60
    body = first
    while body["status"] == "running" and time.time() < deadline:
        time.sleep(0.5)
        body = secured.get(f"/v1/retrieval/benchmark/{job_id}", headers=as_(secured, VIEWER)).json()
    assert body["status"] == "done"
    assert body["result"]["corpus"] == 1000 and body["result"]["curve"]
    missing = secured.get("/v1/retrieval/benchmark/nope", headers=as_(secured, VIEWER))
    assert missing.status_code == 404


def test_a_viewer_cannot_start_a_benchmark(secured: TestClient) -> None:
    response = secured.post("/v1/retrieval/benchmark?corpus=1000", headers=as_(secured, VIEWER))
    assert response.status_code == 403
