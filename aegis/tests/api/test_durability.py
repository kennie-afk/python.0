"""Interview slots survive a restart, the platform is built once, migrations run elsewhere."""

from __future__ import annotations

import importlib
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from aegis.api.app import Platform, app, build_platform
from aegis.integrations.calendar import CalendarError, CalendarTool, PersistentCalendar, Slot
from aegis.persistence import Database
from conftest import OTHER, TENANT, Gov

app_module = importlib.import_module("aegis.api.app")

ROOT = Path(__file__).resolve().parents[2]


def future(hours: int = 48) -> datetime:
    return (datetime.now(UTC) + timedelta(hours=hours)).replace(microsecond=0)


def book(
    database: Database, tenant: str, attendees: list[str], start: datetime, minutes: int = 45
) -> str:
    with database.session(tenant) as session:
        return PersistentCalendar(session, tenant).book(attendees, Slot(start, minutes))


def test_a_booking_survives_a_restart(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path}/aegis.db"
    start = future()
    first = Database(url)
    Platform(database=first)  # creates the schema
    book(first, TENANT, ["ana@example.com"], start)
    first.dispose()

    restarted = Database(url)
    Platform(database=restarted)
    with pytest.raises(CalendarError, match="already booked"):
        book(restarted, TENANT, ["ana@example.com"], start + timedelta(minutes=15))
    with restarted.session(TENANT) as session:
        assert PersistentCalendar(session, TENANT).availability("ana@example.com") == [
            Slot(start, 45)
        ]
    restarted.dispose()


def test_adjacent_slots_are_allowed_and_overlaps_are_not(gov: Gov) -> None:
    start = future()
    book(gov.platform.database, TENANT, ["a@example.com"], start, 30)
    book(gov.platform.database, TENANT, ["a@example.com"], start + timedelta(minutes=30), 30)
    with pytest.raises(CalendarError):
        book(
            gov.platform.database,
            TENANT,
            ["a@example.com", "b@example.com"],
            start + timedelta(minutes=10),
            30,
        )
    with gov.platform.database.session(TENANT) as session:
        # the refused booking left nothing behind, for either attendee
        assert PersistentCalendar(session, TENANT).availability("b@example.com") == []


def test_two_tenants_can_book_the_same_attendee_at_the_same_time(gov: Gov) -> None:
    start = future()
    book(gov.platform.database, TENANT, ["shared.name@example.com"], start)
    book(gov.platform.database, OTHER, ["shared.name@example.com"], start)  # not a conflict

    with gov.platform.database.session(OTHER) as session:
        assert len(PersistentCalendar(session, OTHER).availability("shared.name@example.com")) == 1


def test_the_calendar_tool_books_through_the_database_in_a_real_run(gov: Gov) -> None:
    context = {
        "recipient_email": "candidate@example.com",
        "subject": "Interview",
        "body": "Thursday?",
        "attendees": ["interviewer@example.com"],
        "starts_at": future().isoformat(),
    }
    headers = gov.headers("OPERATOR", "APPROVER", subject="hr@example.com")

    def run(subject: str) -> dict[str, Any]:
        started = gov.client.post(
            "/v1/runs",
            json={"workflow": "talent_acquisition", "subject_id": subject, "context": context},
            headers=headers,
        ).json()
        body = started
        for _ in range(3):  # approve whatever is waiting until it stops or reaches the offer
            for step in body["pending_approvals"]:
                if step == "offer":
                    return dict(body)
                body = gov.client.post(
                    f"/v1/runs/{body['run_id']}/steps/{step}/approve", json={}, headers=headers
                ).json()
        return dict(body)

    first = run("cand-1")
    second = run("cand-2")  # same interviewer, same slot

    steps = {s["key"]: s for s in first["steps"]}
    assert steps["schedule"]["status"] == "COMPLETED"
    clash = {s["key"]: s for s in second["steps"]}["schedule"]
    assert clash["status"] != "COMPLETED" and any("already booked" in r for r in clash["reasons"])
    with gov.platform.database.session(TENANT) as session:
        assert len(PersistentCalendar(session, TENANT).availability("interviewer@example.com")) == 1


def test_the_tool_still_refuses_the_past_and_empty_attendees(gov: Gov) -> None:
    with gov.platform.database.session(TENANT) as session:
        tool = CalendarTool(PersistentCalendar(session, TENANT))
        from aegis.governance.actions import ActionType, ProposedAction

        def execute(payload: dict[str, Any]) -> Any:
            return tool.execute(
                ProposedAction(
                    action_type=ActionType.SCHEDULE_INTERVIEW,
                    subject_id="s",
                    tenant_id=TENANT,
                    agent="test",
                    rationale="test",
                    confidence=0.9,
                    payload=payload,
                )
            )

        assert not execute(
            {"attendees": ["a@example.com"], "starts_at": "2001-01-01T09:00:00+00:00"}
        ).succeeded
        assert not execute({"attendees": [], "starts_at": future().isoformat()}).succeeded


# ---- the platform is built once, at start-up -----------------------------------------------------
def test_concurrent_first_requests_build_one_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[int] = []

    class Slow:
        def __init__(self) -> None:
            time.sleep(0.05)  # long enough for every thread to arrive before the first finishes
            built.append(1)

    monkeypatch.setattr(app_module, "_platform", None)
    monkeypatch.setattr(app_module, "Platform", Slow)
    results: list[object] = []
    gate = threading.Barrier(12)

    def go() -> None:
        gate.wait()
        results.append(build_platform())

    threads = [threading.Thread(target=go) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(built) == 1 and len({id(r) for r in results}) == 1


def test_the_platform_is_built_when_the_app_starts_not_on_the_first_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(app_module, "_platform", None)
    monkeypatch.setenv("AEGIS_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/boot.db")
    app.dependency_overrides.clear()

    with TestClient(app):
        assert app_module._platform is not None  # before any request


def test_a_missing_secret_stops_the_app_at_start_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(app_module, "_platform", None)
    monkeypatch.setenv("AEGIS_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/boot2.db")
    monkeypatch.delenv("AEGIS_JWT_SECRET")
    app.dependency_overrides.clear()

    with pytest.raises(RuntimeError, match="AEGIS_JWT_SECRET"), TestClient(app):
        pass


# ---- migrations are a Job / one-shot service, not every replica's start-up ----
def test_the_image_migrates_only_when_told_to_and_the_deployments_say_no() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert 'ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]' in dockerfile
    assert "alembic upgrade head && uvicorn" not in dockerfile
    assert "ENV AEGIS_RUN_MIGRATIONS=true" in dockerfile  # the safe single-container default
    entrypoint = (ROOT / "scripts/docker-entrypoint.sh").read_text()
    assert "AEGIS_RUN_MIGRATIONS:-true" in entrypoint and 'exec "$@"' in entrypoint

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    assert compose["migrate"]["command"] == ["alembic", "upgrade", "head"]
    assert compose["migrate"]["restart"] == "no"
    assert compose["api"]["environment"]["AEGIS_RUN_MIGRATIONS"] == "false"
    assert compose["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"

    api = next(
        d
        for d in yaml.safe_load_all((ROOT / "k8s/20-api.yaml").read_text())
        if d and d["kind"] == "Deployment"
    )
    env = {
        e["name"]: e.get("value") for e in api["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["AEGIS_RUN_MIGRATIONS"] == "false"
    job = yaml.safe_load((ROOT / "k8s/21-migrate-job.yaml").read_text())
    assert job["kind"] == "Job" and job["spec"]["template"]["spec"]["containers"][0]["command"] == [
        "alembic",
        "upgrade",
        "head",
    ]
    assert "21-migrate-job.yaml" in (ROOT / "k8s/kustomization.yaml").read_text()


def test_the_kubernetes_manifests_pass_the_static_checks() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check-k8s.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
