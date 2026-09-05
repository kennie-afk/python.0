from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse

from aegis.agents.runtime import AgentRuntime, ApprovalError
from aegis.agents.tools import RecordingTool, ToolRegistry
from aegis.agents.workflow import WorkflowRun
from aegis.anonymization.engine import AnonymizationEngine
from aegis.api.schemas import (
    AdverseImpactRequest,
    AdverseImpactResponse,
    AnonymizeRequest,
    AnonymizeResponse,
    ApprovalRequest,
    AttritionScoreView,
    DriverView,
    EmployeeIn,
    ExternalResultRequest,
    GroupImpactView,
    IntegrityView,
    LedgerEntryView,
    RejectionRequest,
    RunView,
    ScoreRequest,
    StartRunRequest,
    StepView,
    TrainRequest,
    TrainResponse,
)
from aegis.attrition.features import EmployeeSnapshot
from aegis.attrition.model import AttritionModel, ModelError
from aegis.bias.adverse_impact import (
    AdverseImpactError,
    GroupOutcome,
    four_fifths_test,
)
from aegis.governance.actions import ActionType
from aegis.governance.gate import GovernanceGate
from aegis.governance.policy import TenantPolicy
from aegis.hr.workflows import CATALOGUE
from aegis.ledger.record import DecisionLedger


class Platform:
    def __init__(self) -> None:
        self.runs: dict[str, WorkflowRun] = {}
        self.ledgers: dict[str, DecisionLedger] = {}
        self.models: dict[str, AttritionModel] = {}
        self.policies: dict[str, TenantPolicy] = {}
        self.salt = os.environ.get("AEGIS_ANONYMISATION_SALT", "aegis-development-salt-value")

    def ledger(self, tenant: str) -> DecisionLedger:
        return self.ledgers.setdefault(tenant, DecisionLedger())

    def policy(self, tenant: str) -> TenantPolicy:
        return self.policies.setdefault(tenant, TenantPolicy.conservative(tenant))

    def runtime(self, tenant: str) -> AgentRuntime:
        tools = ToolRegistry()
        tools.register(RecordingTool(frozenset(ActionType), output={"executed": True}))
        return AgentRuntime(
            gate=GovernanceGate(self.policy(tenant)),
            tools=tools,
            ledger=self.ledger(tenant),
        )

    def anonymizer(self) -> AnonymizationEngine:
        return AnonymizationEngine(salt=self.salt)


_platform = Platform()


def get_platform() -> Iterator[Platform]:
    yield _platform


def tenant_id(x_tenant_id: Annotated[str | None, Header()] = None) -> str:
    if not x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Tenant-Id header is required; every action is scoped to a tenant",
        )
    try:
        UUID(x_tenant_id)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-Id must be a UUID",
        ) from error
    return x_tenant_id


TenantDep = Annotated[str, Depends(tenant_id)]
PlatformDep = Annotated[Platform, Depends(get_platform)]

app = FastAPI(
    title="Aegis",
    version="0.1.0",
    description="HR automation platform with structural governance",
)


@app.exception_handler(ApprovalError)
async def approval_error_handler(_: object, error: ApprovalError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "title": "approval-conflict",
            "detail": str(error),
            "status": 409,
            "code": "approval-conflict",
        },
    )


@app.exception_handler(ModelError)
async def model_error_handler(_: object, error: ModelError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "title": "model-error",
            "detail": str(error),
            "status": 422,
            "code": "model-error",
        },
    )


@app.exception_handler(AdverseImpactError)
async def adverse_impact_error_handler(_: object, error: AdverseImpactError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "title": "adverse-impact-error",
            "detail": str(error),
            "status": 422,
            "code": "adverse-impact-error",
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/workflows")
def list_workflows() -> dict[str, list[str]]:
    return {name: [step.key for step in wf.steps] for name, wf in CATALOGUE.items()}


def _view(run: WorkflowRun, runtime: AgentRuntime) -> RunView:
    return RunView(
        run_id=str(run.run_id),
        workflow=run.definition.name,
        tenant_id=str(run.tenant_id),
        subject_id=run.subject_id,
        status=str(run.status),
        steps=[
            StepView(
                key=state.key,
                status=str(state.status),
                reasons=list(state.reasons),
                approver=state.approver,
                attempts=state.attempts,
            )
            for state in run.steps.values()
        ],
        pending_approvals=list(runtime.pending_approvals(run)),
        context=dict(run.context),
    )


def _load(platform: Platform, tenant: str, run_id: str) -> WorkflowRun:
    run = platform.runs.get(run_id)
    if run is None or str(run.tenant_id) != tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return run


@app.post("/v1/runs", status_code=status.HTTP_201_CREATED)
def start_run(
    request: StartRunRequest, tenant: TenantDep, platform: PlatformDep
) -> RunView:
    definition = CATALOGUE.get(request.workflow)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown workflow {request.workflow!r}",
        )

    runtime = platform.runtime(tenant)
    run = runtime.start(definition, UUID(tenant), request.subject_id, request.context)
    runtime.advance(run)
    platform.runs[str(run.run_id)] = run
    return _view(run, runtime)


@app.get("/v1/runs/{run_id}")
def get_run(run_id: str, tenant: TenantDep, platform: PlatformDep) -> RunView:
    return _view(_load(platform, tenant, run_id), platform.runtime(tenant))


@app.post("/v1/runs/{run_id}/steps/{step_key}/approve")
def approve_step(
    run_id: str,
    step_key: str,
    request: ApprovalRequest,
    tenant: TenantDep,
    platform: PlatformDep,
) -> RunView:
    run = _load(platform, tenant, run_id)
    runtime = platform.runtime(tenant)
    runtime.approve(run, step_key, request.approver)
    return _view(run, runtime)


@app.post("/v1/runs/{run_id}/steps/{step_key}/reject")
def reject_step(
    run_id: str,
    step_key: str,
    request: RejectionRequest,
    tenant: TenantDep,
    platform: PlatformDep,
) -> RunView:
    run = _load(platform, tenant, run_id)
    runtime = platform.runtime(tenant)
    runtime.reject(run, step_key, request.approver, request.reason)
    return _view(run, runtime)


@app.post("/v1/runs/{run_id}/steps/{step_key}/external")
def resolve_external(
    run_id: str,
    step_key: str,
    request: ExternalResultRequest,
    tenant: TenantDep,
    platform: PlatformDep,
) -> RunView:
    run = _load(platform, tenant, run_id)
    runtime = platform.runtime(tenant)
    runtime.resolve_external(run, step_key, request.result, request.succeeded)
    return _view(run, runtime)


@app.post("/v1/anonymize")
def anonymize(
    request: AnonymizeRequest, tenant: TenantDep, platform: PlatformDep
) -> AnonymizeResponse:
    try:
        result = platform.anonymizer().anonymize(request.record)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error

    return AnonymizeResponse(
        subject_key=result.subject_key,
        attributes=dict(result.attributes),
        dropped=list(result.report.dropped),
        pseudonymised=list(result.report.pseudonymised),
        generalised=list(result.report.generalised),
        scrubbed_free_text=list(result.report.scrubbed_free_text),
    )


@app.post("/v1/bias/adverse-impact")
def adverse_impact(
    request: AdverseImpactRequest, tenant: TenantDep
) -> AdverseImpactResponse:
    report = four_fifths_test(
        [
            GroupOutcome(group=item.group, selected=item.selected, total=item.total)
            for item in request.outcomes
        ],
        minimum_group_size=request.minimum_group_size,
    )

    return AdverseImpactResponse(
        verdict=str(report.verdict),
        reference_group=report.reference_group,
        reference_rate=report.reference_rate,
        groups=[
            GroupImpactView(
                group=group.group,
                selection_rate=group.selection_rate,
                impact_ratio=group.impact_ratio,
                total=group.total,
                selected=group.selected,
                adversely_impacted=group.adversely_impacted,
            )
            for group in report.groups
        ],
        p_value=report.p_value,
        summary=report.summary(),
    )


def _snapshot(employee: EmployeeIn) -> EmployeeSnapshot:
    return EmployeeSnapshot(
        subject_key=employee.subject_key,
        tenure_years=employee.tenure_years,
        months_since_promotion=employee.months_since_promotion,
        salary=employee.salary,
        band_midpoint=employee.band_midpoint,
        peer_median_salary=employee.peer_median_salary,
        manager_changes_24m=employee.manager_changes_24m,
        commute_minutes=employee.commute_minutes,
        engagement_score=employee.engagement_score,
        training_hours_12m=employee.training_hours_12m,
        overtime_hours_monthly=employee.overtime_hours_monthly,
        internal_applications_12m=employee.internal_applications_12m,
    )


@app.post("/v1/attrition/train")
def train_model(
    request: TrainRequest, tenant: TenantDep, platform: PlatformDep
) -> TrainResponse:
    if len(request.employees) != len(request.left):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="employees and outcomes must be the same length",
        )

    model = AttritionModel(request.algorithm)
    report = model.train([_snapshot(item) for item in request.employees], request.left)
    platform.models[tenant] = model

    return TrainResponse(
        rows=report.rows,
        positives=report.positives,
        positive_rate=report.positive_rate,
        algorithm=report.algorithm,
        feature_importance=dict(report.feature_importance),
    )


@app.post("/v1/attrition/score")
def score_employees(
    request: ScoreRequest, tenant: TenantDep, platform: PlatformDep
) -> list[AttritionScoreView]:
    model = platform.models.get(tenant)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no attrition model has been trained for this tenant",
        )

    scores = model.score_all([_snapshot(item) for item in request.employees])
    return [
        AttritionScoreView(
            subject_key=score.subject_key,
            probability=round(score.probability, 4),
            band=str(score.band),
            needs_intervention=score.needs_intervention,
            drivers=[
                DriverView(
                    feature=driver.feature,
                    contribution=driver.contribution,
                    direction=driver.direction,
                )
                for driver in score.top_drivers()
            ],
        )
        for score in scores
    ]


@app.get("/v1/ledger")
def read_ledger(tenant: TenantDep, platform: PlatformDep) -> list[LedgerEntryView]:
    return [
        LedgerEntryView(
            sequence=entry.sequence,
            workflow=entry.workflow,
            step=entry.step,
            action_type=entry.action_type,
            subject_id=entry.subject_id,
            outcome=entry.outcome,
            reasons=list(entry.reasons),
            approver=entry.approver,
            recorded_at=entry.recorded_at.isoformat(),
        )
        for entry in platform.ledger(tenant).entries
    ]


@app.get("/v1/ledger/verify")
def verify_ledger(tenant: TenantDep, platform: PlatformDep) -> IntegrityView:
    report = platform.ledger(tenant).verify()
    return IntegrityView(
        intact=report.intact,
        entries_checked=report.entries_checked,
        broken_at=report.broken_at,
        reason=report.reason,
    )
