"""Verification, skills, workforce and sentiment over HTTP.

These four modules existed and were tested but had no way in. Computation endpoints need the
OPERATOR role; reading stored results needs any read role. Verification results feed the ledger
(and are stored, per tenant); sentiment early warnings feed the ledger and the alert outbox.
Skills and workforce results are computed on demand and not stored: they contain no decision and
their inputs (free-text CV evidence, headcount) are not kept.

Imported at the bottom of aegis.api.app, which is why it can use app.* directly.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from typing import Any, Literal

from fastapi import HTTPException, Query, Response, status
from pydantic import BaseModel, Field, model_validator

from aegis.api.app import (
    AdminDep,
    OperatorDep,
    PersistentLedger,
    Platform,
    PlatformDep,
    ReaderDep,
    app,
)
from aegis.api.schemas import AlertView
from aegis.auth.tokens import Principal
from aegis.ops.checks import run_checks
from aegis.persistence.models import VerificationReportRow
from aegis.persistence.repositories import AlertRepository, VerificationRepository
from aegis.sentiment import (
    MINIMUM_GROUP_SIZE,
    SuppressedError,
    analyse,
    detect_early_warnings,
)
from aegis.sentiment import (
    Response as SentimentResponse,
)
from aegis.skills import (
    OpenRole,
    Proficiency,
    SkillProfile,
    SkillRequirement,
    TaxonomyError,
    forecast_gaps,
    internal_candidates,
    rank_roles,
)
from aegis.skills.default import default_taxonomy
from aegis.verification import (
    DeterminismProbe,
    DriftError,
    DriftReport,
    FidelityScorer,
    ProbeError,
    categorical_drift,
    distribution_shift,
    population_stability_index,
)
from aegis.verification.determinism import DeterminismReport
from aegis.verification.model_gate import determinism_view, drift_view
from aegis.verification.normalizers import BUILTIN
from aegis.workforce.simulation import (
    Scenario,
    SimulationError,
    compare,
    hires_required,
)

MAX_SAMPLES = 50_000
MAX_CASES = 20
MAX_REPETITIONS = 50
MAX_PROBE_CALLS = 200


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


# ---------------------------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------------------------
class DeterminismCase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    record: dict[str, Any]
    requirement: str = Field(min_length=1, max_length=500)


class DeterminismRequest(BaseModel):
    label: str = Field(default="Screening determinism", min_length=1, max_length=200)
    cases: list[DeterminismCase] = Field(min_length=1, max_length=MAX_CASES)
    repetitions: int = Field(default=10, ge=2, le=MAX_REPETITIONS)
    normalizer: Literal["identity", "collapse_whitespace", "casefold", "canonical_json"] = (
        "identity"
    )

    @model_validator(mode="after")
    def _bounded_cost(self) -> DeterminismRequest:
        # Every repetition is a screening call, which with a hosted model costs money.
        if len(self.cases) * self.repetitions > MAX_PROBE_CALLS:
            raise ValueError(
                f"{len(self.cases)} cases x {self.repetitions} repetitions is more than "
                f"{MAX_PROBE_CALLS} screening calls"
            )
        return self


class DriftFeature(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["psi", "ks", "categorical"] = "psi"
    baseline: list[float | str] = Field(min_length=2, max_length=MAX_SAMPLES)
    candidate: list[float | str] = Field(min_length=1, max_length=MAX_SAMPLES)

    @model_validator(mode="after")
    def _types_match_kind(self) -> DriftFeature:
        numeric = self.kind != "categorical"
        for value in (*self.baseline, *self.candidate):
            if numeric and isinstance(value, str):
                raise ValueError(f"{self.name}: {self.kind} drift needs numbers, got text")
            if not numeric and not isinstance(value, str):
                raise ValueError(f"{self.name}: categorical drift needs text values")
        return self


class DriftRequest(BaseModel):
    label: str = Field(default="Feature drift", min_length=1, max_length=200)
    features: list[DriftFeature] = Field(min_length=1, max_length=100)


class FidelityRequest(BaseModel):
    label: str = Field(default="Fidelity check", min_length=1, max_length=200)
    determinism: DeterminismRequest | None = None
    drift: DriftRequest | None = None
    warn_below: float = Field(default=0.90, ge=0.0, le=1.0)
    block_below: float = Field(default=0.70, ge=0.0, le=1.0)


class VerificationReportView(BaseModel):
    id: int
    kind: str
    label: str
    verdict: str
    report: dict[str, Any]
    ledger_sequence: int | None
    created_by: str
    created_at: str


def _stored_view(row: VerificationReportRow) -> VerificationReportView:
    return VerificationReportView(
        id=row.id,
        kind=row.kind,
        label=row.label,
        verdict=row.verdict,
        report=dict(row.report or {}),
        ledger_sequence=row.ledger_sequence,
        created_by=row.created_by,
        created_at=row.created_at.isoformat(),
    )


def _run_determinism(platform: Platform, request: DeterminismRequest) -> list[DeterminismReport]:
    probe = DeterminismProbe(
        repetitions=request.repetitions, normalizer=BUILTIN[request.normalizer]
    )
    screener = platform.screener()
    reports: list[DeterminismReport] = []
    for case in request.cases:

        def invoke(case: DeterminismCase = case) -> str:
            result = screener.screen(case.record, case.requirement)
            return json.dumps(
                {
                    "score": result.score,
                    "recommendation": result.recommendation,
                    "rationale": result.rationale,
                },
                sort_keys=True,
            )

        try:
            reports.append(probe.probe(case.name, invoke))
        except ProbeError as error:
            raise _unprocessable(str(error)) from error
    return reports


def _run_drift(request: DriftRequest) -> list[DriftReport]:
    reports: list[DriftReport] = []
    for feature in request.features:
        try:
            if feature.kind == "categorical":
                reports.append(
                    categorical_drift(
                        feature.name,
                        [str(v) for v in feature.baseline],
                        [str(v) for v in feature.candidate],
                    )
                )
            elif feature.kind == "ks":
                reports.append(
                    distribution_shift(
                        feature.name,
                        [float(v) for v in feature.baseline],
                        [float(v) for v in feature.candidate],
                    )
                )
            else:
                reports.append(
                    population_stability_index(
                        feature.name,
                        [float(v) for v in feature.baseline],
                        [float(v) for v in feature.candidate],
                    )
                )
        except DriftError as error:
            raise _unprocessable(str(error)) from error
    return reports


def _store(
    platform: Platform,
    caller: Principal,
    kind: str,
    label: str,
    verdict: str,
    outcome: str,
    reasons: list[str],
    report: dict[str, Any],
) -> VerificationReportView:
    """Stored per tenant, and the verdict written into the hash chain so it cannot be quietly
    removed later."""
    with platform.database.session(caller.tenant_id) as session:
        entry = PersistentLedger(session, caller.tenant_id, platform.ledger_key).append(
            tenant_id=caller.tenant_id,
            workflow="verification",
            run_id=str(uuid.uuid4()),
            step=kind,
            action_type=f"VERIFICATION_{kind.upper()}",
            subject_id=label,
            agent="aegis-verification",
            outcome=outcome,
            reasons=tuple(reasons[:5]),
            approver=None,
        )
        row = VerificationRepository(session).add(
            VerificationReportRow(
                tenant_id=caller.tenant_id,
                kind=kind,
                label=label,
                verdict=verdict,
                report=report,
                ledger_sequence=entry.sequence,
                created_by=caller.subject,
            )
        )
        return _stored_view(row)


@app.post("/v1/verification/determinism")
def verify_determinism(
    request: DeterminismRequest, caller: OperatorDep, platform: PlatformDep
) -> VerificationReportView:
    """Run the same screening cases repeatedly and report whether the answers are stable."""
    reports = _run_determinism(platform, request)
    failing = [r for r in reports if not r.passes]
    return _store(
        platform,
        caller,
        "determinism",
        request.label,
        "FLAGGED" if failing else "PASSED",
        "FLAGGED" if failing else "PASSED",
        [f"{r.case}: {r.stability} ({r.distinct_outputs} distinct outputs)" for r in failing]
        or [f"{len(reports)} case(s) stable across {request.repetitions} runs"],
        {"repetitions": request.repetitions, "cases": [determinism_view(r) for r in reports]},
    )


@app.post("/v1/verification/drift")
def verify_drift(
    request: DriftRequest, caller: OperatorDep, platform: PlatformDep
) -> VerificationReportView:
    """Compare a candidate sample with a baseline, per feature (PSI, KS or categorical PSI)."""
    reports = _run_drift(request)
    drifted = [r for r in reports if r.has_drifted]
    worst = max((str(r.severity) for r in reports), key=["STABLE", "MODERATE", "SIGNIFICANT"].index)
    return _store(
        platform,
        caller,
        "drift",
        request.label,
        worst,
        "FLAGGED" if drifted else "PASSED",
        [f"{r.feature}: {r.severity} ({r.metric} {r.statistic:.3f})" for r in drifted]
        or ["no feature drifted"],
        {"features": [drift_view(r) for r in reports]},
    )


@app.post("/v1/verification/fidelity")
def verify_fidelity(
    request: FidelityRequest, caller: OperatorDep, platform: PlatformDep
) -> VerificationReportView:
    """Combine determinism and drift into one PASS / WARN / BLOCK verdict."""
    if request.determinism is None and request.drift is None:
        raise _unprocessable("supply determinism cases, drift features, or both")
    if request.block_below > request.warn_below:
        raise _unprocessable("block_below cannot exceed warn_below")
    determinism = _run_determinism(platform, request.determinism) if request.determinism else []
    drift = _run_drift(request.drift) if request.drift else []
    fidelity = FidelityScorer(request.warn_below, request.block_below).score(determinism, drift)
    gate = str(fidelity.gate)
    outcome = {"PASS": "PASSED", "WARN": "WARNED", "BLOCK": "BLOCKED"}[gate]
    return _store(
        platform,
        caller,
        "fidelity",
        request.label,
        gate,
        outcome,
        list(fidelity.findings) or [f"fidelity {fidelity.score:.2f}"],
        {
            "score": round(fidelity.score, 4),
            "gate": gate,
            "deployable": fidelity.deployable,
            "determinism_score": round(fidelity.determinism_score, 4),
            "drift_score": round(fidelity.drift_score, 4),
            "findings": list(fidelity.findings),
            "determinism": [determinism_view(r) for r in determinism],
            "drift": [drift_view(r) for r in drift],
        },
    )


@app.get("/v1/verification/reports")
def list_verification_reports(
    caller: ReaderDep,
    platform: PlatformDep,
    response: Response,
    kind: str | None = Query(default=None, pattern="^(determinism|drift|fidelity)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[VerificationReportView]:
    with platform.database.session(caller.tenant_id) as session:
        rows, total = VerificationRepository(session).search(
            caller.tenant_id, kind=kind, limit=limit, offset=offset
        )
        response.headers["X-Total-Count"] = str(total)
        return [_stored_view(row) for row in rows]


@app.get("/v1/verification/reports/{report_id}")
def get_verification_report(
    report_id: int, caller: ReaderDep, platform: PlatformDep
) -> VerificationReportView:
    with platform.database.session(caller.tenant_id) as session:
        row = session.get(VerificationReportRow, report_id)
        if row is None or row.tenant_id != caller.tenant_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report not found")
        return _stored_view(row)


# ---------------------------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------------------------
LEVELS = {level.name: level for level in Proficiency}


class ProfileIn(BaseModel):
    subject_key: str = Field(min_length=1, max_length=200)
    evidence: list[str] = Field(min_length=1, max_length=50)
    years_by_skill: dict[str, float] = Field(default_factory=dict)


class RequirementIn(BaseModel):
    skill: str = Field(min_length=1, max_length=100)
    required: Literal["AWARENESS", "WORKING", "PRACTITIONER", "EXPERT"] = "WORKING"
    headcount: int = Field(default=1, ge=1, le=10_000)


class GapRequest(BaseModel):
    profiles: list[ProfileIn] = Field(min_length=1, max_length=2000)
    requirements: list[RequirementIn] = Field(min_length=1, max_length=100)
    horizon_months: int = Field(default=12, ge=1, le=60)
    attrition_rate: float = Field(default=0.0, ge=0.0, lt=1.0)


class RoleIn(BaseModel):
    role_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    requirements: list[RequirementIn] = Field(min_length=1, max_length=50)
    family: str = "general"


class MobilityRequest(BaseModel):
    profile: ProfileIn
    roles: list[RoleIn] = Field(min_length=1, max_length=200)
    minimum_score: float = Field(default=0.5, ge=0.0, le=1.0)


class CandidatesRequest(BaseModel):
    profiles: list[ProfileIn] = Field(min_length=1, max_length=2000)
    role: RoleIn
    minimum_score: float = Field(default=0.6, ge=0.0, le=1.0)


def _profile(item: ProfileIn) -> SkillProfile:
    return default_taxonomy().extract(item.subject_key, item.evidence, item.years_by_skill)


def _requirement(item: RequirementIn) -> SkillRequirement:
    taxonomy = default_taxonomy()
    skill = taxonomy.canonical(item.skill)
    if skill is None:
        raise _unprocessable(f"unknown skill {item.skill!r}; see GET /v1/skills/taxonomy")
    return SkillRequirement(skill, LEVELS[item.required], item.headcount)


def _role(item: RoleIn) -> OpenRole:
    return OpenRole(
        item.role_id, item.title, tuple(_requirement(r) for r in item.requirements), item.family
    )


def _match_view(match: Any) -> dict[str, Any]:
    return {
        "subject_key": match.subject_key,
        "role_id": match.role_id,
        "title": match.title,
        "score": match.score,
        "ready_now": match.ready_now,
        "stretch": match.stretch,
        "development_path": list(match.development_path()),
        "satisfied": [d.skill for d in match.satisfied],
        "missing": [
            {"skill": d.skill, "held": d.held.name, "required": d.required.name}
            for d in match.missing
        ],
    }


def _guarded[T](compute: Callable[[], T]) -> T:
    try:
        return compute()
    except TaxonomyError as error:
        raise _unprocessable(str(error)) from error


@app.get("/v1/skills/taxonomy")
def skills_taxonomy(caller: ReaderDep) -> list[dict[str, Any]]:
    return [
        {"name": s.name, "family": s.family, "aliases": sorted(s.aliases)}
        for s in default_taxonomy().skills
    ]


@app.post("/v1/skills/extract")
def skills_extract(request: ProfileIn, caller: OperatorDep) -> dict[str, Any]:
    """Skills and proficiency from free-text evidence. Proficiency comes from stated years, not
    from how often a word appears. The evidence is not stored."""
    profile = _profile(request)
    return {
        "subject_key": profile.subject_key,
        "holdings": [
            {
                "skill": h.skill,
                "proficiency": h.proficiency.name,
                "mentions": h.mentions,
                "years": h.years,
            }
            for h in profile.holdings
        ],
    }


@app.post("/v1/skills/gaps")
def skills_gaps(request: GapRequest, caller: OperatorDep) -> dict[str, Any]:
    forecast = _guarded(
        lambda: forecast_gaps(
            [_profile(p) for p in request.profiles],
            [_requirement(r) for r in request.requirements],
            request.horizon_months,
            request.attrition_rate,
        )
    )
    return {
        "horizon_months": forecast.horizon_months,
        "summary": forecast.summary(),
        "total_shortfall": forecast.total_shortfall,
        "gaps": [
            {
                "skill": g.skill,
                "required": g.required.name,
                "supply": g.supply,
                "demand": g.demand,
                "shortfall": g.shortfall,
                "covered": g.covered,
            }
            for g in forecast.gaps
        ],
    }


@app.post("/v1/skills/mobility")
def skills_mobility(request: MobilityRequest, caller: OperatorDep) -> list[dict[str, Any]]:
    """Roles an employee is ready for or within one level of, with the development path."""
    matches = _guarded(
        lambda: rank_roles(
            _profile(request.profile),
            [_role(r) for r in request.roles],
            default_taxonomy(),
            request.minimum_score,
        )
    )
    return [_match_view(m) for m in matches]


@app.post("/v1/skills/candidates")
def skills_candidates(request: CandidatesRequest, caller: OperatorDep) -> list[dict[str, Any]]:
    """Internal candidates for a vacancy, best first."""
    matches = _guarded(
        lambda: internal_candidates(
            [_profile(p) for p in request.profiles],
            _role(request.role),
            default_taxonomy(),
            request.minimum_score,
        )
    )
    return [_match_view(m) for m in matches]


# ---------------------------------------------------------------------------------------------
# Workforce
# ---------------------------------------------------------------------------------------------
class ScenarioIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    starting_headcount: int = Field(ge=0, le=100_000)
    monthly_attrition_rate: float = Field(ge=0.0, lt=1.0)
    monthly_hires: int = Field(default=0, ge=0, le=10_000)
    hire_ramp_months: int = Field(default=3, ge=0, le=24)
    monthly_demand: float = Field(default=0.0, ge=0.0, le=1_000_000)


class WorkforceRequest(BaseModel):
    scenarios: list[ScenarioIn] = Field(min_length=1, max_length=10)
    months: int = Field(default=12, ge=1, le=120)
    # Ask what monthly hiring reaches this headcount, for each scenario.
    target_headcount: int | None = Field(default=None, ge=0, le=5_000)


@app.post("/v1/workforce/simulate")
def workforce_simulate(request: WorkforceRequest, caller: OperatorDep) -> dict[str, Any]:
    """Headcount, attrition and effective capacity month by month; hires ramp up rather than
    counting in full from day one."""
    try:
        scenarios = [Scenario(**item.model_dump()) for item in request.scenarios]
        results = compare(scenarios, request.months)
        required: dict[str, int | str] = {}
        if request.target_headcount is not None:
            for scenario in scenarios:
                if scenario.starting_headcount + request.target_headcount > 5_000:
                    required[scenario.name] = (
                        "too large to solve; keep headcount plus target under 5,000"
                    )
                    continue
                try:
                    required[scenario.name] = hires_required(
                        scenario, request.target_headcount, request.months
                    )
                except SimulationError as error:
                    required[scenario.name] = str(error)
    except SimulationError as error:
        raise _unprocessable(str(error)) from error

    return {
        "months": request.months,
        "results": [
            {
                "scenario": r.scenario,
                "final_headcount": r.final_headcount,
                "total_leavers": r.total_leavers,
                "total_hires": r.total_hires,
                "first_shortfall_month": r.first_shortfall_month,
                "peak_shortfall": r.peak_shortfall,
                "summary": r.summary(),
                "timeline": [
                    {
                        "month": m.month,
                        "headcount": m.headcount,
                        "effective_capacity": m.effective_capacity,
                        "leavers": m.leavers,
                        "joiners": m.joiners,
                        "demand": m.demand,
                        "shortfall": m.shortfall,
                    }
                    for m in r.months
                ],
            }
            for r in results
        ],
        "hires_required": required,
    }


# ---------------------------------------------------------------------------------------------
# Sentiment
# ---------------------------------------------------------------------------------------------
class ResponseIn(BaseModel):
    group: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=4000)


class SentimentRequest(BaseModel):
    responses: list[ResponseIn] = Field(min_length=1, max_length=20_000)
    minimum_group_size: int = Field(default=MINIMUM_GROUP_SIZE, ge=2, le=1000)


class EarlyWarningRequest(BaseModel):
    label: str = Field(default="Sentiment early warning", min_length=1, max_length=200)
    previous: list[ResponseIn] = Field(min_length=1, max_length=20_000)
    current: list[ResponseIn] = Field(min_length=1, max_length=20_000)
    minimum_group_size: int = Field(default=MINIMUM_GROUP_SIZE, ge=2, le=1000)
    drop_threshold: float = Field(default=0.35, gt=0.0, le=2.0)


def _report_view(report: Any) -> dict[str, Any]:
    # Group aggregates only. No individual response is ever returned.
    return {
        "minimum_group_size": report.minimum_group_size,
        "summary": report.summary(),
        "groups": [
            {
                "group": g.group,
                "respondents": g.respondents,
                "overall": g.overall,
                "aspects": [
                    {
                        "aspect": a.aspect.value,
                        "score": a.score,
                        "mentions": a.mentions,
                        "negative": a.negative,
                    }
                    for a in g.aspects
                ],
            }
            for g in report.groups
        ],
        "suppressed_groups": list(report.suppressed_groups),
    }


def _to_responses(items: list[ResponseIn]) -> list[SentimentResponse]:
    return [SentimentResponse(group=i.group, text=i.text) for i in items]


@app.post("/v1/sentiment/analyse")
def sentiment_analyse(request: SentimentRequest, caller: OperatorDep) -> dict[str, Any]:
    """Aspect-based sentiment by group. Groups smaller than the minimum are withheld entirely."""
    try:
        report = analyse(_to_responses(request.responses), request.minimum_group_size)
    except SuppressedError as error:
        raise _unprocessable(str(error)) from error
    return _report_view(report)


@app.post("/v1/sentiment/early-warning")
def sentiment_early_warning(
    request: EarlyWarningRequest, caller: OperatorDep, platform: PlatformDep
) -> dict[str, Any]:
    """Aspects that dropped sharply between two periods, worst first. A finding is written to the
    ledger and queued as an alert."""
    try:
        previous = analyse(_to_responses(request.previous), request.minimum_group_size)
        current = analyse(_to_responses(request.current), request.minimum_group_size)
        warnings = detect_early_warnings(previous, current, request.drop_threshold)
    except SuppressedError as error:
        raise _unprocessable(str(error)) from error

    body = [
        {
            "group": w.group,
            "aspect": w.aspect.value,
            "previous": w.previous,
            "current": w.current,
            "drop": round(w.drop, 4),
        }
        for w in warnings
    ]
    with platform.database.session(caller.tenant_id) as session:
        entry = PersistentLedger(session, caller.tenant_id, platform.ledger_key).append(
            tenant_id=caller.tenant_id,
            workflow="sentiment",
            run_id=str(uuid.uuid4()),
            step="early_warning",
            action_type="SENTIMENT_EARLY_WARNING",
            subject_id=request.label,
            agent="aegis-sentiment",
            outcome="FLAGGED" if warnings else "PASSED",
            reasons=tuple(f"{w['group']}: {w['aspect']} fell by {w['drop']}" for w in body[:5])
            or ("no aspect dropped past the threshold",),
            approver=None,
        )
        if warnings:
            AlertRepository(session).add(
                caller.tenant_id,
                "sentiment_warning",
                f"sentiment early warning: {len(warnings)} aspect(s) dropped sharply "
                f"(worst: {body[0]['group']} {body[0]['aspect']})",
                {"warnings": body[:10], "label": request.label},
                dedupe_key=hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[
                    :32
                ],
            )
    return {
        "warnings": body,
        "ledger_sequence": entry.sequence,
        "previous": _report_view(previous),
        "current": _report_view(current),
    }


# ---------------------------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------------------------
@app.get("/v1/alerts")
def list_alerts(
    caller: ReaderDep,
    platform: PlatformDep,
    response: Response,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[AlertView]:
    with platform.database.session(caller.tenant_id) as session:
        rows, total = AlertRepository(session).recent(caller.tenant_id, limit, offset)
        response.headers["X-Total-Count"] = str(total)
        return [
            AlertView(
                id=r.id,
                kind=r.kind,
                message=r.message,
                created_at=r.created_at.isoformat(),
                delivered_at=r.delivered_at.isoformat() if r.delivered_at else None,
                attempts=r.attempts,
                last_error=r.last_error,
            )
            for r in rows
        ]


@app.post("/v1/ops/checks")
def run_checks_now(caller: AdminDep, platform: PlatformDep) -> dict[str, Any]:
    """Run the scheduled checks for this tenant now: ledger integrity, drift of recently scored
    inputs against the active model's reference, retrain due. Alerts are delivered if a channel
    is configured. The same checks run on a schedule from `python -m aegis.ops`."""
    summary = run_checks(platform.database, caller.tenant_id, platform.alerts)
    return {
        "tenant_id": summary.tenant_id,
        "alerts_queued": summary.alerts_queued,
        "delivered": summary.delivered,
        "notes": summary.notes,
    }
