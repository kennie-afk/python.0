from __future__ import annotations

import csv
import io
import logging
import os
import uuid
from collections.abc import Iterator, Sequence
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.exc import IntegrityError
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
    OverviewView,
    RejectionRequest,
    RetryRequest,
    RunView,
    ScoreRequest,
    ScreeningView,
    ScreenRequest,
    StartRunRequest,
    StepView,
    StoredScoreView,
    StoredScreeningView,
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
from aegis.persistence.models import ImpactReportRow
from aegis.persistence.repositories import (
    ApiKeyRepository,
    ImpactReportRepository,
    LedgerRepository,
    ModelRepository,
    PolicyRepository,
    RiskScoreRepository,
    RunRepository,
    ScreeningRepository,
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

MIN_SALT_LENGTH = 16
MIN_SIGNING_SECRET_LENGTH = 32

def _required_secret(name: str, minimum: int) -> str:
    value = os.environ.get(name, "")
    if len(value) < minimum:
        raise RuntimeError(
            f"{name} must be set to at least {minimum} characters before Aegis will start; "
            "a default would let anyone reproduce every pseudonym and forge every token"
        )
    return value

class Platform:
    def __init__(self, database: Database | None = None, model: LanguageModel | None = None):
        self.database = database or Database()
        if self.database.is_sqlite:
            # Postgres schema is versioned through Alembic (see migrations/) and
            # created by `alembic upgrade head` before this process starts; only
            # the ephemeral sqlite databases tests use get their schema implicitly.
            self.database.create_all()
        self.salt = _required_secret("AEGIS_ANONYMISATION_SALT", MIN_SALT_LENGTH)
        self.tokens = TokenService(
            secret=_required_secret("AEGIS_JWT_SECRET", MIN_SIGNING_SECRET_LENGTH)
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

_LEDGER_APPEND_RETRIES = 5

class PersistentLedger(DecisionLedger):
    def __init__(self, session: Session, tenant: str) -> None:
        super().__init__()
        self._tenant = tenant
        self._session = session
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
        # Two concurrent requests for the same tenant can both cache the same
        # (sequence, previous_hash) pair and race to insert it. Re-read the head
        # fresh on every attempt and retry on the unique-constraint conflict
        # rather than trusting the value cached at construction time - that
        # cached value is what a concurrent sibling request may have already
        # invalidated by the time this append actually runs.
        self._repository.lock_tenant(tenant_id)
        last_error: IntegrityError | None = None
        for _ in range(_LEDGER_APPEND_RETRIES):
            sequence, previous_hash = self._repository.head(tenant_id)
            entry = make_entry(
                sequence=sequence,
                previous_hash=previous_hash,
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
            try:
                with self._session.begin_nested():
                    self._repository.append(self._tenant, entry)
            except IntegrityError as error:
                last_error = error
                continue
            self._sequence = sequence + 1
            self._head = entry.entry_hash
            return entry
        raise RuntimeError(
            f"could not append to {tenant_id!r} ledger after "
            f"{_LEDGER_APPEND_RETRIES} attempts: contention on the sequence"
        ) from last_error

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

_RUNS_PAGE_DEFAULT = 100
_RUNS_PAGE_MAX = 500

@app.get("/v1/runs")
def list_runs(
    caller: PrincipalDep,
    platform: PlatformDep,
    response: Response,
    limit: int = _RUNS_PAGE_DEFAULT,
    offset: int = 0,
    workflow: str | None = None,
    q: str | None = Query(default=None, max_length=100, description="subject contains"),
    needs: str | None = Query(
        default=None, pattern="^(approval|failed|external)$", description="runs blocked on this"
    ),
) -> list[RunView]:
    """Newest first. Filters run in the database; `X-Total-Count` is the number that matched."""
    bounded_limit = max(1, min(limit, _RUNS_PAGE_MAX))
    with platform.database.session() as session:
        runs, total = RunRepository(session).search(
            caller.tenant_id,
            workflow=workflow,
            query=q,
            needs=needs,
            limit=bounded_limit,
            offset=max(0, offset),
        )
        runtime = platform.runtime(session, caller.tenant_id)
        response.headers["X-Total-Count"] = str(total)
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

    # Only the pseudonymous key and the verdict are kept; the record itself never is.
    with platform.database.session() as session:
        ScreeningRepository(session).add(
            caller.tenant_id,
            subject_key=result.subject_key,
            requirement=request.requirement,
            score=round(result.score, 4),
            recommendation=result.recommendation,
            rationale=result.rationale,
            signals=result.signals_considered,
            model=result.model,
            prompt_fingerprint=result.prompt_fingerprint,
        )

    return ScreeningView(
        subject_key=result.subject_key,
        score=round(result.score, 4),
        recommendation=result.recommendation,
        rationale=result.rationale,
        signals_considered=list(result.signals_considered),
        model=result.model,
        prompt_fingerprint=result.prompt_fingerprint,
    )

@app.get("/v1/screenings")
def list_screenings(
    caller: PrincipalDep,
    platform: PlatformDep,
    response: Response,
    recommendation: str | None = Query(default=None, pattern="^(ADVANCE|REVIEW|HOLD)$"),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[StoredScreeningView]:
    with platform.database.session() as session:
        rows, total = ScreeningRepository(session).search(
            caller.tenant_id, recommendation=recommendation, query=q, limit=limit, offset=offset
        )
        response.headers["X-Total-Count"] = str(total)
        return [
            StoredScreeningView(
                id=row.id,
                subject_key=row.subject_key,
                requirement=row.requirement,
                score=row.score,
                recommendation=row.recommendation,
                rationale=row.rationale,
                signals_considered=list(row.signals),
                model=row.model,
                prompt_fingerprint=row.prompt_fingerprint,
                screened_at=row.created_at.isoformat(),
            )
            for row in rows
        ]

_IMPACT_OUTCOME = {
    "ADVERSE_IMPACT": "FLAGGED",
    "NO_ADVERSE_IMPACT": "PASSED",
    "INSUFFICIENT_DATA": "INSUFFICIENT_DATA",
}

def _impact_view(row: ImpactReportRow) -> AdverseImpactResponse:
    return AdverseImpactResponse(
        report_id=row.id,
        label=row.label,
        ledger_sequence=row.ledger_sequence,
        recorded_at=row.created_at.isoformat(),
        minimum_group_size=row.minimum_group_size,
        verdict=row.verdict,
        reference_group=row.reference_group,
        reference_rate=row.reference_rate,
        groups=[GroupImpactView(**group) for group in row.groups],
        p_value=row.p_value,
        summary=row.summary,
    )

@app.post("/v1/bias/adverse-impact")
def adverse_impact(
    request: AdverseImpactRequest, caller: PrincipalDep, platform: PlatformDep
) -> AdverseImpactResponse:
    report = four_fifths_test(
        [
            GroupOutcome(group=item.group, selected=item.selected, total=item.total)
            for item in request.outcomes
        ],
        minimum_group_size=request.minimum_group_size,
    )

    groups = [
        GroupImpactView(
            group=group.group,
            selection_rate=group.selection_rate,
            impact_ratio=group.impact_ratio,
            total=group.total,
            selected=group.selected,
            adversely_impacted=group.adversely_impacted,
        )
        for group in report.groups
    ]

    # A finding is evidence, so it is stored and its verdict is written into the hash chain:
    # a flagged analysis cannot later be quietly deleted without breaking the audit trail.
    with platform.database.session() as session:
        verdict = str(report.verdict)
        ledger = PersistentLedger(session, caller.tenant_id)
        entry = ledger.append(
            tenant_id=caller.tenant_id,
            workflow="compliance",
            run_id=str(uuid.uuid4()),
            step="adverse_impact_test",
            action_type="ADVERSE_IMPACT_TEST",
            subject_id=request.label,
            agent="aegis-compliance",
            outcome=_IMPACT_OUTCOME.get(verdict, verdict),
            reasons=(report.summary(),),
            approver=None,
        )
        row = ImpactReportRepository(session).add(
            ImpactReportRow(
                tenant_id=caller.tenant_id,
                label=request.label,
                verdict=verdict,
                reference_group=report.reference_group,
                reference_rate=report.reference_rate,
                p_value=report.p_value,
                minimum_group_size=request.minimum_group_size,
                groups=[group.model_dump() for group in groups],
                summary=report.summary(),
                ledger_sequence=entry.sequence,
            )
        )
        return _impact_view(row)

@app.get("/v1/bias/reports")
def list_impact_reports(
    caller: PrincipalDep,
    platform: PlatformDep,
    response: Response,
    verdict: str | None = Query(
        default=None, pattern="^(ADVERSE_IMPACT|NO_ADVERSE_IMPACT|INSUFFICIENT_DATA)$"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[AdverseImpactResponse]:
    with platform.database.session() as session:
        rows, total = ImpactReportRepository(session).search(
            caller.tenant_id, verdict=verdict, limit=limit, offset=offset
        )
        response.headers["X-Total-Count"] = str(total)
        return [_impact_view(row) for row in rows]

@app.get("/v1/bias/reports/{report_id}")
def get_impact_report(
    report_id: int, caller: PrincipalDep, platform: PlatformDep
) -> AdverseImpactResponse:
    with platform.database.session() as session:
        row = ImpactReportRepository(session).get(caller.tenant_id, report_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report not found")
        return _impact_view(row)

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
    views = [
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

    with platform.database.session() as session:
        risks = RiskScoreRepository(session)
        for view in views:
            risks.upsert(
                caller.tenant_id,
                view.subject_key,
                view.probability,
                view.band,
                view.needs_intervention,
                [driver.model_dump() for driver in view.drivers],
            )
    return views

@app.get("/v1/attrition/scores")
def list_scores(
    caller: PrincipalDep,
    platform: PlatformDep,
    response: Response,
    band: str | None = Query(default=None, pattern="^(LOW|MEDIUM|HIGH)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[StoredScoreView]:
    """The latest score for each employee, highest risk first."""
    with platform.database.session() as session:
        rows, total = RiskScoreRepository(session).search(
            caller.tenant_id, band=band, limit=limit, offset=offset
        )
        response.headers["X-Total-Count"] = str(total)
        return [
            StoredScoreView(
                subject_key=row.subject_key,
                probability=row.probability,
                band=row.band,
                needs_intervention=row.needs_intervention,
                drivers=[DriverView(**driver) for driver in row.drivers],
                scored_at=row.scored_at.isoformat(),
            )
            for row in rows
        ]

_LEDGER_PAGE_DEFAULT = 200
_LEDGER_PAGE_MAX = 1000

@app.get("/v1/ledger")
def read_ledger(
    caller: PrincipalDep,
    platform: PlatformDep,
    after: int | None = None,
    limit: int = _LEDGER_PAGE_DEFAULT,
) -> list[LedgerEntryView]:
    bounded_limit = max(1, min(limit, _LEDGER_PAGE_MAX))
    with platform.database.session() as session:
        entries = LedgerRepository(session).entries(
            caller.tenant_id, after_sequence=after, limit=bounded_limit
        )

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


def _ledger_view(entry: LedgerEntry) -> LedgerEntryView:
    return LedgerEntryView(
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

@app.get("/v1/ledger/search")
def search_ledger(
    caller: PrincipalDep,
    platform: PlatformDep,
    response: Response,
    outcome: str | None = Query(default=None, max_length=40),
    actor: str | None = Query(default=None, pattern="^(people|agents)$"),
    workflow: str | None = Query(default=None, max_length=100),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[LedgerEntryView]:
    """Newest first, filtered and paged in the database; `X-Total-Count` is the match count."""
    with platform.database.session() as session:
        entries, total = LedgerRepository(session).search(
            caller.tenant_id,
            outcome=outcome,
            actor=actor,
            query=q,
            workflow=workflow,
            limit=limit,
            offset=offset,
        )
    response.headers["X-Total-Count"] = str(total)
    return [_ledger_view(entry) for entry in entries]

_EXPORT_COLUMNS = (
    "sequence",
    "recorded_at",
    "workflow",
    "step",
    "action_type",
    "subject_id",
    "agent",
    "outcome",
    "approver",
    "reasons",
    "previous_hash",
    "entry_hash",
)

def _spreadsheet_safe(value: str) -> str:
    # A cell starting with = + - @ is executed as a formula by spreadsheet software.
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value

@app.get("/v1/ledger/export")
def export_ledger(caller: PrincipalDep, platform: PlatformDep) -> StreamingResponse:
    """The whole audit trail as CSV, including each entry's hashes so it can be re-verified
    offline by someone who does not trust this service."""

    def rows() -> Iterator[str]:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(_EXPORT_COLUMNS)
        yield buffer.getvalue()
        with platform.database.session() as session:
            for entry in LedgerRepository(session).stream(caller.tenant_id):
                buffer.seek(0)
                buffer.truncate()
                writer.writerow(
                    [
                        entry.sequence,
                        entry.recorded_at.isoformat(),
                        entry.workflow,
                        entry.step,
                        entry.action_type,
                        _spreadsheet_safe(entry.subject_id),
                        entry.agent,
                        entry.outcome,
                        _spreadsheet_safe(entry.approver or ""),
                        _spreadsheet_safe(" | ".join(entry.reasons)),
                        entry.previous_hash,
                        entry.entry_hash,
                    ]
                )
                yield buffer.getvalue()

    return StreamingResponse(
        rows(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="aegis-audit-trail.csv"'},
    )

@app.get("/v1/overview")
def overview(caller: PrincipalDep, platform: PlatformDep) -> OverviewView:
    """Counts for the console's front page, computed in the database rather than by fetching
    every run and ledger entry and counting them in the browser."""
    with platform.database.session() as session:
        runs = RunRepository(session).counts(caller.tenant_id)
        ledger = LedgerRepository(session).counts(caller.tenant_id)
        return OverviewView(
            runs=runs["total"],
            awaiting_approval=runs["awaiting_approval"],
            awaiting_external=runs["awaiting_external"],
            failed=runs["failed"],
            ledger_entries=ledger["total"],
            human_decisions=ledger["approvals"],
            screenings=ScreeningRepository(session).by_recommendation(caller.tenant_id),
            impact_reports=ImpactReportRepository(session).by_verdict(caller.tenant_id),
            retention_bands=RiskScoreRepository(session).by_band(caller.tenant_id),
            model_trained=ModelRepository(session).describe(caller.tenant_id) is not None,
        )
