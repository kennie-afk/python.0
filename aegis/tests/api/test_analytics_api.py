"""Verification, skills, workforce and sentiment over HTTP (they had no way in before)."""

from __future__ import annotations

import itertools
import json
import random
from typing import Any

import pytest

from aegis.reasoning.provider import Completion, Prompt
from conftest import OTHER, Gov

CANDIDATE = {
    "national_id": "12345678",
    "full_name": "Amina Wanjiru",
    "gender": "female",
    "university": "University of Nairobi",
    "years_experience": 7,
    "skill_match": 0.88,
    "summary": "Backend engineer with distributed systems experience.",
}


class FlappingModel:
    """Answers differently every call: the failure the determinism probe exists to catch."""

    name = "flapping"

    def __init__(self) -> None:
        self._count = itertools.count()

    def complete(self, prompt: Prompt) -> Completion:
        n = next(self._count)
        body = {
            "score": 0.1 + 0.07 * (n % 11),
            "recommendation": "REVIEW",
            "rationale": f"r{n}",
            "signals_considered": [],
        }
        return Completion(json.dumps(body), self.name, prompt.fingerprint())


# ---- verification ---------------------------------------------------------------------------
def test_a_deterministic_screener_passes_the_probe_and_is_recorded(gov: Gov) -> None:
    response = gov.client.post(
        "/v1/verification/determinism",
        json={
            "label": "Backend screen",
            "repetitions": 6,
            "cases": [{"name": "senior", "record": CANDIDATE, "requirement": "5 years of backend"}],
        },
        headers=gov.headers("OPERATOR"),
    )
    body = response.json()

    assert (
        response.status_code == 200
        and body["verdict"] == "PASSED"
        and body["kind"] == "determinism"
    )
    assert body["report"]["cases"][0]["stability"] == "DETERMINISTIC"
    assert body["ledger_sequence"] == 0
    entry = gov.client.get("/v1/ledger", headers=gov.headers("AUDITOR")).json()[0]
    assert (entry["workflow"], entry["outcome"]) == ("verification", "PASSED")


def test_a_flapping_screener_is_flagged(gov: Gov) -> None:
    gov.platform.model = FlappingModel()  # type: ignore[assignment]
    body = gov.client.post(
        "/v1/verification/determinism",
        json={"repetitions": 10, "cases": [{"name": "x", "record": CANDIDATE, "requirement": "r"}]},
        headers=gov.headers("OPERATOR"),
    ).json()

    assert body["verdict"] == "FLAGGED"
    assert body["report"]["cases"][0]["stability"] in {"UNSTABLE", "NON_DETERMINISTIC"}
    outcomes = [
        e["outcome"] for e in gov.client.get("/v1/ledger", headers=gov.headers("AUDITOR")).json()
    ]
    assert outcomes == ["FLAGGED"]


def drift_body(shift: float) -> dict[str, Any]:
    rng = random.Random(1)
    return {
        "label": "Applicant pool",
        "features": [
            {
                "name": "years",
                "kind": "psi",
                "baseline": [rng.gauss(5, 2) for _ in range(500)],
                "candidate": [rng.gauss(5 + shift, 2) for _ in range(500)],
            },
            {
                "name": "city",
                "kind": "categorical",
                "baseline": ["nairobi"] * 80 + ["mombasa"] * 20,
                "candidate": ["nairobi"] * 80 + ["mombasa"] * 20,
            },
        ],
    }


def test_drift_is_reported_per_feature_and_stable_data_passes(gov: Gov) -> None:
    quiet = gov.client.post(
        "/v1/verification/drift", json=drift_body(0.0), headers=gov.headers("OPERATOR")
    ).json()
    moved = gov.client.post(
        "/v1/verification/drift", json=drift_body(4.0), headers=gov.headers("OPERATOR")
    ).json()

    assert quiet["verdict"] == "STABLE" and quiet["report"]["features"][0]["metric"] == "psi"
    assert moved["verdict"] == "SIGNIFICANT"
    assert {f["feature"]: f["severity"] for f in moved["report"]["features"]} == {
        "years": "SIGNIFICANT",
        "city": "STABLE",
    }


def test_drift_input_is_validated(gov: Gov) -> None:
    headers = gov.headers("OPERATOR")
    text_in_numeric = {
        "features": [{"name": "x", "kind": "psi", "baseline": ["a"] * 20, "candidate": ["b"]}]
    }
    numbers_in_categorical = {
        "features": [
            {"name": "x", "kind": "categorical", "baseline": [1.0, 2.0], "candidate": [1.0]}
        ]
    }
    too_short = {
        "features": [{"name": "x", "kind": "psi", "baseline": [1.0, 2.0], "candidate": [1.0]}]
    }

    assert (
        gov.client.post("/v1/verification/drift", json=text_in_numeric, headers=headers).status_code
        == 422
    )
    assert (
        gov.client.post(
            "/v1/verification/drift", json=numbers_in_categorical, headers=headers
        ).status_code
        == 422
    )
    assert (
        gov.client.post("/v1/verification/drift", json=too_short, headers=headers).status_code
        == 422
    )


def test_fidelity_combines_both_into_a_gate(gov: Gov) -> None:
    determinism = {
        "repetitions": 4,
        "cases": [{"name": "a", "record": CANDIDATE, "requirement": "r"}],
    }
    passing = gov.client.post(
        "/v1/verification/fidelity",
        json={"determinism": determinism, "drift": drift_body(0.0)},
        headers=gov.headers("OPERATOR"),
    ).json()
    blocked = gov.client.post(
        "/v1/verification/fidelity",
        json={"drift": drift_body(6.0), "block_below": 0.6, "warn_below": 0.9},
        headers=gov.headers("OPERATOR"),
    ).json()

    assert passing["verdict"] == "PASS" and passing["report"]["deployable"] is True
    assert blocked["verdict"] == "BLOCK" and blocked["report"]["deployable"] is False
    assert any("SIGNIFICANT" in f for f in blocked["report"]["findings"])
    assert (
        gov.client.post(
            "/v1/verification/fidelity", json={}, headers=gov.headers("OPERATOR")
        ).status_code
        == 422
    )
    assert (
        gov.client.post(
            "/v1/verification/fidelity",
            json={"drift": drift_body(0.0), "warn_below": 0.5, "block_below": 0.9},
            headers=gov.headers("OPERATOR"),
        ).status_code
        == 422
    )


def test_stored_verification_reports_are_per_tenant_and_filterable(gov: Gov) -> None:
    run = gov.client.post(
        "/v1/verification/drift", json=drift_body(0.0), headers=gov.headers("OPERATOR")
    ).json()
    gov.client.post("/v1/verification/drift", json=drift_body(4.0), headers=gov.headers("OPERATOR"))

    listed = gov.client.get("/v1/verification/reports?kind=drift", headers=gov.headers("VIEWER"))
    assert listed.headers["x-total-count"] == "2" and listed.json()[0]["verdict"] == "SIGNIFICANT"
    assert (
        gov.client.get(
            "/v1/verification/reports?kind=fidelity", headers=gov.headers("VIEWER")
        ).json()
        == []
    )
    assert (
        gov.client.get(
            f"/v1/verification/reports/{run['id']}", headers=gov.headers("VIEWER")
        ).status_code
        == 200
    )

    stranger = gov.headers("ADMIN", tenant=OTHER)
    assert gov.client.get("/v1/verification/reports", headers=stranger).json() == []
    assert (
        gov.client.get(f"/v1/verification/reports/{run['id']}", headers=stranger).status_code == 404
    )
    assert (
        gov.client.get("/v1/verification/reports?kind=bogus", headers=stranger).status_code == 422
    )


def test_verification_needs_the_operator_role_to_compute_and_a_read_role_to_read(gov: Gov) -> None:
    for path, body in (
        ("determinism", {"cases": [{"name": "a", "record": CANDIDATE, "requirement": "r"}]}),
        ("drift", drift_body(0.0)),
        ("fidelity", {"drift": drift_body(0.0)}),
    ):
        for roles in (("VIEWER",), ("AUDITOR",), ()):
            assert (
                gov.client.post(
                    f"/v1/verification/{path}", json=body, headers=gov.headers(*roles)
                ).status_code
                == 403
            )
    assert gov.client.get("/v1/verification/reports", headers=gov.headers()).status_code == 403
    assert gov.client.post("/v1/verification/drift", json=drift_body(0.0)).status_code == 401


# ---- skills ---------------------------------------------------------------------------------
PROFILE = {
    "subject_key": "emp-1",
    "evidence": ["Built services in Python and Postgres.", "Ran python migrations on k8s nightly."],
    "years_by_skill": {"python": 6, "postgresql": 4, "kubernetes": 2},
}


def test_skills_are_extracted_with_proficiency_from_years(gov: Gov) -> None:
    body = gov.client.post(
        "/v1/skills/extract", json=PROFILE, headers=gov.headers("OPERATOR")
    ).json()
    levels = {h["skill"]: h["proficiency"] for h in body["holdings"]}

    assert levels["python"] == "EXPERT" and levels["postgresql"] == "PRACTITIONER"
    assert levels["kubernetes"] == "WORKING"
    taxonomy = gov.client.get("/v1/skills/taxonomy", headers=gov.headers("VIEWER")).json()
    assert {"python", "kubernetes"} <= {s["name"] for s in taxonomy}


def test_gap_forecast_counts_supply_after_attrition(gov: Gov) -> None:
    body = gov.client.post(
        "/v1/skills/gaps",
        json={
            "profiles": [PROFILE, {**PROFILE, "subject_key": "emp-2"}],
            "requirements": [
                {"skill": "py", "required": "EXPERT", "headcount": 3},
                {"skill": "kubernetes", "required": "WORKING", "headcount": 1},
            ],
            "horizon_months": 12,
            "attrition_rate": 0.5,
        },
        headers=gov.headers("OPERATOR"),
    ).json()
    gaps = {g["skill"]: g for g in body["gaps"]}

    assert gaps["python"]["supply"] == 1 and gaps["python"]["shortfall"] == 2  # alias "py" resolved
    assert gaps["kubernetes"]["covered"] is True and body["total_shortfall"] == 2
    assert "python" in body["summary"]


def test_mobility_and_internal_candidates(gov: Gov) -> None:
    role = {
        "role_id": "r1",
        "title": "Platform lead",
        "family": "platform",
        "requirements": [
            {"skill": "kubernetes", "required": "PRACTITIONER"},
            {"skill": "python", "required": "EXPERT"},
        ],
    }
    mobility = gov.client.post(
        "/v1/skills/mobility",
        json={"profile": PROFILE, "roles": [role], "minimum_score": 0.5},
        headers=gov.headers("OPERATOR"),
    ).json()
    candidates = gov.client.post(
        "/v1/skills/candidates",
        json={"profiles": [PROFILE], "role": role, "minimum_score": 0.5},
        headers=gov.headers("OPERATOR"),
    ).json()

    assert mobility[0]["stretch"] is True and mobility[0]["ready_now"] is False
    assert mobility[0]["development_path"] == ["kubernetes: WORKING to PRACTITIONER"]
    assert candidates[0]["subject_key"] == "emp-1"


def test_skills_errors_are_clean(gov: Gov) -> None:
    headers = gov.headers("OPERATOR")
    unknown = {"profiles": [PROFILE], "requirements": [{"skill": "cobol-on-rails"}]}
    assert gov.client.post("/v1/skills/gaps", json=unknown, headers=headers).status_code == 422
    bad_level = {"profiles": [PROFILE], "requirements": [{"skill": "python", "required": "GURU"}]}
    assert gov.client.post("/v1/skills/gaps", json=bad_level, headers=headers).status_code == 422
    assert (
        gov.client.post(
            "/v1/skills/extract", json=PROFILE, headers=gov.headers("VIEWER")
        ).status_code
        == 403
    )


# ---- workforce ------------------------------------------------------------------------------
def test_workforce_simulation_ramps_hires_and_solves_for_a_target(gov: Gov) -> None:
    scenario = {
        "name": "steady",
        "starting_headcount": 100,
        "monthly_attrition_rate": 0.02,
        "monthly_hires": 3,
        "hire_ramp_months": 3,
        "monthly_demand": 100,
    }
    body = gov.client.post(
        "/v1/workforce/simulate",
        json={
            "scenarios": [scenario, {**scenario, "name": "freeze", "monthly_hires": 0}],
            "months": 12,
            "target_headcount": 110,
        },
        headers=gov.headers("OPERATOR"),
    ).json()
    results = {r["scenario"]: r for r in body["results"]}

    assert len(results["steady"]["timeline"]) == 12
    assert results["freeze"]["final_headcount"] < results["steady"]["final_headcount"] < 120
    assert results["freeze"]["first_shortfall_month"] == 1
    month_one = results["steady"]["timeline"][0]
    assert month_one["effective_capacity"] < month_one["headcount"]  # hires have not ramped yet
    assert body["hires_required"]["steady"] >= 3


def test_workforce_inputs_are_bounded_and_validated(gov: Gov) -> None:
    headers = gov.headers("OPERATOR")
    base = {"name": "x", "starting_headcount": 10, "monthly_attrition_rate": 0.01}

    assert (
        gov.client.post(
            "/v1/workforce/simulate",
            json={"scenarios": [{**base, "monthly_attrition_rate": 1.5}]},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        gov.client.post(
            "/v1/workforce/simulate", json={"scenarios": [base], "months": 5000}, headers=headers
        ).status_code
        == 422
    )
    huge = gov.client.post(
        "/v1/workforce/simulate",
        json={"scenarios": [{**base, "starting_headcount": 90_000}], "target_headcount": 4_000},
        headers=headers,
    )
    assert "too large" in huge.json()["hires_required"]["x"]
    assert (
        gov.client.post(
            "/v1/workforce/simulate", json={"scenarios": [base]}, headers=gov.headers("VIEWER")
        ).status_code
        == 403
    )


# ---- sentiment ------------------------------------------------------------------------------
def responses(group: str, texts: list[str]) -> list[dict[str, str]]:
    return [{"group": group, "text": t} for t in texts]


GOOD = [
    "the manager is supportive and leadership is clear",
    "great team culture and good pay",
    "pay is fair and the work is good",
    "supportive manager, good tools",
    "good culture here",
]
BAD = [
    "leadership is not clear and the manager is poor",
    "pay is bad and hours are long",
    "poor management, terrible workload",
    "the manager is not supportive",
    "bad culture, poor tools",
]


def test_small_groups_are_withheld_and_no_response_is_returned(gov: Gov) -> None:
    body = gov.client.post(
        "/v1/sentiment/analyse",
        json={
            "responses": responses("ops", GOOD) + responses("tiny", GOOD[:2]),
            "minimum_group_size": 5,
        },
        headers=gov.headers("OPERATOR"),
    ).json()

    assert [g["group"] for g in body["groups"]] == ["ops"] and body["suppressed_groups"] == ["tiny"]
    assert "supportive" not in json.dumps(body)  # aggregates only
    assert (
        gov.client.post(
            "/v1/sentiment/analyse",
            json={"responses": responses("a", GOOD), "minimum_group_size": 1},
            headers=gov.headers("OPERATOR"),
        ).status_code
        == 422
    )


def test_an_early_warning_is_written_to_the_ledger_and_queued_as_an_alert(gov: Gov) -> None:
    body = gov.client.post(
        "/v1/sentiment/early-warning",
        json={
            "label": "Q3 vs Q4",
            "minimum_group_size": 5,
            "previous": responses("ops", GOOD),
            "current": responses("ops", BAD),
        },
        headers=gov.headers("OPERATOR"),
    ).json()

    assert body["warnings"] and body["warnings"][0]["group"] == "ops"
    assert body["warnings"] == sorted(body["warnings"], key=lambda w: -w["drop"])
    ledger = gov.client.get("/v1/ledger", headers=gov.headers("AUDITOR")).json()
    assert (ledger[0]["workflow"], ledger[0]["outcome"]) == ("sentiment", "FLAGGED")
    alerts = gov.client.get("/v1/alerts", headers=gov.headers("VIEWER")).json()
    assert alerts[0]["kind"] == "sentiment_warning" and alerts[0]["delivered_at"] is None
    assert gov.client.get("/v1/alerts", headers=gov.headers("VIEWER", tenant=OTHER)).json() == []

    again = gov.client.post(
        "/v1/sentiment/early-warning",
        json={
            "minimum_group_size": 5,
            "previous": responses("ops", GOOD),
            "current": responses("ops", BAD),
        },
        headers=gov.headers("OPERATOR"),
    )
    assert again.status_code == 200
    assert (
        len(gov.client.get("/v1/alerts", headers=gov.headers("VIEWER")).json()) == 1
    )  # same finding, one alert


def test_no_drop_is_a_pass_and_raises_no_alert(gov: Gov) -> None:
    body = gov.client.post(
        "/v1/sentiment/early-warning",
        json={
            "minimum_group_size": 5,
            "previous": responses("ops", GOOD),
            "current": responses("ops", GOOD),
        },
        headers=gov.headers("OPERATOR"),
    ).json()

    assert body["warnings"] == []
    assert gov.client.get("/v1/alerts", headers=gov.headers("VIEWER")).json() == []
    assert (
        gov.client.get("/v1/ledger", headers=gov.headers("AUDITOR")).json()[0]["outcome"]
        == "PASSED"
    )


@pytest.mark.parametrize("path", ["/v1/sentiment/analyse", "/v1/sentiment/early-warning"])
def test_sentiment_is_an_operator_action(gov: Gov, path: str) -> None:
    body = (
        {"responses": responses("a", GOOD)}
        if path.endswith("analyse")
        else {"previous": responses("a", GOOD), "current": responses("a", GOOD)}
    )
    assert gov.client.post(path, json=body, headers=gov.headers("VIEWER")).status_code == 403


def test_a_probe_that_would_make_too_many_screening_calls_is_refused(gov: Gov) -> None:
    case = {"name": "a", "record": CANDIDATE, "requirement": "r"}
    heavy = gov.client.post(
        "/v1/verification/determinism",
        json={"repetitions": 50, "cases": [case] * 5},
        headers=gov.headers("OPERATOR"),
    )

    assert heavy.status_code == 422 and "screening calls" in json.dumps(heavy.json())
    fine = gov.client.post(
        "/v1/verification/determinism",
        json={"repetitions": 20, "cases": [case] * 10},
        headers=gov.headers("OPERATOR"),
    )
    assert fine.status_code == 200
