from __future__ import annotations

from collections.abc import Iterator
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from aegis.api.app import Platform, app, get_platform
from aegis.persistence import Database

TENANT = "66666666-6666-4666-8666-666666666666"
OTHER = "77777777-7777-4777-8777-777777777777"

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
def client(platform: Platform) -> Iterator[TestClient]:
    token = platform.tokens.mint(TENANT, "hr@example.com", frozenset({"ADMIN"}))
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as test_client:
        yield test_client


@pytest.fixture
def other(platform: Platform) -> Iterator[TestClient]:
    token = platform.tokens.mint(OTHER, "hr@else.com", frozenset({"ADMIN"}))
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as test_client:
        yield test_client


def start(
    client: TestClient, subject: str, workflow: str = "talent_acquisition"
) -> dict[str, object]:
    context = CONTEXT if workflow == "talent_acquisition" else {}
    response = client.post(
        "/v1/runs", json={"workflow": workflow, "subject_id": subject, "context": context}
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


class TestRunFilters:
    def test_reports_the_total_and_filters_in_the_query(self, client: TestClient) -> None:
        for index in range(5):
            start(client, f"candidate-{index}")
        start(client, "leaver-1", "offboarding")

        everything = client.get("/v1/runs", params={"limit": 2})
        assert len(everything.json()) == 2
        assert everything.headers["x-total-count"] == "6"

        only = client.get("/v1/runs", params={"workflow": "offboarding"})
        assert [run["subject_id"] for run in only.json()] == ["leaver-1"]
        assert only.headers["x-total-count"] == "1"

        searched = client.get("/v1/runs", params={"q": "CANDIDATE-3"})
        assert [run["subject_id"] for run in searched.json()] == ["candidate-3"]

    def test_needs_approval_finds_runs_held_for_a_person(self, client: TestClient) -> None:
        start(client, "held")
        waiting = client.get("/v1/runs", params={"needs": "approval"})
        assert waiting.headers["x-total-count"] == "1"
        assert client.get("/v1/runs", params={"needs": "failed"}).json() == []

    def test_rejects_an_unknown_filter(self, client: TestClient) -> None:
        assert client.get("/v1/runs", params={"needs": "bogus"}).status_code == 422

    def test_one_tenant_never_sees_anothers_runs(
        self, client: TestClient, other: TestClient
    ) -> None:
        start(client, "mine")
        assert other.get("/v1/runs").json() == []
        assert other.get("/v1/overview").json()["runs"] == 0


class TestLedger:
    def test_search_is_newest_first_filtered_and_counted(self, client: TestClient) -> None:
        run = start(client, "held")
        client.post(
            f"/v1/runs/{run['run_id']}/steps/shortlist/approve", json={}
        )

        page = client.get("/v1/ledger/search", params={"limit": 3})
        sequences = [entry["sequence"] for entry in page.json()]
        assert sequences == sorted(sequences, reverse=True)
        assert int(page.headers["x-total-count"]) > 3

        people = client.get("/v1/ledger/search", params={"actor": "people"}).json()
        assert people and all(entry["approver"] == "hr@example.com" for entry in people)
        assert client.get("/v1/ledger/search", params={"actor": "robots"}).status_code == 422

    def test_export_is_csv_with_hashes_and_neutralises_formulas(
        self, client: TestClient, platform: Platform
    ) -> None:
        run = start(client, "=HYPERLINK(\"http://evil\")")
        # The approver is the signed-in identity, so a hostile-looking one has to come from the
        # token itself, which is the only place it can still come from.
        hostile = platform.tokens.mint(TENANT, "+cmd", frozenset({"ADMIN"}))
        client.post(
            f"/v1/runs/{run['run_id']}/steps/shortlist/approve",
            json={},
            headers={"Authorization": f"Bearer {hostile}"},
        )

        response = client.get("/v1/ledger/export")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "attachment" in response.headers["content-disposition"]

        lines = response.text.splitlines()
        assert lines[0].startswith("sequence,recorded_at")
        assert "entry_hash" in lines[0]
        assert "'=HYPERLINK" in response.text and ",=HYPERLINK" not in response.text
        assert ",'+cmd," in response.text
        assert len(lines) > 3


class TestScreeningAndCompliance:
    RECORD: ClassVar[dict[str, object]] = {
        "name": "Jane Doe",
        "email": "jane@example.com",
        "years_experience": 8,
        "skill_match": 0.9,
    }

    def test_screenings_are_kept_without_the_record(
        self, client: TestClient, other: TestClient
    ) -> None:
        verdict = client.post(
            "/v1/screen", json={"record": self.RECORD, "requirement": "5+ years of Python"}
        ).json()

        stored = client.get("/v1/screenings")
        assert stored.headers["x-total-count"] == "1"
        row = stored.json()[0]
        assert row["subject_key"] == verdict["subject_key"]
        assert row["prompt_fingerprint"] == verdict["prompt_fingerprint"]
        assert "Jane" not in stored.text and "jane@example.com" not in stored.text
        assert other.get("/v1/screenings").json() == []

    def test_a_finding_is_stored_and_written_into_the_chain(self, client: TestClient) -> None:
        report = client.post(
            "/v1/bias/adverse-impact",
            json={
                "label": "Shortlist by age band",
                "outcomes": [
                    {"group": "Under 45", "selected": 60, "total": 100},
                    {"group": "45 and over", "selected": 20, "total": 100},
                ],
            },
        ).json()
        assert report["verdict"] == "ADVERSE_IMPACT"
        assert report["report_id"] and report["ledger_sequence"] is not None

        listed = client.get("/v1/bias/reports").json()
        assert [r["label"] for r in listed] == ["Shortlist by age band"]
        assert client.get(f"/v1/bias/reports/{report['report_id']}").json()["verdict"] == (
            "ADVERSE_IMPACT"
        )

        entry = client.get("/v1/ledger/search", params={"outcome": "FLAGGED"}).json()[0]
        assert entry["sequence"] == report["ledger_sequence"]
        assert client.get("/v1/ledger/verify").json()["intact"] is True

    def test_a_small_sample_is_recorded_as_insufficient_not_as_a_pass(
        self, client: TestClient
    ) -> None:
        report = client.post(
            "/v1/bias/adverse-impact",
            json={
                "outcomes": [
                    {"group": "A", "selected": 4, "total": 10},
                    {"group": "B", "selected": 1, "total": 10},
                ]
            },
        ).json()
        assert report["verdict"] == "INSUFFICIENT_DATA"
        outcome = client.get("/v1/ledger/search", params={"workflow": "compliance"}).json()[0]
        assert outcome["outcome"] == "INSUFFICIENT_DATA"

    def test_reports_cannot_be_read_across_tenants(
        self, client: TestClient, other: TestClient
    ) -> None:
        report = client.post(
            "/v1/bias/adverse-impact",
            json={
                "outcomes": [
                    {"group": "A", "selected": 40, "total": 100},
                    {"group": "B", "selected": 40, "total": 100},
                ]
            },
        ).json()
        assert other.get(f"/v1/bias/reports/{report['report_id']}").status_code == 404
        assert other.get("/v1/bias/reports").json() == []


class TestRetentionRoster:
    def test_scores_are_kept_as_the_latest_per_employee(self, client: TestClient) -> None:
        from aegis.demo.data import build_cohort

        history, current = build_cohort(history=120, current=12)
        client.post(
            "/v1/attrition/train",
            json={
                "employees": [{"subject_key": e.subject_key, **e.features} for e in history],
                "left": [e.left for e in history],
            },
        )
        payload = {"employees": [{"subject_key": e.subject_key, **e.features} for e in current]}
        assert client.post("/v1/attrition/score", json=payload).status_code == 200
        assert client.post("/v1/attrition/score", json=payload).status_code == 200  # rescoring

        roster = client.get("/v1/attrition/scores", params={"limit": 5})
        assert roster.headers["x-total-count"] == "12"  # one row each, not two
        probabilities = [row["probability"] for row in roster.json()]
        assert probabilities == sorted(probabilities, reverse=True)

        high = client.get("/v1/attrition/scores", params={"band": "HIGH"})
        assert all(row["band"] == "HIGH" for row in high.json())
        assert client.get("/v1/attrition/scores", params={"band": "SEVERE"}).status_code == 422
