"""The governance gate on attrition models: a bad model is blocked and cannot serve."""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

import pytest

from aegis.attrition.model import AttritionModel
from aegis.persistence import ModelVersionRow
from conftest import OTHER, TENANT, Gov  # pytest puts tests/api on the path


def train(gov: Gov, employees: list[dict[str, Any]], left: list[bool], **extra: Any) -> Any:
    return gov.client.post(
        "/v1/attrition/train",
        json={"employees": employees, "left": left, **extra},
        headers=gov.headers("OPERATOR"),
    )


def test_a_clean_first_model_passes_and_becomes_active(
    gov: Gov, cohort: Callable[..., Any]
) -> None:
    employees, left = cohort()
    response = train(gov, employees, left)

    assert response.status_code == 200
    body = response.json()
    assert body["gate"] == "PASS" and body["active"] is True and body["version"] == 1
    assert {case["case"].split(":")[0] for case in body["fidelity"]["determinism"]} == {
        "score",
        "training_reproducibility",
    }
    status = gov.client.get("/v1/attrition/model", headers=gov.headers("VIEWER")).json()
    assert status["version"] == 1 and status["gate"] == "PASS"


def test_a_non_deterministic_model_is_blocked_and_cannot_be_activated(
    gov: Gov, cohort: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    employees, left = cohort()
    assert train(gov, employees, left).status_code == 200  # a good v1 is serving

    original = AttritionModel.score
    counter = {"n": 0}

    def noisy(self: AttritionModel, snapshot: Any) -> Any:
        counter["n"] += 1
        result = original(self, snapshot)
        object.__setattr__(result, "probability", (result.probability + counter["n"] * 0.0137) % 1)
        return result

    monkeypatch.setattr(AttritionModel, "score", noisy)
    blocked = train(gov, employees, left)
    monkeypatch.undo()

    assert blocked.status_code == 409
    body = blocked.json()
    assert body["code"] == "model-blocked" and body["gate"] == "BLOCK"
    assert any("NON_DETERMINISTIC" in reason for reason in body["reasons"])

    models = gov.client.get("/v1/attrition/models", headers=gov.headers("VIEWER")).json()
    by_version = {m["version"]: m for m in models}
    assert by_version[2]["gate"] == "BLOCK" and by_version[2]["active"] is False
    assert by_version[1]["active"] is True  # the good one keeps serving

    refused = gov.client.post("/v1/attrition/models/2/activate", headers=gov.headers("APPROVER"))
    assert refused.status_code == 409 and "blocked" in refused.json()["detail"]


def test_a_drifted_retrain_is_blocked_against_the_active_reference(
    gov: Gov, cohort: Callable[..., Any]
) -> None:
    employees, left = cohort()
    assert train(gov, employees, left).status_code == 200

    shifted, shifted_left = cohort(shift=3.0, seed=9)
    blocked = train(gov, shifted, shifted_left)

    assert blocked.status_code == 409
    assert blocked.json()["fidelity"]["reference_version"] == 1
    assert any("SIGNIFICANT drift" in reason for reason in blocked.json()["reasons"])
    active = gov.client.get("/v1/attrition/model", headers=gov.headers("VIEWER")).json()
    assert active["version"] == 1


def test_a_stable_retrain_activates_the_new_version_and_keeps_the_old_one(
    gov: Gov, cohort: Callable[..., Any]
) -> None:
    employees, left = cohort()
    train(gov, employees, left)
    second_employees, second_left = cohort(seed=11)
    response = train(gov, second_employees, second_left)

    assert response.status_code == 200 and response.json()["version"] == 2
    models = {
        m["version"]: m
        for m in gov.client.get("/v1/attrition/models", headers=gov.headers("VIEWER")).json()
    }
    assert models[2]["active"] and not models[1]["active"]


def test_rollback_returns_to_the_previous_version_and_is_audited(
    gov: Gov, cohort: Callable[..., Any]
) -> None:
    train(gov, *cohort())
    train(gov, *cohort(seed=11))

    rolled = gov.client.post(
        "/v1/attrition/models/rollback", headers=gov.headers("APPROVER", subject="ana@example.com")
    )
    assert rolled.status_code == 200 and rolled.json()["version"] == 1 and rolled.json()["active"]

    ledger = gov.client.get(
        "/v1/ledger/search?workflow=model_governance", headers=gov.headers("AUDITOR")
    ).json()
    actions = [(e["step"], e["outcome"], e["approver"]) for e in ledger]
    assert ("rollback", "ROLLED_BACK", "ana@example.com") in actions
    assert ("train", "PASSED", None) in actions

    nothing_earlier = gov.client.post(
        "/v1/attrition/models/rollback", headers=gov.headers("APPROVER")
    )
    assert nothing_earlier.status_code == 409


def test_activation_needs_an_approver_not_an_operator(gov: Gov, cohort: Callable[..., Any]) -> None:
    train(gov, *cohort())
    assert (
        gov.client.post(
            "/v1/attrition/models/1/activate", headers=gov.headers("OPERATOR")
        ).status_code
        == 403
    )
    assert (
        gov.client.post(
            "/v1/attrition/models/9/activate", headers=gov.headers("APPROVER")
        ).status_code
        == 404
    )


def test_scoring_reports_the_model_version_and_gate(gov: Gov, cohort: Callable[..., Any]) -> None:
    employees, left = cohort()
    train(gov, employees, left)
    response = gov.client.post(
        "/v1/attrition/score", json={"employees": employees[:12]}, headers=gov.headers("OPERATOR")
    )
    assert response.status_code == 200
    assert response.headers["x-aegis-model-version"] == "1"
    assert response.headers["x-aegis-gate"] == "PASS"

    fresh, _ = cohort(size=240, seed=21)  # same population, enough rows to be judged
    steady = gov.client.post(
        "/v1/attrition/score", json={"employees": fresh}, headers=gov.headers("OPERATOR")
    )
    assert "x-aegis-drift" not in steady.headers


def test_scoring_a_drifted_batch_says_so_without_refusing_it(
    gov: Gov, cohort: Callable[..., Any]
) -> None:
    train(gov, *cohort())
    drifted, _ = cohort(size=240, shift=3.0, seed=9)
    response = gov.client.post(
        "/v1/attrition/score", json={"employees": drifted}, headers=gov.headers("OPERATOR")
    )

    assert response.status_code == 200
    assert response.headers["x-aegis-drift"].startswith("SIGNIFICANT: ")


def test_scoring_is_refused_when_a_stored_gate_is_edited(
    gov: Gov, cohort: Callable[..., Any]
) -> None:
    employees, left = cohort()
    train(gov, employees, left)
    with gov.platform.database.session(TENANT) as session:
        session.query(ModelVersionRow).one().gate = "WARN"  # someone edits the row directly
    response = gov.client.post(
        "/v1/attrition/score", json={"employees": employees[:3]}, headers=gov.headers("OPERATOR")
    )

    assert response.status_code == 409  # the gate is covered by the signature


def test_adverse_impact_is_tested_when_groups_are_supplied(
    gov: Gov, cohort: Callable[..., Any]
) -> None:
    employees, left = cohort(size=200)
    # Flagged-high-risk rate is driven by leaving; make one group almost all leavers.
    groups = ["under_45" if index % 2 == 0 else "45_plus" for index in range(200)]
    response = train(gov, employees, left, groups=groups, minimum_group_size=30)

    assert response.status_code == 200
    impact = response.json()["fidelity"]["adverse_impact"]
    assert impact is not None and impact["verdict"] == "ADVERSE_IMPACT"
    assert response.json()["gate"] == "WARN"
    assert any("adverse impact" in f for f in response.json()["findings"])


def test_groups_must_match_the_employees(gov: Gov, cohort: Callable[..., Any]) -> None:
    employees, left = cohort()
    assert train(gov, employees, left, groups=["a"]).status_code == 422


def test_models_are_per_tenant(gov: Gov, cohort: Callable[..., Any]) -> None:
    train(gov, *cohort())
    other = gov.client.get("/v1/attrition/models", headers=gov.headers("ADMIN", tenant=OTHER))

    assert other.json() == []
    assert (
        gov.client.post(
            "/v1/attrition/models/1/activate", headers=gov.headers("ADMIN", tenant=OTHER)
        ).status_code
        == 404
    )


def test_every_score_is_kept_with_its_model_version(gov: Gov, cohort: Callable[..., Any]) -> None:
    employees, left = cohort()
    train(gov, employees, left)
    one = employees[:1]
    gov.client.post("/v1/attrition/score", json={"employees": one}, headers=gov.headers("OPERATOR"))
    train(gov, *cohort(seed=11))
    gov.client.post("/v1/attrition/score", json={"employees": one}, headers=gov.headers("OPERATOR"))

    history = gov.client.get(
        f"/v1/attrition/scores/{one[0]['subject_key']}/history", headers=gov.headers("VIEWER")
    )
    assert history.headers["x-total-count"] == "2"
    assert [h["model_version"] for h in history.json()] == [2, 1]  # newest first
    roster = gov.client.get("/v1/attrition/scores", headers=gov.headers("VIEWER")).json()
    assert len(roster) == 1  # the roster still holds only the latest
    other = gov.client.get(
        f"/v1/attrition/scores/{one[0]['subject_key']}/history",
        headers=gov.headers("VIEWER", tenant=OTHER),
    )
    assert other.json() == []


def test_the_stored_blob_is_plain_arrays_not_a_pickle(gov: Gov, cohort: Callable[..., Any]) -> None:
    train(gov, *cohort())
    with gov.platform.database.session() as session:
        row = session.query(ModelVersionRow).one()
        blob = base64.b64decode(row.payload)
    assert blob[:2] == b"PK"  # a zip of .npy arrays
    assert AttritionModel.from_bytes(blob).is_trained
