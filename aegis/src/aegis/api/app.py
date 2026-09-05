from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Sequence
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from aegis.agents.runtime import (
    MAX_STEP_ATTEMPTS,
    AgentRuntime,
    ApprovalError,
    MissingContextError,
    RetryError,
)
from aegis.agents.tools import RecordingTool, ToolRegistry
from aegis.agents.workflow import StepStatus, WorkflowRun
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
    ModelStatusView,
    RejectionRequest,
    RetryRequest,
    RunView,
    ScoreRequest,
    ScreeningView,
    ScreenRequest,
    StartRunRequest,
    StepView,
    TokenRequest,
    TokenResponse,
    TrainRequest,
    TrainResponse,
    WorkflowStepView,
    WorkflowView,
)
from aegis.attrition.features import EmployeeSnapshot
from aegis.attrition.model import AttritionModel, ModelError
from aegis.auth.tokens import AuthError, Principal, TokenService, hash_api_key
from aegis.bias.adverse_impact import (
    AdverseImpactError,
    GroupOutcome,
    four_fifths_test,
)
from aegis.governance.actions import IRREVERSIBLE_ACTIONS, ActionType
from aegis.governance.gate import GovernanceGate
from aegis.governance.policy import TenantPolicy
from aegis.hr.workflows import CATALOGUE
from aegis.integrations.calendar import CalendarTool, InMemoryCalendar
from aegis.integrations.email import (
    EmailError,
    EmailTool,
    EmailTransport,
    MockEmailTransport,
    SmtpEmailTransport,
)
from aegis.ledger.record import DecisionLedger, LedgerEntry, make_entry
from aegis.persistence.repositories import (
    ApiKeyRepository,
    LedgerRepository,
    ModelRepository,
    PolicyRepository,
    RunRepository,
)
from aegis.persistence.session import Database
from aegis.reasoning.deterministic import DeterministicModel
from aegis.reasoning.http_model import HttpLanguageModel
from aegis.reasoning.provider import LanguageModel, ReasoningError
from aegis.reasoning.screening import CandidateScreener

logger = logging.getLogger("aegis.platform")


def _configured_model() -> LanguageModel:
    try:
        return HttpLanguageModel.from_environment()
    except ReasoningError as reason:
        logger.info("using the deterministic model: %s", reason)
        return DeterministicModel()


def _configured_email() -> EmailTransport:
    try:
        return SmtpEmailTransport.from_environment()
    except EmailError as reason:
        logger.info("using the mock email transport: %s", reason)
        return MockEmailTransport()


class Platform:
    def __init__(self, database: Database | None = None, model: LanguageModel | None = None):
        self.database = database or Database()
        self.database.create_all()
        self.salt = os.environ.get("AEGIS_ANONYMISATION_SALT", "aegis-development-salt-value")
        self.tokens = TokenService(
            secret=os.environ.get(
                "AEGIS_JWT_SECRET", "aegis-development-signing-secret-not-for-production"
            )
        )
        self.model = model or _configured_model()
        self.calendar = InMemoryCalendar()
        self.email = _configured_email()

    @property
    def delivery(self) -> dict[str, str]:
        return {
            "model": self.model.name,
            "email": type(self.email).__name__,
            "calendar": type(self.calendar).__name__,
        }

    def policy(self, session: Session, tenant: str) -> TenantPolicy:
        stored = PolicyRepository(session).load(tenant)
        return stored or TenantPolicy.conservative(tenant)

    def runtime(self, session: Session, tenant: str) -> AgentRuntime:
        tools = ToolRegistry()
        tools.register(EmailTool(self.email))
        tools.register(CalendarTool(self.calendar))
        remaining = frozenset(ActionType) - tools.registered()
        tools.register(RecordingTool(remaining, output={"executed": True}))

        return AgentRuntime(
            gate=GovernanceGate(self.policy(session, tenant)),
            tools=tools,
            ledger=PersistentLedger(session, tenant),
        )

    def anonymizer(self) -> AnonymizationEngine:
        return AnonymizationEngine(salt=self.salt)

    def screener(self) -> CandidateScreener:
        return CandidateScreener(self.model, self.anonymizer())


class PersistentLedger(DecisionLedger):
    def __init__(self, session: Session, tenant: str) -> None:
        super().__init__()
        self._tenant = tenant
        self._repository = LedgerRepository(session)
        self._sequence, self._head = self._repository.head(tenant)

    @property
    def head_hash(self) -> str:
        return self._head

    def append(
        self,
        tenant_id: str,
        workflow: str,
        run_id: str,
        step: str,
        action_type: str,
        subject_id: str,
        agent: str,
        outcome: str,
        reasons: Sequence[str] = (),
        approver: str | None = None,
    ) -> LedgerEntry:
        entry = make_entry(
            sequence=self._sequence,
            previous_hash=self._head,
            tenant_id=self._tenant,
            workflow=workflow,
            run_id=run_id,
            step=step,
            action_type=action_type,
            subject_id=subject_id,
            agent=agent,
            outcome=outcome,
            reasons=reasons,
            approver=approver,
        )
        self._repository.append(self._tenant, entry)
        self._sequence += 1
        self._head = entry.entry_hash
        return entry


_platform: Platform | None = None


def get_platform() -> Iterator[Platform]:
    global _platform
    if _platform is None:
        _platform = Platform()
    yield _platform


def principal(
    platform: Annotated[Platform, Depends(get_platform)],
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> Principal:
    if authorization and authorization.startswith("Bearer "):
        try:
            return platform.tokens.verify(authorization[len("Bearer ") :].strip())
        except AuthError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)
            ) from error

    if x_api_key:
        with platform.database.session() as session:
            row = ApiKeyRepository(session).resolve(hash_api_key(x_api_key))
            if row is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="api key is not valid"
                )
            try:
                return Principal(
                    tenant_id=row.tenant_id,
                    subject=f"key:{row.label}",
                    roles=frozenset(row.roles),
                )
            except AuthError as error:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)
                ) from error

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="a bearer token or X-Api-Key is required; a tenant header is not authentication",
    )


PrincipalDep = Annotated[Principal, Depends(principal)]
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


@app.exception_handler(MissingContextError)
async def missing_context_handler(_: object, error: MissingContextError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "title": "missing-context",
            "detail": str(error),
            "status": 422,
            "code": "missing-context",
        },
    )


@app.exception_handler(RetryError)
async def retry_error_handler(_: object, error: RetryError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "title": "retry-refused",
            "detail": str(error),
            "status": 409,
            "code": "retry-refused",
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


@app.get("/v1/configuration")
def configuration(caller: PrincipalDep, platform: PlatformDep) -> dict[str, str]:
    return {"tenant_id": caller.tenant_id, **platform.delivery}


@app.post("/v1/auth/token")
def exchange_key_for_token(request: TokenRequest, platform: PlatformDep) -> TokenResponse:
    with platform.database.session() as session:
        row = ApiKeyRepository(session).resolve(hash_api_key(request.api_key))
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="api key is not valid"
            )
        tenant_id, label, roles = row.tenant_id, row.label, list(row.roles)

    try:
        subject = f"key:{label}"
        token = platform.tokens.mint(tenant_id, subject, frozenset(roles))
    except AuthError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error

    return TokenResponse(token=token, tenant_id=tenant_id, subject=subject, roles=roles)


@app.get("/v1/workflows")
def list_workflows() -> dict[str, WorkflowView]:
    return {
        name: WorkflowView(
            name=name,
            steps=[
                WorkflowStepView(
                    key=step.key,
                    action_type=str(step.action_type),
                    description=step.description,
                    requires=list(step.requires),
                    requires_context=list(step.requires_context),
                    irreversible=step.action_type in IRREVERSIBLE_ACTIONS,
                    optional=step.optional,
                )
                for step in wf.steps
            ],
            required_context=list(wf.required_context),
        )
        for name, wf in CATALOGUE.items()
    }


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
                description=run.definition.step(state.key).description,
                action_type=str(run.definition.step(state.key).action_type),
                irreversible=run.definition.step(state.key).action_type in IRREVERSIBLE_ACTIONS,
                reasons=list(state.reasons),
                approver=state.approver,
                attempts=state.attempts,
                retryable=(
                    state.status is StepStatus.FAILED and state.attempts < MAX_STEP_ATTEMPTS
                ),
            )
            for state in run.steps.values()
        ],
        pending_approvals=list(runtime.pending_approvals(run)),
        context=dict(run.context),
    )


@app.post("/v1/runs", status_code=status.HTTP_201_CREATED)
def start_run(request: StartRunRequest, caller: PrincipalDep, platform: PlatformDep) -> RunView:
    definition = CATALOGUE.get(request.workflow)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown workflow {request.workflow!r}",
        )

    with platform.database.session() as session:
        runtime = platform.runtime(session, caller.tenant_id)
        run = runtime.start(definition, caller.tenant_uuid, request.subject_id, request.context)
        runtime.advance(run)
        RunRepository(session).save(run)
        return _view(run, runtime)


@app.get("/v1/runs")
def list_runs(caller: PrincipalDep, platform: PlatformDep) -> list[RunView]:
    with platform.database.session() as session:
        runs = RunRepository(session).for_tenant(caller.tenant_id)
        runtime = platform.runtime(session, caller.tenant_id)
        return [_view(run, runtime) for run in runs]


@app.get("/v1/runs/{run_id}")
def get_run(run_id: str, caller: PrincipalDep, platform: PlatformDep) -> RunView:
    with platform.database.session() as session:
        run = RunRepository(session).load(caller.tenant_id, run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
        return _view(run, platform.runtime(session, caller.tenant_id))


def _mutate_run(
    platform: Platform, caller: Principal, run_id: str, operation: str, **kwargs: Any
) -> RunView:
    with platform.database.session() as session:
        run = RunRepository(session).load(caller.tenant_id, run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")

        runtime = platform.runtime(session, caller.tenant_id)
        handler: Any = getattr(runtime, operation)
        handler(run, **kwargs)
        RunRepository(session).save(run)
        return _view(run, runtime)


@app.post("/v1/runs/{run_id}/steps/{step_key}/approve")
def approve_step(
    run_id: str,
    step_key: str,
    request: ApprovalRequest,
    caller: PrincipalDep,
    platform: PlatformDep,
) -> RunView:
    return _mutate_run(
        platform, caller, run_id, "approve", step_key=step_key, approver=request.approver
    )


@app.post("/v1/runs/{run_id}/steps/{step_key}/reject")
def reject_step(
    run_id: str,
    step_key: str,
    request: RejectionRequest,
    caller: PrincipalDep,
    platform: PlatformDep,
) -> RunView:
    return _mutate_run(
        platform,
        caller,
        run_id,
        "reject",
        step_key=step_key,
        approver=request.approver,
        reason=request.reason,
    )


@app.post("/v1/runs/{run_id}/steps/{step_key}/retry")
def retry_step(
    run_id: str,
    step_key: str,
    request: RetryRequest,
    caller: PrincipalDep,
    platform: PlatformDep,
) -> RunView:
    return _mutate_run(
        platform,
        caller,
        run_id,
        "retry",
        step_key=step_key,
        actor=request.actor,
        amendments=request.amendments,
    )


@app.post("/v1/runs/{run_id}/steps/{step_key}/external")
def resolve_external(
    run_id: str,
    step_key: str,
    request: ExternalResultRequest,
    caller: PrincipalDep,
    platform: PlatformDep,
) -> RunView:
    return _mutate_run(
        platform,
        caller,
        run_id,
        "resolve_external",
        step_key=step_key,
        result=request.result,
        succeeded=request.succeeded,
    )


@app.post("/v1/anonymize")
def anonymize(
    request: AnonymizeRequest, caller: PrincipalDep, platform: PlatformDep
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


@app.post("/v1/screen")
def screen_candidate(
    request: ScreenRequest, caller: PrincipalDep, platform: PlatformDep
) -> ScreeningView:
    try:
        result = platform.screener().screen(request.record, request.requirement)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error

    return ScreeningView(
        subject_key=result.subject_key,
        score=round(result.score, 4),
        recommendation=result.recommendation,
        rationale=result.rationale,
        signals_considered=list(result.signals_considered),
        model=result.model,
        prompt_fingerprint=result.prompt_fingerprint,
    )


@app.post("/v1/bias/adverse-impact")
def adverse_impact(request: AdverseImpactRequest, caller: PrincipalDep) -> AdverseImpactResponse:
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
    request: TrainRequest, caller: PrincipalDep, platform: PlatformDep
) -> TrainResponse:
    if len(request.employees) != len(request.left):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="employees and outcomes must be the same length",
        )

    model = AttritionModel(request.algorithm)
    report = model.train([_snapshot(item) for item in request.employees], request.left)

    with platform.database.session() as session:
        ModelRepository(session).save(
            caller.tenant_id, model, report.rows, report.positives, report.feature_importance
        )

    return TrainResponse(
        rows=report.rows,
        positives=report.positives,
        positive_rate=report.positive_rate,
        algorithm=report.algorithm,
        feature_importance=dict(report.feature_importance),
    )


@app.get("/v1/attrition/model")
def model_status(caller: PrincipalDep, platform: PlatformDep) -> ModelStatusView:
    with platform.database.session() as session:
        row = ModelRepository(session).describe(caller.tenant_id)
        if row is None:
            return ModelStatusView(trained=False)
        return ModelStatusView(
            trained=True,
            algorithm=row.algorithm,
            rows=row.rows,
            positives=row.positives,
            trained_at=row.trained_at.isoformat(),
            feature_importance=dict(row.feature_importance),
        )


@app.post("/v1/attrition/score")
def score_employees(
    request: ScoreRequest, caller: PrincipalDep, platform: PlatformDep
) -> list[AttritionScoreView]:
    with platform.database.session() as session:
        model = ModelRepository(session).load(caller.tenant_id)

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
def read_ledger(caller: PrincipalDep, platform: PlatformDep) -> list[LedgerEntryView]:
    with platform.database.session() as session:
        entries = LedgerRepository(session).entries(caller.tenant_id)

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
        for entry in entries
    ]


@app.get("/v1/ledger/verify")
def verify_ledger(caller: PrincipalDep, platform: PlatformDep) -> IntegrityView:
    with platform.database.session() as session:
        report = LedgerRepository(session).verify(caller.tenant_id)

    return IntegrityView(
        intact=report.intact,
        entries_checked=report.entries_checked,
        broken_at=report.broken_at,
        reason=report.reason,
    )
