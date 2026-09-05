from __future__ import annotations

import random
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from aegis.api.app import Platform, app, get_platform
from aegis.auth import generate_api_key, hash_api_key
from aegis.persistence import ApiKeyRepository, Database

TENANT = "33333333-3333-3333-3333-333333333333"
OTHER_TENANT = "44444444-4444-4444-4444-444444444444"

RANDOM = random.Random(20260905)


@pytest.fixture
def platform() -> Iterator[Platform]:
    database = Database("sqlite+pysqlite:///:memory:")
    instance = Platform(database=database)
    app.dependency_overrides[get_platform] = lambda: instance
    yield instance
    app.dependency_overrides.clear()
    database.dispose()


@pytest.fixture
def client(platform: Platform) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth(platform: Platform) -> dict[str, str]:
    token = platform.tokens.mint(TENANT, "hr.partner@example.com", frozenset({"ADMIN"}))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def other_auth(platform: Platform) -> dict[str, str]:
    token = platform.tokens.mint(OTHER_TENANT, "someone@else.com")
    return {"Authorization": f"Bearer {token}"}


CONTEXT = {
    "recipient_email": "candidate@example.com",
    "subject": "Interview invitation",
    "body": "Are you available on Thursday?",
    "attendees": ["interviewer@example.com"],
    "starts_at": "2099-01-01T09:00:00+00:00",
}


def employee(index: int, leaving: bool) -> dict[str, float | int | str]:
    return {
        "subject_key": f"subj_{index}",
        "tenure_years": 3.0,
        "months_since_promotion": RANDOM.uniform(30, 60) if leaving else RANDOM.uniform(1, 12),
        "salary": RANDOM.uniform(70_000, 85_000) if leaving else RANDOM.uniform(100_000, 120_000),
        "band_midpoint": 100_000.0,
        "peer_median_salary": 100_000.0,
        "manager_changes_24m": 3 if leaving else 0,
        "commute_minutes": 30.0,
        "engagement_score": 1.5 if leaving else 4.5,
        "training_hours_12m": 20.0,
        "overtime_hours_monthly": 5.0,
        "internal_applications_12m": 3 if leaving else 0,
    }


def cohort(size: int = 120) -> tuple[list[dict[str, object]], list[bool]]:
    employees, left = [], []
    for index in range(size):
        leaving = index % 2 == 0
        employees.append(employee(index, leaving))
        left.append(leaving)
    return employees, left


class TestAuthentication:
    def test_an_unauthenticated_request_is_refused(self, client: TestClient) -> None:
        response = client.get("/v1/ledger")

        assert response.status_code == 401
        assert "not authentication" in response.json()["detail"]

    def test_a_tenant_header_alone_no_longer_grants_access(self, client: TestClient) -> None:
        response = client.get("/v1/ledger", headers={"X-Tenant-Id": TENANT})

        assert response.status_code == 401

    def test_a_forged_token_is_refused(self, client: TestClient) -> None:
        response = client.get("/v1/ledger", headers={"Authorization": "Bearer made.up.token"})

        assert response.status_code == 401

    def test_a_valid_token_is_accepted(self, client: TestClient, auth: dict[str, str]) -> None:
        assert client.get("/v1/ledger", headers=auth).status_code == 200

    def test_an_api_key_authenticates_a_machine_caller(
        self, client: TestClient, platform: Platform
    ) -> None:
        key = generate_api_key()
        with platform.database.session() as session:
            ApiKeyRepository(session).issue(TENANT, "ci", hash_api_key(key), ["ADMIN"])

        assert client.get("/v1/ledger", headers={"X-Api-Key": key}).status_code == 200

    def test_a_revoked_api_key_stops_working(self, client: TestClient, platform: Platform) -> None:
        key = generate_api_key()
        with platform.database.session() as session:
            repository = ApiKeyRepository(session)
            repository.issue(TENANT, "ci", hash_api_key(key), ["ADMIN"])
            repository.revoke(hash_api_key(key))

        assert client.get("/v1/ledger", headers={"X-Api-Key": key}).status_code == 401

    def test_health_needs_no_authentication(self, client: TestClient) -> None:
        assert client.get("/health").json() == {"status": "ok"}


class TestTenantIsolation:
    def test_one_tenant_cannot_read_anothers_run(
        self, client: TestClient, auth: dict[str, str], other_auth: dict[str, str]
    ) -> None:
        created = client.post(
            "/v1/runs",
            json={
                "workflow": "talent_acquisition",
                "subject_id": "candidate-1",
                "context": CONTEXT,
            },
            headers=auth,
        ).json()

        assert client.get(f"/v1/runs/{created['run_id']}", headers=other_auth).status_code == 404

    def test_one_tenants_ledger_excludes_anothers_entries(
        self, client: TestClient, auth: dict[str, str], other_auth: dict[str, str]
    ) -> None:
        client.post(
            "/v1/runs",
            json={
                "workflow": "talent_acquisition",
                "subject_id": "candidate-1",
                "context": CONTEXT,
            },
            headers=auth,
        )

        assert client.get("/v1/ledger", headers=other_auth).json() == []

    def test_a_model_trained_by_one_tenant_is_invisible_to_another(
        self, client: TestClient, auth: dict[str, str], other_auth: dict[str, str]
    ) -> None:
        employees, left = cohort()
        client.post(
            "/v1/attrition/train", json={"employees": employees, "left": left}, headers=auth
        )

        response = client.post(
            "/v1/attrition/score", json={"employees": [employee(1, False)]}, headers=other_auth
        )

        assert response.status_code == 409


class TestPersistence:
    def test_a_run_survives_a_new_client_connection(
        self, client: TestClient, auth: dict[str, str], platform: Platform
    ) -> None:
        created = client.post(
            "/v1/runs",
            json={
                "workflow": "talent_acquisition",
                "subject_id": "candidate-1",
                "context": CONTEXT,
            },
            headers=auth,
        ).json()

        with TestClient(app) as reconnected:
            response = reconnected.get(f"/v1/runs/{created['run_id']}", headers=auth)

        assert response.status_code == 200
        assert response.json()["subject_id"] == "candidate-1"

    def test_an_approval_persists(self, client: TestClient, auth: dict[str, str]) -> None:
        created = client.post(
            "/v1/runs",
            json={
                "workflow": "talent_acquisition",
                "subject_id": "candidate-1",
                "context": CONTEXT,
            },
            headers=auth,
        ).json()
        step = created["pending_approvals"][0]
        client.post(
            f"/v1/runs/{created['run_id']}/steps/{step}/approve",
            json={"approver": "hr.partner@example.com"},
            headers=auth,
        )

        reloaded = client.get(f"/v1/runs/{created['run_id']}", headers=auth).json()
        approved = next(item for item in reloaded["steps"] if item["key"] == step)

        assert approved["approver"] == "hr.partner@example.com"

    def test_a_trained_model_survives_and_scores_later(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        employees, left = cohort()
        client.post(
            "/v1/attrition/train", json={"employees": employees, "left": left}, headers=auth
        )

        with TestClient(app) as reconnected:
            response = reconnected.post(
                "/v1/attrition/score",
                json={"employees": [employee(999, True)]},
                headers=auth,
            )

        assert response.status_code == 200
        assert response.json()[0]["band"] == "HIGH"

    def test_the_ledger_chain_continues_across_runs(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        for subject in ("candidate-1", "candidate-2", "candidate-3"):
            client.post(
                "/v1/runs",
                json={
                    "workflow": "talent_acquisition",
                    "subject_id": subject,
                    "context": CONTEXT,
                },
                headers=auth,
            )

        report = client.get("/v1/ledger/verify", headers=auth).json()

        assert report["intact"]
        assert report["entries_checked"] >= 3


class TestScreening:
    def test_a_candidate_is_screened_by_the_reasoning_layer(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        response = client.post(
            "/v1/screen",
            json={
                "record": {
                    "national_id": "12345678",
                    "full_name": "Amina Wanjiru",
                    "gender": "female",
                    "years_experience": 7,
                    "skill_match": 0.9,
                },
                "requirement": "senior backend engineer",
            },
            headers=auth,
        )

        body = response.json()
        assert body["recommendation"] in ("ADVANCE", "REVIEW", "HOLD")
        assert 0.0 <= body["score"] <= 1.0
        assert body["subject_key"].startswith("subj_")
        assert body["model"]
        assert body["prompt_fingerprint"]

    def test_screening_requires_authentication(self, client: TestClient) -> None:
        response = client.post(
            "/v1/screen", json={"record": {"id": "1"}, "requirement": "engineer"}
        )

        assert response.status_code == 401

    def test_a_record_without_an_identifier_is_unprocessable(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        response = client.post(
            "/v1/screen",
            json={"record": {"skills": ["python"]}, "requirement": "engineer"},
            headers=auth,
        )

        assert response.status_code == 422


class TestWorkflows:
    def test_the_catalogue_is_discoverable(self, client: TestClient) -> None:
        catalogue = client.get("/v1/workflows").json()

        assert "talent_acquisition" in catalogue
        assert "onboarding" in catalogue
        assert "recipient_email" in catalogue["talent_acquisition"]["required_context"]

    def test_a_run_without_required_context_is_refused_upfront(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        response = client.post(
            "/v1/runs",
            json={"workflow": "talent_acquisition", "subject_id": "candidate-1"},
            headers=auth,
        )

        assert response.status_code == 422
        assert response.json()["code"] == "missing-context"
        assert "recipient_email" in response.json()["detail"]

    def test_starting_a_run_stops_for_approval(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        response = client.post(
            "/v1/runs",
            json={
                "workflow": "talent_acquisition",
                "subject_id": "candidate-1",
                "context": CONTEXT,
            },
            headers=auth,
        )

        assert response.status_code == 201
        assert response.json()["status"] == "BLOCKED"
        assert response.json()["pending_approvals"]

    def test_an_unknown_workflow_is_a_not_found(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        response = client.post(
            "/v1/runs",
            json={"workflow": "nope", "subject_id": "c-1", "context": CONTEXT},
            headers=auth,
        )

        assert response.status_code == 404

    def test_approving_a_step_not_awaiting_approval_is_a_conflict(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        created = client.post(
            "/v1/runs",
            json={
                "workflow": "talent_acquisition",
                "subject_id": "candidate-1",
                "context": CONTEXT,
            },
            headers=auth,
        ).json()

        response = client.post(
            f"/v1/runs/{created['run_id']}/steps/source/approve",
            json={"approver": "hr.partner@example.com"},
            headers=auth,
        )

        assert response.status_code == 409

    def test_rejecting_a_step_records_the_reason(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        created = client.post(
            "/v1/runs",
            json={
                "workflow": "talent_acquisition",
                "subject_id": "candidate-1",
                "context": CONTEXT,
            },
            headers=auth,
        ).json()
        step = created["pending_approvals"][0]

        response = client.post(
            f"/v1/runs/{created['run_id']}/steps/{step}/reject",
            json={"approver": "hr.partner@example.com", "reason": "headcount frozen"},
            headers=auth,
        )

        rejected = next(item for item in response.json()["steps"] if item["key"] == step)
        assert rejected["status"] == "REJECTED"


class TestComplianceEndpoints:
    def test_anonymisation_strips_protected_and_identifying_fields(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        response = client.post(
            "/v1/anonymize",
            json={
                "record": {
                    "national_id": "12345678",
                    "full_name": "Amina Wanjiru",
                    "gender": "female",
                    "years_experience": 7,
                }
            },
            headers=auth,
        )

        body = response.json()
        assert "full_name" not in body["attributes"]
        assert "gender" not in body["attributes"]
        assert body["attributes"]["years_experience"] == 7

    def test_a_disparate_process_is_flagged(self, client: TestClient, auth: dict[str, str]) -> None:
        response = client.post(
            "/v1/bias/adverse-impact",
            json={
                "outcomes": [
                    {"group": "group_a", "selected": 60, "total": 100},
                    {"group": "group_b", "selected": 30, "total": 100},
                ]
            },
            headers=auth,
        )

        assert response.json()["verdict"] == "ADVERSE_IMPACT"

    def test_attrition_training_reports_importance(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        employees, left = cohort()

        response = client.post(
            "/v1/attrition/train", json={"employees": employees, "left": left}, headers=auth
        )

        body = response.json()
        assert body["rows"] == 120
        assert sum(body["feature_importance"].values()) == pytest.approx(1.0, abs=1e-6)

    def test_scoring_before_training_is_a_conflict(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        response = client.post(
            "/v1/attrition/score", json={"employees": [employee(1, False)]}, headers=auth
        )

        assert response.status_code == 409


class TestRetryingAFailedStep:
    def _run_through_to(self, client: TestClient, auth: dict[str, str], slot: str) -> dict:
        context = dict(CONTEXT) | {"starts_at": slot}
        created = client.post(
            "/v1/runs",
            json={
                "workflow": "talent_acquisition",
                "subject_id": f"candidate-{slot}",
                "context": context,
            },
            headers=auth,
        ).json()
        return client.post(
            f"/v1/runs/{created['run_id']}/steps/shortlist/approve",
            json={"approver": "recruiter@example.com"},
            headers=auth,
        ).json()

    def test_a_double_booking_fails_the_second_run(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        slot = "2099-06-01T09:00:00+00:00"
        self._run_through_to(client, auth, slot)
        second = self._run_through_to(client, auth, slot)

        assert second["status"] == "FAILED"
        schedule = next(s for s in second["steps"] if s["key"] == "schedule")
        assert schedule["status"] == "FAILED"

    def test_a_new_slot_recovers_the_failed_run(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        slot = "2099-06-02T09:00:00+00:00"
        self._run_through_to(client, auth, slot)
        second = self._run_through_to(client, auth, slot)

        response = client.post(
            f"/v1/runs/{second['run_id']}/steps/schedule/retry",
            json={
                "actor": "recruiter@example.com",
                "amendments": {"starts_at": "2099-06-03T11:00:00+00:00"},
            },
            headers=auth,
        )

        assert response.status_code == 200
        recovered = response.json()
        schedule = next(s for s in recovered["steps"] if s["key"] == "schedule")
        assert schedule["status"] == "COMPLETED"
        assert recovered["pending_approvals"] == ["offer"]

    def test_the_retry_appears_in_the_audit_trail(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        slot = "2099-06-04T09:00:00+00:00"
        self._run_through_to(client, auth, slot)
        second = self._run_through_to(client, auth, slot)
        client.post(
            f"/v1/runs/{second['run_id']}/steps/schedule/retry",
            json={
                "actor": "recruiter@example.com",
                "amendments": {"starts_at": "2099-06-05T11:00:00+00:00"},
            },
            headers=auth,
        )

        entries = client.get("/v1/ledger", headers=auth).json()
        retried = [e for e in entries if e["outcome"] == "RETRIED"]

        assert len(retried) == 1
        assert retried[0]["approver"] == "recruiter@example.com"
        assert client.get("/v1/ledger/verify", headers=auth).json()["intact"]

    def test_retrying_a_step_that_did_not_fail_is_refused(
        self, client: TestClient, auth: dict[str, str]
    ) -> None:
        created = client.post(
            "/v1/runs",
            json={
                "workflow": "talent_acquisition",
                "subject_id": "candidate-ok",
                "context": CONTEXT,
            },
            headers=auth,
        ).json()

        response = client.post(
            f"/v1/runs/{created['run_id']}/steps/source/retry",
            json={"actor": "recruiter@example.com"},
            headers=auth,
        )

        assert response.status_code == 409
        assert response.json()["code"] == "retry-refused"

    def test_another_tenant_cannot_retry_the_run(
        self, client: TestClient, auth: dict[str, str], other_auth: dict[str, str]
    ) -> None:
        slot = "2099-06-06T09:00:00+00:00"
        self._run_through_to(client, auth, slot)
        second = self._run_through_to(client, auth, slot)

        response = client.post(
            f"/v1/runs/{second['run_id']}/steps/schedule/retry",
            json={"actor": "intruder@example.com"},
            headers=other_auth,
        )

        assert response.status_code == 404
