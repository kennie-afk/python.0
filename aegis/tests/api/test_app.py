from __future__ import annotations

import random
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from aegis.api.app import Platform, app, get_platform

TENANT = "33333333-3333-3333-3333-333333333333"
OTHER_TENANT = "44444444-4444-4444-4444-444444444444"
HEADERS = {"X-Tenant-Id": TENANT}

RANDOM = random.Random(20260905)


@pytest.fixture
def client() -> Iterator[TestClient]:
    platform = Platform()
    app.dependency_overrides[get_platform] = lambda: platform
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


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
    employees = []
    left = []
    for index in range(size):
        leaving = index % 2 == 0
        employees.append(employee(index, leaving))
        left.append(leaving)
    return employees, left


class TestTenancy:
    def test_a_request_without_a_tenant_is_rejected(self, client: TestClient) -> None:
        response = client.get("/v1/ledger")

        assert response.status_code == 401
        assert "X-Tenant-Id" in response.json()["detail"]

    def test_a_malformed_tenant_is_rejected(self, client: TestClient) -> None:
        response = client.get("/v1/ledger", headers={"X-Tenant-Id": "not-a-uuid"})

        assert response.status_code == 400

    def test_one_tenant_cannot_read_another_tenants_run(self, client: TestClient) -> None:
        created = client.post(
            "/v1/runs",
            json={"workflow": "talent_acquisition", "subject_id": "candidate-1"},
            headers=HEADERS,
        ).json()

        response = client.get(
            f"/v1/runs/{created['run_id']}", headers={"X-Tenant-Id": OTHER_TENANT}
        )

        assert response.status_code == 404

    def test_health_needs_no_tenant(self, client: TestClient) -> None:
        assert client.get("/health").json() == {"status": "ok"}


class TestWorkflowRuns:
    def test_the_catalogue_is_discoverable(self, client: TestClient) -> None:
        catalogue = client.get("/v1/workflows").json()

        assert "talent_acquisition" in catalogue
        assert "onboarding" in catalogue

    def test_starting_a_run_advances_it_and_stops_for_approval(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/v1/runs",
            json={"workflow": "talent_acquisition", "subject_id": "candidate-1"},
            headers=HEADERS,
        )

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "BLOCKED"
        assert body["pending_approvals"]

    def test_an_unknown_workflow_is_a_not_found(self, client: TestClient) -> None:
        response = client.post(
            "/v1/runs",
            json={"workflow": "does_not_exist", "subject_id": "candidate-1"},
            headers=HEADERS,
        )

        assert response.status_code == 404

    def test_approving_a_step_moves_the_run_forward(self, client: TestClient) -> None:
        created = client.post(
            "/v1/runs",
            json={"workflow": "talent_acquisition", "subject_id": "candidate-1"},
            headers=HEADERS,
        ).json()
        step = created["pending_approvals"][0]

        response = client.post(
            f"/v1/runs/{created['run_id']}/steps/{step}/approve",
            json={"approver": "hr.partner@example.com"},
            headers=HEADERS,
        )

        assert response.status_code == 200
        approved = next(
            item for item in response.json()["steps"] if item["key"] == step
        )
        assert approved["approver"] == "hr.partner@example.com"

    def test_approving_a_step_that_is_not_waiting_is_a_conflict(
        self, client: TestClient
    ) -> None:
        created = client.post(
            "/v1/runs",
            json={"workflow": "talent_acquisition", "subject_id": "candidate-1"},
            headers=HEADERS,
        ).json()

        response = client.post(
            f"/v1/runs/{created['run_id']}/steps/source/approve",
            json={"approver": "hr.partner@example.com"},
            headers=HEADERS,
        )

        assert response.status_code == 409
        assert response.json()["code"] == "approval-conflict"

    def test_rejecting_a_step_records_the_reason(self, client: TestClient) -> None:
        created = client.post(
            "/v1/runs",
            json={"workflow": "talent_acquisition", "subject_id": "candidate-1"},
            headers=HEADERS,
        ).json()
        step = created["pending_approvals"][0]

        response = client.post(
            f"/v1/runs/{created['run_id']}/steps/{step}/reject",
            json={"approver": "hr.partner@example.com", "reason": "headcount frozen"},
            headers=HEADERS,
        )

        rejected = next(item for item in response.json()["steps"] if item["key"] == step)
        assert rejected["status"] == "REJECTED"
        assert "headcount frozen" in rejected["reasons"][0]


class TestAnonymisation:
    def test_protected_and_identifying_fields_never_come_back(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/v1/anonymize",
            json={
                "record": {
                    "national_id": "12345678",
                    "full_name": "Amina Wanjiru",
                    "gender": "female",
                    "university": "University of Nairobi",
                    "years_experience": 7,
                }
            },
            headers=HEADERS,
        )

        body = response.json()
        assert "full_name" not in body["attributes"]
        assert "gender" not in body["attributes"]
        assert body["attributes"]["years_experience"] == 7
        assert body["subject_key"].startswith("subj_")
        assert "gender" in body["dropped"]

    def test_a_record_without_an_identifier_is_unprocessable(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/v1/anonymize", json={"record": {"skills": ["python"]}}, headers=HEADERS
        )

        assert response.status_code == 422


class TestAdverseImpact:
    def test_a_disparate_process_is_flagged(self, client: TestClient) -> None:
        response = client.post(
            "/v1/bias/adverse-impact",
            json={
                "outcomes": [
                    {"group": "group_a", "selected": 60, "total": 100},
                    {"group": "group_b", "selected": 30, "total": 100},
                ]
            },
            headers=HEADERS,
        )

        body = response.json()
        assert body["verdict"] == "ADVERSE_IMPACT"
        assert body["groups"][1]["impact_ratio"] == pytest.approx(0.5)
        assert "group_b" in body["summary"]

    def test_an_even_process_passes(self, client: TestClient) -> None:
        response = client.post(
            "/v1/bias/adverse-impact",
            json={
                "outcomes": [
                    {"group": "group_a", "selected": 50, "total": 100},
                    {"group": "group_b", "selected": 48, "total": 100},
                ]
            },
            headers=HEADERS,
        )

        assert response.json()["verdict"] == "NO_ADVERSE_IMPACT"

    def test_a_single_group_is_rejected_by_validation(self, client: TestClient) -> None:
        response = client.post(
            "/v1/bias/adverse-impact",
            json={"outcomes": [{"group": "group_a", "selected": 10, "total": 100}]},
            headers=HEADERS,
        )

        assert response.status_code == 422


class TestAttrition:
    def test_scoring_before_training_is_a_conflict(self, client: TestClient) -> None:
        response = client.post(
            "/v1/attrition/score",
            json={"employees": [employee(1, False)]},
            headers=HEADERS,
        )

        assert response.status_code == 409

    def test_a_model_trains_and_reports_importance(self, client: TestClient) -> None:
        employees, left = cohort()

        response = client.post(
            "/v1/attrition/train",
            json={"employees": employees, "left": left},
            headers=HEADERS,
        )

        body = response.json()
        assert body["rows"] == 120
        assert body["positive_rate"] == pytest.approx(0.5)
        assert sum(body["feature_importance"].values()) == pytest.approx(1.0, abs=1e-6)

    def test_a_trained_model_scores_and_explains(self, client: TestClient) -> None:
        employees, left = cohort()
        client.post(
            "/v1/attrition/train",
            json={"employees": employees, "left": left},
            headers=HEADERS,
        )

        response = client.post(
            "/v1/attrition/score",
            json={"employees": [employee(999, True), employee(998, False)]},
            headers=HEADERS,
        )

        scores = response.json()
        assert scores[0]["band"] == "HIGH"
        assert scores[0]["needs_intervention"]
        assert scores[0]["drivers"]
        assert scores[1]["band"] == "LOW"

    def test_a_model_trained_by_one_tenant_is_not_visible_to_another(
        self, client: TestClient
    ) -> None:
        employees, left = cohort()
        client.post(
            "/v1/attrition/train",
            json={"employees": employees, "left": left},
            headers=HEADERS,
        )

        response = client.post(
            "/v1/attrition/score",
            json={"employees": [employee(1, False)]},
            headers={"X-Tenant-Id": OTHER_TENANT},
        )

        assert response.status_code == 409

    def test_too_few_rows_is_rejected_by_validation(self, client: TestClient) -> None:
        response = client.post(
            "/v1/attrition/train",
            json={"employees": [employee(1, True)], "left": [True]},
            headers=HEADERS,
        )

        assert response.status_code == 422


class TestLedger:
    def test_every_run_writes_an_auditable_trail(self, client: TestClient) -> None:
        client.post(
            "/v1/runs",
            json={"workflow": "talent_acquisition", "subject_id": "candidate-1"},
            headers=HEADERS,
        )

        entries = client.get("/v1/ledger", headers=HEADERS).json()

        assert entries
        assert entries[0]["workflow"] == "talent_acquisition"

    def test_the_ledger_verifies_as_intact(self, client: TestClient) -> None:
        client.post(
            "/v1/runs",
            json={"workflow": "onboarding", "subject_id": "employee-1"},
            headers=HEADERS,
        )

        report = client.get("/v1/ledger/verify", headers=HEADERS).json()

        assert report["intact"]
        assert report["entries_checked"] > 0

    def test_an_approval_is_attributed_in_the_ledger(self, client: TestClient) -> None:
        created = client.post(
            "/v1/runs",
            json={"workflow": "talent_acquisition", "subject_id": "candidate-1"},
            headers=HEADERS,
        ).json()
        step = created["pending_approvals"][0]
        client.post(
            f"/v1/runs/{created['run_id']}/steps/{step}/approve",
            json={"approver": "hr.partner@example.com"},
            headers=HEADERS,
        )

        entries = client.get("/v1/ledger", headers=HEADERS).json()

        assert any(entry["approver"] == "hr.partner@example.com" for entry in entries)

    def test_one_tenants_ledger_does_not_contain_anothers_entries(
        self, client: TestClient
    ) -> None:
        client.post(
            "/v1/runs",
            json={"workflow": "talent_acquisition", "subject_id": "candidate-1"},
            headers=HEADERS,
        )

        assert client.get("/v1/ledger", headers={"X-Tenant-Id": OTHER_TENANT}).json() == []
