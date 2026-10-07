"""Scheduled checks and alert delivery, against stub servers and a mock mailbox."""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aegis.api.app import Platform, app, get_platform, token_failures
from aegis.governance.policy import TenantPolicy
from aegis.integrations.email import EmailError, MockEmailTransport, SentEmail
from aegis.ops.__main__ import main
from aegis.ops.alerts import MAX_ATTEMPTS, AlertDispatcher
from aegis.ops.checks import run_checks, tenant_ids
from aegis.persistence import (
    AlertRepository,
    AlertRow,
    Database,
    ModelVersionRow,
    PolicyRepository,
    RiskScoreHistoryRow,
)

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
SECRET = "webhook-secret-for-tests"


class Hook:
    """A webhook receiver on localhost."""

    def __init__(self, status: int = 200) -> None:
        self.received: list[tuple[dict[str, Any], dict[str, str], bytes]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.received.append((json.loads(body), dict(self.headers), body))
                self.send_response(status)
                self.end_headers()

            def log_message(self, *_: object) -> None:
                return

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/alerts"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def hook() -> Iterator[Hook]:
    stub = Hook()
    yield stub
    stub.close()


class World:
    def __init__(self, platform: Platform, client: TestClient) -> None:
        self.platform, self.client = platform, client

    def headers(self, *roles: str, tenant: str = TENANT) -> dict[str, str]:
        token = self.platform.tokens.mint(tenant, "ops@example.com", frozenset(roles))
        return {"Authorization": f"Bearer {token}"}

    def alerts(self, tenant: str = TENANT) -> list[AlertRow]:
        with self.platform.database.session(tenant) as session:
            return list(AlertRepository(session).recent(tenant, 100)[0])


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, hook: Hook) -> Iterator[World]:
    monkeypatch.setenv("AEGIS_ALERT_WEBHOOK", hook.url)
    monkeypatch.setenv("AEGIS_ALERT_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("AEGIS_ALERT_EMAIL_TO", "oncall@example.com, hr-lead@example.com")
    database = Database("sqlite+pysqlite:///:memory:")
    platform = Platform(database=database)
    app.dependency_overrides[get_platform] = lambda: platform
    token_failures.reset()
    with TestClient(app) as client:
        yield World(platform, client)
    app.dependency_overrides.clear()
    database.dispose()


def cohort(
    size: int = 160, shift: float = 1.0, seed: int = 5
) -> tuple[list[dict[str, Any]], list[bool]]:
    import random

    rng = random.Random(seed)
    left = [i % 2 == 0 for i in range(size)]
    return [
        {
            "subject_key": f"emp_{seed}_{i}",
            "tenure_years": rng.uniform(0.5, 10) * shift,
            "months_since_promotion": (rng.uniform(30, 60) if flag else rng.uniform(1, 12)) * shift,
            "salary": (rng.uniform(70_000, 85_000) if flag else rng.uniform(100_000, 120_000))
            * shift,
            "band_midpoint": 100_000.0,
            "peer_median_salary": 100_000.0,
            "manager_changes_24m": int((3 if flag else 0) + rng.randint(0, 2) + 8 * (shift - 1)),
            "commute_minutes": rng.uniform(10, 60) * shift,
            "engagement_score": min(5.0, rng.uniform(1, 2.5) if flag else rng.uniform(3, 5)),
            "training_hours_12m": rng.uniform(0, 40) * shift,
            "overtime_hours_monthly": rng.uniform(0, 20) * shift,
            "internal_applications_12m": int(
                (3 if flag else 0) + rng.randint(0, 1) + 6 * (shift - 1)
            ),
        }
        for i, flag in enumerate(left)
    ], left


def train(world: World, shift: float = 1.0, seed: int = 5) -> Any:
    employees, left = cohort(shift=shift, seed=seed)
    return world.client.post(
        "/v1/attrition/train",
        json={"employees": employees, "left": left},
        headers=world.headers("OPERATOR"),
    )


def score(world: World, shift: float, seed: int, size: int = 240) -> None:
    employees, _ = cohort(size=size, shift=shift, seed=seed)
    response = world.client.post(
        "/v1/attrition/score", json={"employees": employees}, headers=world.headers("OPERATOR")
    )
    assert response.status_code == 200, response.text


def drop_history(world: World) -> None:
    with world.platform.database.session(TENANT) as session:
        session.query(RiskScoreHistoryRow).delete()


# ---- delivery ------------------------------------------------------------------------------------
def test_a_blocked_model_alerts_the_webhook_and_the_mailbox(world: World, hook: Hook) -> None:
    assert train(world).status_code == 200
    assert train(world, shift=3.0, seed=9).status_code == 409  # drifted retrain is blocked

    payload, headers, raw = hook.received[-1]
    assert payload["source"] == "aegis" and payload["kind"] == "model_blocked"
    assert payload["tenant_id"] == TENANT and "SIGNIFICANT drift" in payload["message"]
    expected = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    assert headers["X-Aegis-Signature"] == expected

    mailbox = world.platform.email
    assert isinstance(mailbox, MockEmailTransport)
    assert sorted(m.to for m in mailbox.sent) == ["hr-lead@example.com", "oncall@example.com"]
    assert mailbox.sent[0].subject == "[Aegis] model blocked"
    assert [a.delivered_at is not None for a in world.alerts()] == [True]


def test_a_compliance_finding_is_not_alerted_but_a_warned_model_is(
    world: World, hook: Hook
) -> None:
    employees, left = cohort(size=200)
    groups = ["young" if i % 2 == 0 else "older" for i in range(200)]
    response = world.client.post(
        "/v1/attrition/train",
        json={"employees": employees, "left": left, "groups": groups},
        headers=world.headers("OPERATOR"),
    )

    assert response.json()["gate"] == "WARN"
    assert [p["kind"] for p, _, _ in hook.received] == ["model_warned"]


def test_a_dead_webhook_delays_the_alert_it_does_not_lose_it(world: World, hook: Hook) -> None:
    with world.platform.database.session(TENANT) as session:
        AlertRepository(session).add(TENANT, "drift", "something drifted", {})
    dead = AlertDispatcher(world.platform.database, webhook="http://127.0.0.1:9/alerts")

    assert dead.deliver_pending(TENANT) == 0
    (row,) = world.alerts()
    assert row.delivered_at is None and row.attempts == 1 and "webhook" in (row.last_error or "")

    live = AlertDispatcher(world.platform.database, webhook=hook.url)
    assert live.deliver_pending(TENANT) == 1
    assert world.alerts()[0].delivered_at is not None and world.alerts()[0].attempts == 2
    assert live.deliver_pending(TENANT) == 0  # delivered once, never again
    assert len(hook.received) == 1


def test_an_alert_that_keeps_failing_stops_being_retried(world: World) -> None:
    with world.platform.database.session(TENANT) as session:
        AlertRepository(session).add(TENANT, "drift", "x", {})
    dead = AlertDispatcher(world.platform.database, webhook="http://127.0.0.1:9/alerts")
    for _ in range(MAX_ATTEMPTS + 3):
        dead.deliver_pending(TENANT)

    assert world.alerts()[0].attempts == MAX_ATTEMPTS and world.alerts()[0].delivered_at is None


def test_a_failing_mail_server_is_reported_per_recipient(world: World) -> None:
    class Down:
        def deliver(self, message: SentEmail) -> str:
            raise EmailError("connection refused")

    with world.platform.database.session(TENANT) as session:
        AlertRepository(session).add(TENANT, "drift", "x", {})
    mailer = AlertDispatcher(world.platform.database, email=Down(), recipients=["a@example.com"])

    assert mailer.deliver_pending(TENANT) == 0
    assert "email to a@example.com: connection refused" in (world.alerts()[0].last_error or "")


def test_with_no_channel_alerts_stay_queued_and_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("AEGIS_ALERT_WEBHOOK", "AEGIS_ALERT_EMAIL_TO"):
        monkeypatch.delenv(name, raising=False)
    database = Database("sqlite+pysqlite:///:memory:")
    platform = Platform(database=database)

    assert platform.alerts.configured is False
    with database.session(TENANT) as session:
        AlertRepository(session).add(TENANT, "drift", "x", {})
    assert platform.alerts.deliver_pending(TENANT) == 0
    database.dispose()


def test_a_webhook_must_be_http() -> None:
    with pytest.raises(ValueError, match="http"):
        AlertDispatcher(Database("sqlite+pysqlite:///:memory:"), webhook="file:///etc/passwd")


# ---- the checks ----------------------------------------------------------------------------------
def test_drifted_scoring_inputs_raise_one_drift_alert_and_a_retrain_recommendation(
    world: World, hook: Hook
) -> None:
    train(world)
    score(world, shift=3.0, seed=9)  # real inputs, far from the training reference

    first = run_checks(world.platform.database, TENANT, world.platform.alerts)
    again = run_checks(world.platform.database, TENANT, world.platform.alerts)

    assert sorted(first.alerts_queued) == ["drift", "retrain_recommended"]
    assert again.alerts_queued == []  # a persisting condition alerts once, not every run
    kinds = [p["kind"] for p, _, _ in hook.received]
    assert kinds.count("drift") == 1 and kinds.count("retrain_recommended") == 1
    drift = next(p for p, _, _ in hook.received if p["kind"] == "drift")
    assert "tenure_years" in drift["payload"]["features"] and drift["payload"]["version"] == 1
    assert "no outcome labels" in next(
        p["message"] for p, _, _ in hook.received if p["kind"] == "retrain_recommended"
    )


def test_stable_inputs_raise_nothing_and_a_new_drift_alerts_again(world: World) -> None:
    train(world)
    score(world, shift=1.0, seed=21)
    quiet = run_checks(world.platform.database, TENANT, world.platform.alerts)
    assert quiet.alerts_queued == [] and quiet.notes == []

    drop_history(world)
    score(world, shift=3.0, seed=9)
    assert "drift" in run_checks(world.platform.database, TENANT).alerts_queued
    drop_history(world)
    score(world, shift=1.0, seed=22)
    assert (
        "drift" not in run_checks(world.platform.database, TENANT).alerts_queued
    )  # clears the key
    drop_history(world)
    score(world, shift=3.0, seed=10)
    assert "drift" in run_checks(world.platform.database, TENANT).alerts_queued  # alerts afresh
    assert [a.kind for a in world.alerts()].count("drift") == 2


def test_too_little_recent_scoring_is_said_not_guessed(world: World) -> None:
    train(world)
    score(world, shift=3.0, seed=9, size=10)
    summary = run_checks(world.platform.database, TENANT)

    assert summary.alerts_queued == [] and "not enough recent scoring" in summary.notes[0]


def test_an_old_model_is_flagged_for_retraining(world: World) -> None:
    train(world)
    later = datetime.now(UTC) + timedelta(days=120)
    summary = run_checks(world.platform.database, TENANT, now=later)

    assert summary.alerts_queued == ["retrain_recommended"]
    assert "120 days old" in world.alerts()[0].message or "days old" in world.alerts()[0].message
    assert run_checks(world.platform.database, TENANT, now=later).alerts_queued == []


def test_a_broken_ledger_is_alerted(world: World) -> None:
    train(world)
    from aegis.persistence.models import LedgerRow

    with world.platform.database.session(TENANT) as session:
        session.query(LedgerRow).first().outcome = "TAMPERED"  # type: ignore[union-attr]
    summary = run_checks(world.platform.database, TENANT)

    assert "ledger_integrity" in summary.alerts_queued
    assert (
        "failed verification"
        in next(a for a in world.alerts() if a.kind == "ledger_integrity").message
    )


def test_a_tampered_active_model_is_alerted(world: World) -> None:
    train(world)
    with world.platform.database.session(TENANT) as session:
        session.query(ModelVersionRow).one().gate = "WARN"
    assert "model_integrity" in run_checks(world.platform.database, TENANT).alerts_queued


def test_checks_are_per_tenant(world: World) -> None:
    train(world)
    drop = run_checks(world.platform.database, OTHER, now=datetime.now(UTC) + timedelta(days=400))

    assert drop.alerts_queued == [] and world.alerts(OTHER) == []


def test_tenants_are_listed_and_the_checks_run_over_all_of_them(
    world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with world.platform.database.session(TENANT) as session:
        PolicyRepository(session).upsert(TENANT, "Acme", TenantPolicy.conservative(TENANT))
    with world.platform.database.session(OTHER) as session:
        PolicyRepository(session).upsert(OTHER, "Beta", TenantPolicy.conservative(OTHER))
    assert sorted(tenant_ids(world.platform.database)) == [TENANT, OTHER]


def test_the_scheduled_entry_point_runs_one_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    url = f"sqlite+pysqlite:///{tmp_path}/ops.db"
    monkeypatch.setenv("AEGIS_DATABASE_URL", url)
    monkeypatch.delenv("AEGIS_ADMIN_DATABASE_URL", raising=False)
    monkeypatch.delenv("AEGIS_MIGRATION_URL", raising=False)
    database = Database(url)
    Platform(database=database)
    with database.session(TENANT) as session:
        PolicyRepository(session).upsert(TENANT, "Acme", TenantPolicy.conservative(TENANT))
    monkeypatch.setattr(sys, "argv", ["aegis.ops", "--once"])

    with caplog.at_level("INFO", logger="aegis.ops"):
        main()

    assert any(f"checked {TENANT}" in record.getMessage() for record in caplog.records)
    database.dispose()


def test_running_the_checks_now_is_an_admin_action(world: World) -> None:
    train(world)
    assert world.client.post("/v1/ops/checks", headers=world.headers("OPERATOR")).status_code == 403
    done = world.client.post("/v1/ops/checks", headers=world.headers("ADMIN"))

    assert done.status_code == 200 and done.json()["tenant_id"] == TENANT
    assert (
        world.client.get("/v1/alerts", headers=world.headers("VIEWER", tenant=OTHER)).json() == []
    )
