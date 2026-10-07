from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aegis.api.app import Platform, app, get_platform
from aegis.persistence import Database

TENANT = "11111111-1111-4111-8111-111111111111"
CONTEXT = {
    "recipient_email": "candidate@example.com",
    "subject": "Interview invitation",
    "body": "Are you available on Thursday?",
    "attendees": ["interviewer@example.com"],
    "starts_at": "2099-01-01T09:00:00+00:00",
}


@pytest.fixture
def platform() -> Iterator[Platform]:
    database = Database("sqlite+pysqlite:///:memory:")
    instance = Platform(database=database)
    app.dependency_overrides[get_platform] = lambda: instance
    yield instance
    app.dependency_overrides.clear()
    database.dispose()


@pytest.fixture
def as_role(platform: Platform) -> Any:
    def headers(subject: str, *roles: str) -> dict[str, str]:
        token = platform.tokens.mint(TENANT, subject, frozenset(roles))
        return {"Authorization": f"Bearer {token}"}

    return headers


@pytest.fixture
def client(platform: Platform) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def start_run(client: TestClient, headers: dict[str, str]) -> Any:
    return client.post(
        "/v1/runs",
        json={"workflow": "talent_acquisition", "subject_id": "c-1", "context": CONTEXT},
        headers=headers,
    )


def test_a_principal_with_no_role_can_read_nothing(client: TestClient, as_role: Any) -> None:
    nobody = as_role("nobody@example.com")

    assert client.get("/v1/overview", headers=nobody).status_code == 403
    assert client.get("/v1/runs", headers=nobody).status_code == 403
    assert client.get("/v1/ledger", headers=nobody).status_code == 403


def test_a_viewer_reads_but_cannot_change_anything(client: TestClient, as_role: Any) -> None:
    viewer = as_role("viewer@example.com", "VIEWER")

    assert client.get("/v1/overview", headers=viewer).status_code == 200
    assert start_run(client, viewer).status_code == 403
    assert client.post("/v1/attrition/train", json={}, headers=viewer).status_code == 403
    assert client.post("/v1/anonymize", json={}, headers=viewer).status_code == 403


def test_a_viewer_cannot_read_the_ledger_or_export_it(client: TestClient, as_role: Any) -> None:
    viewer = as_role("viewer@example.com", "VIEWER")

    for path in ("/v1/ledger", "/v1/ledger/search", "/v1/ledger/verify", "/v1/ledger/export"):
        assert client.get(path, headers=viewer).status_code == 403, path


def test_an_operator_starts_runs_but_cannot_approve_them(
    client: TestClient, as_role: Any
) -> None:
    operator = as_role("recruiter@example.com", "OPERATOR")
    created = start_run(client, operator)
    assert created.status_code == 201
    run = created.json()

    response = client.post(
        f"/v1/runs/{run['run_id']}/steps/shortlist/approve", json={}, headers=operator
    )

    assert response.status_code == 403


def test_an_approver_signs_off_but_cannot_start_a_run(client: TestClient, as_role: Any) -> None:
    operator = as_role("recruiter@example.com", "OPERATOR")
    approver = as_role("hr.partner@example.com", "APPROVER")
    run = start_run(client, operator).json()

    assert start_run(client, approver).status_code == 403
    approved = client.post(
        f"/v1/runs/{run['run_id']}/steps/shortlist/approve", json={}, headers=approver
    )

    assert approved.status_code == 200
    step = next(item for item in approved.json()["steps"] if item["key"] == "shortlist")
    assert step["approver"] == "hr.partner@example.com"


def test_the_approver_cannot_be_named_by_the_caller(client: TestClient, as_role: Any) -> None:
    operator = as_role("recruiter@example.com", "OPERATOR")
    approver = as_role("hr.partner@example.com", "APPROVER")
    run = start_run(client, operator).json()

    forged = client.post(
        f"/v1/runs/{run['run_id']}/steps/shortlist/approve",
        json={"approver": "chro@example.com"},
        headers=approver,
    )

    assert forged.status_code == 403
    assert "signed-in identity" in forged.json()["detail"]
    own = client.post(
        f"/v1/runs/{run['run_id']}/steps/shortlist/approve",
        json={"approver": "hr.partner@example.com"},
        headers=approver,
    )
    assert own.status_code == 200


def test_the_ledger_records_the_signed_in_identity(client: TestClient, as_role: Any) -> None:
    operator = as_role("recruiter@example.com", "OPERATOR")
    approver = as_role("hr.partner@example.com", "APPROVER")
    auditor = as_role("auditor@example.com", "AUDITOR")
    run = start_run(client, operator).json()
    client.post(f"/v1/runs/{run['run_id']}/steps/shortlist/approve", json={}, headers=approver)

    entries = client.get("/v1/ledger", headers=auditor).json()

    assert "hr.partner@example.com" in {entry["approver"] for entry in entries}
    assert client.get("/v1/ledger/verify", headers=auditor).json()["intact"]


def test_an_auditor_reads_the_ledger_but_cannot_act(client: TestClient, as_role: Any) -> None:
    auditor = as_role("auditor@example.com", "AUDITOR")

    assert client.get("/v1/ledger/export", headers=auditor).status_code == 200
    assert start_run(client, auditor).status_code == 403


def test_role_names_are_matched_without_regard_to_case(client: TestClient, as_role: Any) -> None:
    assert client.get("/v1/overview", headers=as_role("a@example.com", "viewer")).status_code == 200


def test_admin_may_do_everything(client: TestClient, as_role: Any) -> None:
    admin = as_role("admin@example.com", "ADMIN")
    run = start_run(client, admin).json()

    assert client.post(
        f"/v1/runs/{run['run_id']}/steps/shortlist/approve", json={}, headers=admin
    ).status_code == 200
    assert client.get("/v1/ledger/export", headers=admin).status_code == 200
