from __future__ import annotations

import csv
import io
import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from itertools import islice
from typing import Annotated, Any

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
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
    IssuedKeyView,
    KeyCreateRequest,
    KeyView,
    LedgerEntryView,
    LedgerHeadView,
    ModelStatusView,
    ModelVersionView,
    OverviewView,
    RejectionRequest,
    RetryRequest,
    RunView,
    ScoreHistoryView,
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
from aegis.attrition.features import FEATURE_NAMES, EmployeeSnapshot
from aegis.attrition.model import AttritionModel, ModelError
from aegis.auth.tokens import AuthError, Principal, TokenService, generate_api_key, hash_api_key
from aegis.bias.adverse_impact import (
    AdverseImpactError,
    GroupOutcome,
    four_fifths_test,
)
from aegis.governance.actions import IRREVERSIBLE_ACTIONS, ActionType
from aegis.governance.gate import GovernanceGate
from aegis.governance.policy import TenantPolicy
from aegis.hr.workflows import CATALOGUE
from aegis.integrations.calendar import CalendarTool, PersistentCalendar
from aegis.integrations.email import (
    EmailError,
    EmailTool,
    EmailTransport,
    MockEmailTransport,
    SmtpEmailTransport,
)
from aegis.ledger.evidence import VERIFIER_SOURCE, build_evidence_pack
from aegis.ledger.record import (
    GENESIS,
    DecisionLedger,
    LedgerEntry,
    head_signature,
    make_entry,
)
from aegis.ops.alerts import AlertDispatcher
from aegis.persistence.models import ImpactReportRow, ModelVersionRow
from aegis.persistence.repositories import (
    AlertRepository,
    ApiKeyRepository,
    ImpactReportRepository,
    LedgerRepository,
    ModelIntegrityError,
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
from aegis.security.signing import (
    is_production,
    key_fingerprint,
    ledger_signing_key,
)
from aegis.verification.fidelity import Gate
from aegis.verification.model_gate import assess_model, feature_drift

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
        self.database.assert_runtime_role_is_safe()
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
        # Interview slots live in the database (per tenant); this names the implementation.
        self.calendar = PersistentCalendar
        self.email = _configured_email()
        # None when signing is off; an error here, at start-up, in production without a key.
        self.ledger_key = ledger_signing_key()
        self.alerts = AlertDispatcher.from_environment(self.database, self.email)

    @property
    def delivery(self) -> dict[str, str]:
        return {
            "model": self.model.name,
            "email": type(self.email).__name__,
            "calendar": self.calendar.__name__,
        }

    def policy(self, session: Session, tenant: str) -> TenantPolicy:
        stored = PolicyRepository(session).load(tenant)
        return stored or TenantPolicy.conservative(tenant)

    def runtime(self, session: Session, tenant: str) -> AgentRuntime:
        tools = ToolRegistry()
        tools.register(EmailTool(self.email))
        tools.register(CalendarTool(PersistentCalendar(session, tenant)))
        remaining = frozenset(ActionType) - tools.registered()
        tools.register(RecordingTool(remaining, output={"executed": True}))

        return AgentRuntime(
            gate=GovernanceGate(self.policy(session, tenant)),
            tools=tools,
            ledger=PersistentLedger(session, tenant, self.ledger_key),
        )

    def anonymizer(self) -> AnonymizationEngine:
        return AnonymizationEngine(salt=self.salt)

    def screener(self) -> CandidateScreener:
        return CandidateScreener(self.model, self.anonymizer())

_LEDGER_APPEND_RETRIES = 5

class PersistentLedger(DecisionLedger):
    def __init__(self, session: Session, tenant: str, signing_key: bytes | None = None) -> None:
        super().__init__(signing_key)
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
                signing_key=self._signing_key,
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
_platform_lock = threading.Lock()

def build_platform() -> Platform:
    """Create the process-wide platform once. Two first requests arriving together must not each
    build one (two connection pools, two sets of secrets read), so creation is under a lock."""
    global _platform
    if _platform is None:
        with _platform_lock:
            if _platform is None:
                _platform = Platform()
    return _platform

def get_platform() -> Iterator[Platform]:
    yield build_platform()

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Built at start-up, so a missing secret or an unsafe database role stops the process before it
    # takes traffic, not on the first request. Overridden platforms (tests) are left alone.
    if get_platform not in app.dependency_overrides:
        build_platform()
    yield

def _check_token_key(platform: Platform, token: Principal) -> None:
    """A token issued from an API key is only as good as that key: revoking the key, or setting
    its not_before, ends every token it already issued, without waiting for them to expire."""
    if token.key_id is None:
        return
    with platform.database.session() as session:
        state = ApiKeyRepository(session).state(token.key_id)
    revoked = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="token has been revoked"
    )
    if state is None:
        raise revoked
    tenant, active, not_before = state
    if tenant != token.tenant_id or not active:
        raise revoked
    if not_before is not None and token.issued_at is not None:
        floor = not_before if not_before.tzinfo else not_before.replace(tzinfo=UTC)
        if token.issued_at < floor:
            raise revoked

def principal(
    platform: Annotated[Platform, Depends(get_platform)],
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> Principal:
    if authorization and authorization.startswith("Bearer "):
        try:
            token_principal = platform.tokens.verify(authorization[len("Bearer ") :].strip())
        except AuthError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)
            ) from error
        _check_token_key(platform, token_principal)
        return token_principal

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

# Roles. ADMIN may do everything; a principal with no role may do nothing, so a key issued
# without one is useless rather than all-powerful. Role names are matched case-insensitively.
READ_ROLES = frozenset({"VIEWER", "OPERATOR", "APPROVER", "AUDITOR"})
OPERATE_ROLES = frozenset({"OPERATOR"})
APPROVE_ROLES = frozenset({"APPROVER"})
AUDIT_ROLES = frozenset({"AUDITOR", "APPROVER"})

def requires(allowed: frozenset[str]) -> Callable[[Principal], Principal]:
    def check(caller: PrincipalDep) -> Principal:
        held = {role.upper() for role in caller.roles}
        if "ADMIN" in held or held & allowed:
            return caller
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"this needs one of: {', '.join(sorted({'ADMIN', *allowed}))}",
        )

    return check

ALL_ROLES = frozenset({"ADMIN", "OPERATOR", "APPROVER", "AUDITOR", "VIEWER"})
AdminDep = Annotated[Principal, Depends(requires(frozenset()))]
ReaderDep = Annotated[Principal, Depends(requires(READ_ROLES))]
OperatorDep = Annotated[Principal, Depends(requires(OPERATE_ROLES))]
ApproverDep = Annotated[Principal, Depends(requires(APPROVE_ROLES))]
AuditorDep = Annotated[Principal, Depends(requires(AUDIT_ROLES))]

def _own_identity(caller: Principal, claimed: str | None, what: str) -> str:
    if claimed is not None and claimed != caller.subject:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"the {what} is the signed-in identity ({caller.subject}); it cannot be set",
        )
    return caller.subject

app = FastAPI(
    title="Aegis",
    version="0.1.0",
    description="HR automation platform with structural governance",
    lifespan=lifespan,
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
def configuration(caller: ReaderDep, platform: PlatformDep) -> dict[str, str]:
    return {"tenant_id": caller.tenant_id, **platform.delivery}

class _FailureLimiter:
    """Sliding window of failed sign-ins per client address. In process memory, so with several
    replicas each keeps its own count; behind a proxy run uvicorn with --proxy-headers so the
    address is the caller's and not the proxy's."""

    def __init__(self) -> None:
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    @staticmethod
    def limit() -> int:
        return int(os.environ.get("AEGIS_TOKEN_FAILURES_PER_MINUTE", "10"))

    def check(self, client: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._failures[client]
            while hits and now - hits[0] > 60.0:
                hits.popleft()
            if len(hits) >= self.limit():
                retry = max(1, int(60.0 - (now - hits[0])) + 1)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="too many failed sign-in attempts; wait and try again",
                    headers={"Retry-After": str(retry)},
                )

    def record_failure(self, client: str) -> None:
        with self._lock:
            self._failures[client].append(time.monotonic())

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()

token_failures = _FailureLimiter()

@app.post("/v1/auth/token")
def exchange_key_for_token(
    request: TokenRequest, http: Request, platform: PlatformDep
) -> TokenResponse:
    client = http.client.host if http.client else "unknown"
    token_failures.check(client)
    with platform.database.session() as session:
        row = ApiKeyRepository(session).resolve(hash_api_key(request.api_key))
        if row is None:
            token_failures.record_failure(client)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="api key is not valid"
            )
        tenant_id, label, roles, key_id = row.tenant_id, row.label, list(row.roles), row.key_id

    try:
        subject = f"key:{label}"
        token = platform.tokens.mint(tenant_id, subject, frozenset(roles), key_id=key_id)
    except AuthError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error

    return TokenResponse(token=token, tenant_id=tenant_id, subject=subject, roles=roles)

def _key_view(row: Any) -> KeyView:
    return KeyView(
        key_id=row.key_id,
        label=row.label,
        roles=list(row.roles),
        active=bool(row.active),
        created_at=row.created_at.isoformat(),
        created_by=row.created_by,
        revoked_at=row.revoked_at.isoformat() if row.revoked_at else None,
        tokens_valid_from=row.not_before.isoformat() if row.not_before else None,
    )

def _record_access_event(
    session: Session, caller: Principal, step: str, action: str, subject: str, outcome: str,
    platform: Platform, reason: str,
) -> None:
    PersistentLedger(session, caller.tenant_id, platform.ledger_key).append(
        tenant_id=caller.tenant_id, workflow="access", run_id=str(uuid.uuid4()), step=step,
        action_type=action, subject_id=subject, agent="aegis-access", outcome=outcome,
        reasons=(reason,), approver=caller.subject,
    )

def _clean_roles(roles: list[str]) -> list[str]:
    cleaned = sorted({role.strip().upper() for role in roles if role.strip()})
    unknown = [role for role in cleaned if role not in ALL_ROLES]
    if unknown or not cleaned:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"roles must be drawn from {sorted(ALL_ROLES)}; got {unknown or 'none'}",
        )
    return cleaned

def _would_remove_last_admin(session: Session, tenant: str, key_id: str) -> bool:
    keys = ApiKeyRepository(session).list_for_tenant(tenant)
    others = [
        k for k in keys
        if k.key_id != key_id and k.active and "ADMIN" in {r.upper() for r in k.roles}
    ]
    target = next((k for k in keys if k.key_id == key_id), None)
    return bool(
        target and target.active and "ADMIN" in {r.upper() for r in target.roles} and not others
    )

@app.post("/v1/keys", status_code=status.HTTP_201_CREATED)
def create_key(
    request: KeyCreateRequest, caller: AdminDep, platform: PlatformDep
) -> IssuedKeyView:
    roles = _clean_roles(request.roles)
    secret = generate_api_key()
    with platform.database.session(caller.tenant_id) as session:
        row = ApiKeyRepository(session).issue(
            caller.tenant_id, request.label, hash_api_key(secret), roles, caller.subject
        )
        _record_access_event(
            session, caller, "key_issue", "API_KEY_ISSUED", request.label, "ISSUED", platform,
            f"key {row.key_id} issued with roles {', '.join(roles)}",
        )
        return IssuedKeyView(**_key_view(row).model_dump(), api_key=secret)

@app.get("/v1/keys")
def list_keys(caller: AdminDep, platform: PlatformDep) -> list[KeyView]:
    with platform.database.session(caller.tenant_id) as session:
        rows = ApiKeyRepository(session).list_for_tenant(caller.tenant_id)
        return [_key_view(row) for row in rows]

@app.post("/v1/keys/{key_id}/rotate", status_code=status.HTTP_201_CREATED)
def rotate_key(key_id: str, caller: AdminDep, platform: PlatformDep) -> IssuedKeyView:
    """Issue a replacement with the same label and roles and revoke the old key, whose tokens stop
    working at once."""
    secret = generate_api_key()
    with platform.database.session(caller.tenant_id) as session:
        repository = ApiKeyRepository(session)
        old = repository.by_key_id(caller.tenant_id, key_id)
        if old is None or not old.active:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such active key")
        new = repository.issue(
            caller.tenant_id, old.label, hash_api_key(secret), list(old.roles), caller.subject
        )
        repository.revoke_by_id(caller.tenant_id, key_id)
        _record_access_event(
            session, caller, "key_rotate", "API_KEY_ROTATED", old.label, "ROTATED", platform,
            f"key {key_id} replaced by {new.key_id}",
        )
        return IssuedKeyView(**_key_view(new).model_dump(), api_key=secret)

@app.post("/v1/keys/{key_id}/revoke")
def revoke_key(key_id: str, caller: AdminDep, platform: PlatformDep) -> KeyView:
    with platform.database.session(caller.tenant_id) as session:
        if _would_remove_last_admin(session, caller.tenant_id, key_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="that is the last active ADMIN key; create or rotate another first",
            )
        row = ApiKeyRepository(session).revoke_by_id(caller.tenant_id, key_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such key")
        _record_access_event(
            session, caller, "key_revoke", "API_KEY_REVOKED", row.label, "REVOKED", platform,
            f"key {key_id} revoked",
        )
        return _key_view(row)

@app.post("/v1/keys/{key_id}/revoke-tokens")
def revoke_key_tokens(key_id: str, caller: AdminDep, platform: PlatformDep) -> KeyView:
    """End every token already issued from this key, keeping the key itself usable."""
    with platform.database.session(caller.tenant_id) as session:
        row = ApiKeyRepository(session).invalidate_tokens(caller.tenant_id, key_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such key")
        _record_access_event(
            session, caller, "token_revoke", "API_TOKENS_REVOKED", row.label, "REVOKED", platform,
            f"tokens issued from key {key_id} before now were revoked",
        )
        return _key_view(row)

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
def start_run(request: StartRunRequest, caller: OperatorDep, platform: PlatformDep) -> RunView:
    definition = CATALOGUE.get(request.workflow)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown workflow {request.workflow!r}",
        )

    with platform.database.session(caller.tenant_id) as session:
        runtime = platform.runtime(session, caller.tenant_id)
        run = runtime.start(definition, caller.tenant_uuid, request.subject_id, request.context)
        runtime.advance(run)
        RunRepository(session).save(run)
        return _view(run, runtime)

_RUNS_PAGE_DEFAULT = 100
_RUNS_PAGE_MAX = 500

@app.get("/v1/runs")
def list_runs(
    caller: ReaderDep,
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
    with platform.database.session(caller.tenant_id) as session:
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
def get_run(run_id: str, caller: ReaderDep, platform: PlatformDep) -> RunView:
    with platform.database.session(caller.tenant_id) as session:
        run = RunRepository(session).load(caller.tenant_id, run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
        return _view(run, platform.runtime(session, caller.tenant_id))

def _mutate_run(
    platform: Platform, caller: Principal, run_id: str, operation: str, **kwargs: Any
) -> RunView:
    with platform.database.session(caller.tenant_id) as session:
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
    caller: ApproverDep,
    platform: PlatformDep,
) -> RunView:
    return _mutate_run(
        platform,
        caller,
        run_id,
        "approve",
        step_key=step_key,
        approver=_own_identity(caller, request.approver, "approver"),
    )

@app.post("/v1/runs/{run_id}/steps/{step_key}/reject")
def reject_step(
    run_id: str,
    step_key: str,
    request: RejectionRequest,
    caller: ApproverDep,
    platform: PlatformDep,
) -> RunView:
    return _mutate_run(
        platform,
        caller,
        run_id,
        "reject",
        step_key=step_key,
        approver=_own_identity(caller, request.approver, "approver"),
        reason=request.reason,
    )

@app.post("/v1/runs/{run_id}/steps/{step_key}/retry")
def retry_step(
    run_id: str,
    step_key: str,
    request: RetryRequest,
    caller: OperatorDep,
    platform: PlatformDep,
) -> RunView:
    return _mutate_run(
        platform,
        caller,
        run_id,
        "retry",
        step_key=step_key,
        actor=_own_identity(caller, request.actor, "actor"),
        amendments=request.amendments,
    )

@app.post("/v1/runs/{run_id}/steps/{step_key}/external")
def resolve_external(
    run_id: str,
    step_key: str,
    request: ExternalResultRequest,
    caller: OperatorDep,
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
    request: AnonymizeRequest, caller: OperatorDep, platform: PlatformDep
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
    request: ScreenRequest, caller: OperatorDep, platform: PlatformDep
) -> ScreeningView:
    try:
        result = platform.screener().screen(request.record, request.requirement)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error

    # Only the pseudonymous key and the verdict are kept; the record itself never is.
    with platform.database.session(caller.tenant_id) as session:
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
    caller: ReaderDep,
    platform: PlatformDep,
    response: Response,
    recommendation: str | None = Query(default=None, pattern="^(ADVANCE|REVIEW|HOLD)$"),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[StoredScreeningView]:
    with platform.database.session(caller.tenant_id) as session:
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
    request: AdverseImpactRequest, caller: OperatorDep, platform: PlatformDep
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
    with platform.database.session(caller.tenant_id) as session:
        verdict = str(report.verdict)
        ledger = PersistentLedger(session, caller.tenant_id, platform.ledger_key)
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
    caller: ReaderDep,
    platform: PlatformDep,
    response: Response,
    verdict: str | None = Query(
        default=None, pattern="^(ADVERSE_IMPACT|NO_ADVERSE_IMPACT|INSUFFICIENT_DATA)$"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[AdverseImpactResponse]:
    with platform.database.session(caller.tenant_id) as session:
        rows, total = ImpactReportRepository(session).search(
            caller.tenant_id, verdict=verdict, limit=limit, offset=offset
        )
        response.headers["X-Total-Count"] = str(total)
        return [_impact_view(row) for row in rows]

@app.get("/v1/bias/reports/{report_id}")
def get_impact_report(
    report_id: int, caller: ReaderDep, platform: PlatformDep
) -> AdverseImpactResponse:
    with platform.database.session(caller.tenant_id) as session:
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

_GATE_OUTCOME = {"PASS": "PASSED", "WARN": "WARNED", "BLOCK": "BLOCKED"}

def _model_ledger(
    session: Session, platform: Platform, caller: Principal, step: str, action: str,
    subject: str, outcome: str, reasons: Sequence[str], human: bool = False,
) -> LedgerEntry:
    return PersistentLedger(session, caller.tenant_id, platform.ledger_key).append(
        tenant_id=caller.tenant_id, workflow="model_governance", run_id=str(uuid.uuid4()),
        step=step, action_type=action, subject_id=subject, agent="aegis-governance",
        outcome=outcome, reasons=tuple(reasons), approver=caller.subject if human else None,
    )

def _version_view(row: ModelVersionRow) -> ModelVersionView:
    return ModelVersionView(
        version=row.version,
        algorithm=row.algorithm,
        rows=row.rows,
        positives=row.positives,
        data_hash=row.data_hash,
        gate=row.gate,
        active=bool(row.active),
        created_by=row.created_by,
        created_at=row.created_at.isoformat(),
        activated_at=row.activated_at.isoformat() if row.activated_at else None,
        feature_importance=dict(row.feature_importance or {}),
        fidelity=dict(row.fidelity or {}),
    )

def _deliver_alerts(platform: Platform, tenant: str) -> None:
    try:
        platform.alerts.deliver_pending(tenant)
    except Exception:  # delivery is best effort here; the scheduled checks retry
        logger.exception("alert delivery failed")

@app.post("/v1/attrition/train", response_model=TrainResponse)
def train_model(
    request: TrainRequest,
    caller: OperatorDep,
    platform: PlatformDep,
    background: BackgroundTasks,
) -> TrainResponse | JSONResponse:
    """Train, then put the model through the governance gate before it can serve.

    The result is stored either way as a new version, so the record shows what was refused. PASS
    and WARN activate it (WARN is flagged in the response and the audit trail); BLOCK stores it
    inactive and answers 409 with the reasons."""
    if len(request.employees) != len(request.left):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="employees and outcomes must be the same length",
        )
    if request.groups is not None and len(request.groups) != len(request.employees):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="groups, when given, must have one label per employee",
        )

    snapshots = [_snapshot(item) for item in request.employees]
    model = AttritionModel(request.algorithm)
    report = model.train(snapshots, request.left)

    with platform.database.session(caller.tenant_id) as session:
        models = ModelRepository(session)
        previous: AttritionModel | None = None
        previous_version: int | None = None
        current = models.active(caller.tenant_id)
        extra_notes: list[str] = []
        if current is not None:
            try:
                previous = models.load(caller.tenant_id)
                previous_version = current.version
            except ModelIntegrityError as error:
                extra_notes.append(f"the active version could not be used as a reference: {error}")

        fidelity = assess_model(
            model, snapshots, request.left, previous, previous_version,
            request.groups, request.minimum_group_size,
        )
        verdict = fidelity.to_dict()
        verdict["notes"] = [*verdict["notes"], *extra_notes]
        gate = str(fidelity.gate)
        row = models.add_version(
            caller.tenant_id, model, report.rows, report.positives, report.feature_importance,
            gate, verdict, caller.subject, activate=fidelity.gate is not Gate.BLOCK,
        )
        findings = list(fidelity.report.findings)
        _model_ledger(
            session, platform, caller, "train", "MODEL_TRAINED", f"attrition:v{row.version}",
            _GATE_OUTCOME[gate],
            [f"gate {gate} at {fidelity.report.score:.2f}", *findings[:5]],
        )
        if gate != "PASS":
            AlertRepository(session).add(
                caller.tenant_id,
                "model_blocked" if gate == "BLOCK" else "model_warned",
                f"attrition model v{row.version} was {_GATE_OUTCOME[gate].lower()} by the "
                f"governance gate: " + ("; ".join(findings) or "see the fidelity report"),
                {"version": row.version, "gate": gate, "findings": findings},
            )
        view = _version_view(row)

    if gate != "PASS":
        background.add_task(_deliver_alerts, platform, caller.tenant_id)

    if gate == "BLOCK":
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "title": "model-blocked",
                "detail": "the governance gate blocked this model; it was stored but not activated",
                "status": 409,
                "code": "model-blocked",
                "version": view.version,
                "gate": gate,
                "reasons": findings or ["fidelity score below the block threshold"],
                "fidelity": view.fidelity,
            },
        )

    return TrainResponse(
        rows=report.rows,
        positives=report.positives,
        positive_rate=report.positive_rate,
        algorithm=report.algorithm,
        feature_importance=dict(report.feature_importance),
        version=view.version,
        gate=gate,
        active=view.active,
        findings=findings,
        fidelity=view.fidelity,
    )

@app.get("/v1/attrition/model")
def model_status(caller: ReaderDep, platform: PlatformDep) -> ModelStatusView:
    with platform.database.session(caller.tenant_id) as session:
        row = ModelRepository(session).active(caller.tenant_id)
        if row is None:
            return ModelStatusView(trained=False)
        return ModelStatusView(
            trained=True,
            algorithm=row.algorithm,
            rows=row.rows,
            positives=row.positives,
            trained_at=row.created_at.isoformat(),
            feature_importance=dict(row.feature_importance or {}),
            version=row.version,
            gate=row.gate,
            data_hash=row.data_hash,
            created_by=row.created_by,
            findings=list((row.fidelity or {}).get("findings", [])),
        )

@app.get("/v1/attrition/models")
def list_models(caller: ReaderDep, platform: PlatformDep) -> list[ModelVersionView]:
    """Every version trained for this tenant, newest first, with its gate verdict."""
    with platform.database.session(caller.tenant_id) as session:
        return [_version_view(row) for row in ModelRepository(session).versions(caller.tenant_id)]

@app.post("/v1/attrition/models/{version}/activate")
def activate_model(version: int, caller: ApproverDep, platform: PlatformDep) -> ModelVersionView:
    """Make a stored version the one that serves. A person does this, and it is on the ledger.
    A BLOCKed version cannot be activated, and neither can one whose signature fails."""
    with platform.database.session(caller.tenant_id) as session:
        models = ModelRepository(session)
        target = models.get(caller.tenant_id, version)
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such version")
        try:
            row = models.activate(caller.tenant_id, version)
        except ModelIntegrityError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ModelError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        _model_ledger(
            session, platform, caller, "activate", "MODEL_ACTIVATED", f"attrition:v{version}",
            "ACTIVATED", [f"{caller.subject} activated v{version} (gate {row.gate})"], human=True,
        )
        return _version_view(row)

@app.post("/v1/attrition/models/rollback")
def rollback_model(caller: ApproverDep, platform: PlatformDep) -> ModelVersionView:
    """Re-activate the newest older version that passed its gate."""
    with platform.database.session(caller.tenant_id) as session:
        models = ModelRepository(session)
        current = models.active(caller.tenant_id)
        previous = models.previous_eligible(caller.tenant_id)
        if previous is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="there is no earlier version that is allowed to serve",
            )
        row = models.activate(caller.tenant_id, previous.version)
        _model_ledger(
            session, platform, caller, "rollback", "MODEL_ROLLED_BACK", f"attrition:v{row.version}",
            "ROLLED_BACK",
            [f"{caller.subject} rolled back from v{current.version if current else '-'} "
             f"to v{row.version}"],
            human=True,
        )
        return _version_view(row)

@app.post("/v1/attrition/score")
def score_employees(
    request: ScoreRequest, caller: OperatorDep, platform: PlatformDep, response: Response
) -> list[AttritionScoreView]:
    with platform.database.session(caller.tenant_id) as session:
        models = ModelRepository(session)
        active = models.active(caller.tenant_id)
        try:
            model = models.load(caller.tenant_id)
        except ModelIntegrityError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        version = active.version if active else None
        gate = active.gate if active else None

    if model is None or active is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no attrition model has been trained for this tenant",
        )
    if gate == "BLOCK":
        # An active BLOCK version should be impossible; refusing here is the last line.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"model v{version} was blocked by the governance gate and cannot score",
        )

    snapshots = [_snapshot(item) for item in request.employees]
    scores = model.score_all(snapshots)
    matrix = model.features_matrix(snapshots)
    response.headers["X-Aegis-Model-Version"] = str(version)
    response.headers["X-Aegis-Gate"] = str(gate)
    # PSI on a handful of rows is mostly noise, so a batch is only judged from 200 rows up.
    batch_drift = feature_drift(model.reference, matrix) if len(matrix) >= 200 else ()
    significant = [d.feature for d in batch_drift if d.severity.value == "SIGNIFICANT"]
    if significant:
        response.headers["X-Aegis-Drift"] = "SIGNIFICANT: " + ",".join(significant)

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

    with platform.database.session(caller.tenant_id) as session:
        risks = RiskScoreRepository(session)
        for view, row in zip(views, matrix, strict=True):
            risks.upsert(
                caller.tenant_id,
                view.subject_key,
                view.probability,
                view.band,
                view.needs_intervention,
                [driver.model_dump() for driver in view.drivers],
                model_version=version,
                features={
                    name: float(value) for name, value in zip(FEATURE_NAMES, row, strict=True)
                },
            )
    return views

@app.get("/v1/attrition/scores/{subject_key}/history")
def score_history(
    subject_key: str,
    caller: ReaderDep,
    platform: PlatformDep,
    response: Response,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ScoreHistoryView]:
    """Every score this employee has had, newest first, with the model version that gave it."""
    with platform.database.session(caller.tenant_id) as session:
        rows, total = RiskScoreRepository(session).history(
            caller.tenant_id, subject_key, limit=limit, offset=offset
        )
        response.headers["X-Total-Count"] = str(total)
        return [
            ScoreHistoryView(
                subject_key=row.subject_key,
                probability=row.probability,
                band=row.band,
                needs_intervention=row.needs_intervention,
                model_version=row.model_version,
                drivers=[DriverView(**driver) for driver in row.drivers],
                scored_at=row.scored_at.isoformat(),
            )
            for row in rows
        ]

@app.get("/v1/attrition/scores")
def list_scores(
    caller: ReaderDep,
    platform: PlatformDep,
    response: Response,
    band: str | None = Query(default=None, pattern="^(LOW|MEDIUM|HIGH)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[StoredScoreView]:
    """The latest score for each employee, highest risk first."""
    with platform.database.session(caller.tenant_id) as session:
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

# An evidence pack is built in memory; past this, ask for a date range instead.
MAX_EVIDENCE = 200_000
_LEDGER_PAGE_DEFAULT = 200
_LEDGER_PAGE_MAX = 1000

@app.get("/v1/ledger")
def read_ledger(
    caller: AuditorDep,
    platform: PlatformDep,
    after: int | None = None,
    limit: int = _LEDGER_PAGE_DEFAULT,
) -> list[LedgerEntryView]:
    bounded_limit = max(1, min(limit, _LEDGER_PAGE_MAX))
    with platform.database.session(caller.tenant_id) as session:
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
def verify_ledger(caller: AuditorDep, platform: PlatformDep) -> IntegrityView:
    with platform.database.session(caller.tenant_id) as session:
        report = LedgerRepository(session).verify(
            caller.tenant_id, platform.ledger_key, require_signed=is_production()
        )

    return IntegrityView(
        intact=report.intact,
        entries_checked=report.entries_checked,
        broken_at=report.broken_at,
        reason=report.reason,
        signed=report.signed,
        unsigned=report.unsigned,
        signatures_checked=report.signatures_checked,
    )

@app.get("/v1/ledger/head")
def ledger_head(caller: AuditorDep, platform: PlatformDep) -> LedgerHeadView:
    """Where the chain ends, attested with the signing key. Record this somewhere the database's
    administrators cannot write (a ticket, an email, an append-only bucket): later evidence is
    then checkable against it, which catches a chain that was truncated or rebuilt."""
    with platform.database.session(caller.tenant_id) as session:
        last = LedgerRepository(session).last(caller.tenant_id)
        total = LedgerRepository(session).counts(caller.tenant_id)["total"]
    key = platform.ledger_key
    head_sequence = last.sequence if last else -1
    head_hash = last.entry_hash if last else GENESIS
    return LedgerHeadView(
        tenant_id=caller.tenant_id,
        sequence=last.sequence if last else None,
        entry_hash=head_hash,
        entries=total,
        signed=key is not None,
        signature=head_signature(key, caller.tenant_id, head_sequence, head_hash) if key else None,
        key_fingerprint=key_fingerprint(key) if key else None,
        generated_at=datetime.now(UTC).isoformat(),
    )

def _parse_when(value: str | None, name: str) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{name} must be an ISO 8601 date or time",
        ) from error
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)

@app.get("/v1/ledger/evidence")
def ledger_evidence(
    caller: AuditorDep,
    platform: PlatformDep,
    format: str = Query(default="json", pattern="^(json|ndjson)$"),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
) -> Response:
    """A signed evidence pack: a manifest (head hash, count, date range, signature) and every entry
    with its hashes and signature, checkable offline with `scripts/verify_evidence.py` (served at
    /v1/ledger/evidence/verifier) and the signing key, without trusting this service or its
    database. `from` and `to` bound recorded_at."""
    start, end = _parse_when(from_, "from"), _parse_when(to, "to")
    with platform.database.session(caller.tenant_id) as session:
        repository = LedgerRepository(session)
        entries = list(islice(repository.in_range(caller.tenant_id, start, end), MAX_EVIDENCE + 1))
        head = repository.last(caller.tenant_id)
    if len(entries) > MAX_EVIDENCE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"more than {MAX_EVIDENCE} entries match; narrow the range with from and to",
        )
    pack = build_evidence_pack(
        caller.tenant_id, entries, head, platform.ledger_key, start, end
    )
    if format == "ndjson":
        header = {"manifest": pack["manifest"], "manifest_signature": pack["manifest_signature"]}
        lines = [json.dumps(header, sort_keys=True)]
        lines.extend(json.dumps(entry, sort_keys=True) for entry in pack["entries"])
        return Response(
            "\n".join(lines) + "\n",
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="aegis-evidence.ndjson"'},
        )
    return JSONResponse(
        pack,
        headers={"Content-Disposition": 'attachment; filename="aegis-evidence.json"'},
    )

@app.get("/v1/ledger/evidence/verifier")
def evidence_verifier(caller: AuditorDep) -> Response:
    """The stand-alone verification script, standard library only."""
    return Response(VERIFIER_SOURCE, media_type="text/x-python")


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
    caller: AuditorDep,
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
    with platform.database.session(caller.tenant_id) as session:
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
def export_ledger(caller: AuditorDep, platform: PlatformDep) -> StreamingResponse:
    """The whole audit trail as CSV, including each entry's hashes so it can be re-verified
    offline by someone who does not trust this service."""

    def rows() -> Iterator[str]:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(_EXPORT_COLUMNS)
        yield buffer.getvalue()
        with platform.database.session(caller.tenant_id) as session:
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
def overview(caller: ReaderDep, platform: PlatformDep) -> OverviewView:
    """Counts for the console's front page, computed in the database rather than by fetching
    every run and ledger entry and counting them in the browser."""
    with platform.database.session(caller.tenant_id) as session:
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

# Routes that live in their own module register themselves on `app` when imported.
from aegis.api import analytics  # noqa: E402,F401
