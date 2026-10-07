from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import UUID, uuid4

from sqlalchemy import delete, distinct, func, or_, select, text
from sqlalchemy.orm import Session

from aegis.agents.workflow import (
    StepState,
    StepStatus,
    WorkflowDefinition,
    WorkflowRun,
)
from aegis.attrition.model import AttritionModel, ModelError
from aegis.governance.actions import ActionType, RestrictedDomain
from aegis.governance.policy import TenantPolicy
from aegis.hr.workflows import CATALOGUE
from aegis.ledger.record import (
    GENESIS,
    DecisionLedger,
    IntegrityReport,
    LedgerEntry,
    entry_signature,
)
from aegis.persistence.models import (
    AlertRow,
    ApiKeyRow,
    CalendarSlotRow,
    ImpactReportRow,
    LedgerRow,
    ModelRow,
    ModelVersionRow,
    RiskScoreHistoryRow,
    RiskScoreRow,
    RunRow,
    ScreeningRow,
    StepRow,
    TenantRow,
    VerificationReportRow,
)
from aegis.security.signing import constant_time_equal, model_signing_key, sign


class UnknownWorkflowError(LookupError):
    pass

class RunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, run: WorkflowRun) -> None:
        row = self._session.get(RunRow, str(run.run_id))
        if row is None:
            row = RunRow(
                run_id=str(run.run_id),
                tenant_id=str(run.tenant_id),
                workflow=run.definition.name,
                subject_id=run.subject_id,
            )
            self._session.add(row)

        row.context = dict(run.context)
        existing = {step.step_key: step for step in row.steps}

        for key, state in run.steps.items():
            step_row = existing.get(key)
            if step_row is None:
                step_row = StepRow(run_id=row.run_id, step_key=key)
                row.steps.append(step_row)
            step_row.status = str(state.status)
            step_row.reasons = list(state.reasons)
            step_row.result = dict(state.result)
            step_row.approver = state.approver
            step_row.attempts = state.attempts

        self._session.flush()

    def load(self, tenant_id: str, run_id: str) -> WorkflowRun | None:
        row = self._session.get(RunRow, run_id)
        if row is None or row.tenant_id != tenant_id:
            return None

        definition = CATALOGUE.get(row.workflow)
        if definition is None:
            raise UnknownWorkflowError(f"stored run references unknown workflow {row.workflow!r}")

        return self._rebuild(row, definition)

    def for_subject(self, tenant_id: str, subject_id: str) -> tuple[WorkflowRun, ...]:
        rows = self._session.scalars(
            select(RunRow)
            .where(RunRow.tenant_id == tenant_id, RunRow.subject_id == subject_id)
            .order_by(RunRow.created_at)
        ).all()
        return tuple(self._rebuild(row, CATALOGUE[row.workflow]) for row in rows)

    def for_tenant(
        self, tenant_id: str, limit: int = 100, offset: int = 0
    ) -> tuple[WorkflowRun, ...]:
        rows = self._session.scalars(
            select(RunRow)
            .where(RunRow.tenant_id == tenant_id)
            .order_by(RunRow.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return tuple(self._rebuild(row, CATALOGUE[row.workflow]) for row in rows)

    _NEEDS: ClassVar[dict[str, StepStatus]] = {
        "approval": StepStatus.AWAITING_APPROVAL,
        "failed": StepStatus.FAILED,
        "external": StepStatus.AWAITING_EXTERNAL,
    }

    def search(
        self,
        tenant_id: str,
        workflow: str | None = None,
        query: str | None = None,
        needs: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[tuple[WorkflowRun, ...], int]:
        """Filtered, paged runs plus the total that matched. Filtering happens in the query:
        a console that filters a fetched page shows a subset as though it were everything."""
        conditions = [RunRow.tenant_id == tenant_id]
        if workflow:
            conditions.append(RunRow.workflow == workflow)
        if query:
            escaped = query.strip().lower().replace("%", "").replace("_", "")
            conditions.append(func.lower(RunRow.subject_id).like(f"%{escaped}%"))
        if needs:
            status = self._NEEDS.get(needs)
            if status is None:
                raise ValueError(f"needs must be one of {sorted(self._NEEDS)}")
            conditions.append(
                RunRow.run_id.in_(select(StepRow.run_id).where(StepRow.status == str(status)))
            )

        total = self._session.scalar(select(func.count()).select_from(RunRow).where(*conditions))
        rows = self._session.scalars(
            select(RunRow)
            .where(*conditions)
            .order_by(RunRow.created_at.desc(), RunRow.run_id)
            .limit(limit)
            .offset(offset)
        ).all()
        return tuple(self._rebuild(row, CATALOGUE[row.workflow]) for row in rows), int(total or 0)

    def counts(self, tenant_id: str) -> dict[str, int]:
        def runs_with(status: StepStatus) -> int:
            return int(
                self._session.scalar(
                    select(func.count(distinct(StepRow.run_id)))
                    .join(RunRow, RunRow.run_id == StepRow.run_id)
                    .where(RunRow.tenant_id == tenant_id, StepRow.status == str(status))
                )
                or 0
            )

        total = self._session.scalar(
            select(func.count()).select_from(RunRow).where(RunRow.tenant_id == tenant_id)
        )
        return {
            "total": int(total or 0),
            "awaiting_approval": runs_with(StepStatus.AWAITING_APPROVAL),
            "awaiting_external": runs_with(StepStatus.AWAITING_EXTERNAL),
            "failed": runs_with(StepStatus.FAILED),
        }

    def _rebuild(self, row: RunRow, definition: WorkflowDefinition) -> WorkflowRun:
        run = WorkflowRun(
            definition=definition,
            tenant_id=UUID(row.tenant_id),
            subject_id=row.subject_id,
            context=dict(row.context),
            run_id=UUID(row.run_id),
        )
        for step_row in row.steps:
            if step_row.step_key not in run.steps:
                continue
            state = StepState(
                key=step_row.step_key,
                status=StepStatus(step_row.status),
                reasons=tuple(step_row.reasons),
                result=dict(step_row.result),
                approver=step_row.approver,
                attempts=step_row.attempts,
            )
            run.steps[step_row.step_key] = state
        return run

class LedgerRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, tenant_id: str, entry: LedgerEntry) -> None:
        self._session.add(
            LedgerRow(
                tenant_id=tenant_id,
                sequence=entry.sequence,
                workflow=entry.workflow,
                run_id=entry.run_id,
                step=entry.step,
                action_type=entry.action_type,
                subject_id=entry.subject_id,
                agent=entry.agent,
                outcome=entry.outcome,
                reasons=list(entry.reasons),
                approver=entry.approver,
                recorded_at=entry.recorded_at,
                previous_hash=entry.previous_hash,
                entry_hash=entry.entry_hash,
                signature=entry.signature,
            )
        )
        self._session.flush()

    def lock_tenant(self, tenant_id: str) -> None:
        """Serialise ledger appends for one tenant so concurrent writers queue for the
        advisory lock instead of racing on `head()` and burning through the append
        retry budget. Transaction-scoped: released automatically when the request's
        session commits or rolls back. A no-op on sqlite (tests), which has no
        advisory locks and, being a single in-memory connection, no real concurrency
        to serialise in the first place."""
        if self._session.get_bind().dialect.name == "sqlite":
            return
        key = UUID(tenant_id).int & 0x7FFFFFFFFFFFFFFF
        self._session.execute(select(func.pg_advisory_xact_lock(key)))

    def head(self, tenant_id: str) -> tuple[int, str]:
        row = self._session.scalars(
            select(LedgerRow)
            .where(LedgerRow.tenant_id == tenant_id)
            .order_by(LedgerRow.sequence.desc())
            .limit(1)
        ).first()
        return (row.sequence + 1, row.entry_hash) if row else (0, GENESIS)

    def entries(
        self,
        tenant_id: str,
        subject_id: str | None = None,
        after_sequence: int | None = None,
        limit: int | None = None,
    ) -> tuple[LedgerEntry, ...]:
        statement = select(LedgerRow).where(LedgerRow.tenant_id == tenant_id)
        if subject_id:
            statement = statement.where(LedgerRow.subject_id == subject_id)
        if after_sequence is not None:
            statement = statement.where(LedgerRow.sequence > after_sequence)

        statement = statement.order_by(LedgerRow.sequence)
        if limit is not None:
            statement = statement.limit(limit)

        rows = self._session.scalars(statement).all()
        return tuple(self._entry(row) for row in rows)

    @staticmethod
    def _entry(row: LedgerRow) -> LedgerEntry:
        return LedgerEntry(
            sequence=row.sequence,
            tenant_id=row.tenant_id,
            workflow=row.workflow,
            run_id=row.run_id,
            step=row.step,
            action_type=row.action_type,
            subject_id=row.subject_id,
            agent=row.agent,
            outcome=row.outcome,
            reasons=tuple(row.reasons),
            approver=row.approver,
            # Always UTC: the hash covers isoformat(), which differs with the offset, and a
            # PostgreSQL session may hand timestamps back in another zone.
            recorded_at=row.recorded_at.replace(tzinfo=UTC)
            if row.recorded_at.tzinfo is None
            else row.recorded_at.astimezone(UTC),
            previous_hash=row.previous_hash,
            entry_hash=row.entry_hash,
            signature=row.signature,
        )

    def search(
        self,
        tenant_id: str,
        outcome: str | None = None,
        actor: str | None = None,
        query: str | None = None,
        workflow: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[tuple[LedgerEntry, ...], int]:
        """Newest first, filtered and paged in the database, with the matching total."""
        conditions = [LedgerRow.tenant_id == tenant_id]
        if outcome:
            conditions.append(LedgerRow.outcome == outcome)
        if workflow:
            conditions.append(LedgerRow.workflow == workflow)
        if actor == "people":
            conditions.append(LedgerRow.approver.is_not(None))
        elif actor == "agents":
            conditions.append(LedgerRow.approver.is_(None))
        if query:
            needle = f"%{query.strip().lower().replace('%', '').replace('_', '')}%"
            conditions.append(
                or_(
                    func.lower(LedgerRow.subject_id).like(needle),
                    func.lower(LedgerRow.step).like(needle),
                    func.lower(func.coalesce(LedgerRow.approver, "")).like(needle),
                )
            )
        total = self._session.scalar(select(func.count()).select_from(LedgerRow).where(*conditions))
        rows = self._session.scalars(
            select(LedgerRow)
            .where(*conditions)
            .order_by(LedgerRow.sequence.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return tuple(self._entry(row) for row in rows), int(total or 0)

    def counts(self, tenant_id: str) -> dict[str, int]:
        total = self._session.scalar(
            select(func.count()).select_from(LedgerRow).where(LedgerRow.tenant_id == tenant_id)
        )
        approvals = self._session.scalar(
            select(func.count())
            .select_from(LedgerRow)
            .where(LedgerRow.tenant_id == tenant_id, LedgerRow.approver.is_not(None))
        )
        return {"total": int(total or 0), "approvals": int(approvals or 0)}

    def stream(self, tenant_id: str, batch: int = 500) -> Iterator[LedgerEntry]:
        """Every entry in sequence order, a batch at a time, for exports."""
        after = -1
        while True:
            rows = self.entries(tenant_id, after_sequence=after, limit=batch)
            if not rows:
                return
            yield from rows
            after = rows[-1].sequence

    def verify(
        self, tenant_id: str, signing_key: bytes | None = None, require_signed: bool = False
    ) -> IntegrityReport:
        ledger = DecisionLedger()
        ledger._entries.extend(self.entries(tenant_id))
        return ledger.verify(signing_key, require_signed)

    def last(self, tenant_id: str) -> LedgerEntry | None:
        row = self._session.scalars(
            select(LedgerRow)
            .where(LedgerRow.tenant_id == tenant_id)
            .order_by(LedgerRow.sequence.desc())
            .limit(1)
        ).first()
        return self._entry(row) if row else None

    def in_range(
        self,
        tenant_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
        batch: int = 500,
    ) -> Iterator[LedgerEntry]:
        """Entries recorded within [start, end], in sequence order, for evidence exports."""
        after = -1
        while True:
            statement = select(LedgerRow).where(
                LedgerRow.tenant_id == tenant_id, LedgerRow.sequence > after
            )
            if start is not None:
                statement = statement.where(LedgerRow.recorded_at >= start)
            if end is not None:
                statement = statement.where(LedgerRow.recorded_at <= end)
            rows = self._session.scalars(statement.order_by(LedgerRow.sequence).limit(batch)).all()
            if not rows:
                return
            yield from (self._entry(row) for row in rows)
            after = rows[-1].sequence

    def sign_unsigned(self, tenant_id: str, signing_key: bytes) -> int:
        """Sign entries written before signing was enabled. The hash chain is checked first: this
        trusts the history as it stands now, which is the most a one-time migration can do."""
        report = self.verify(tenant_id)
        if not report.intact:
            raise RuntimeError(
                f"refusing to sign a broken ledger (broken at {report.broken_at}: {report.reason})"
            )
        rows = self._session.scalars(
            select(LedgerRow).where(
                LedgerRow.tenant_id == tenant_id, LedgerRow.signature.is_(None)
            )
        ).all()
        for row in rows:
            row.signature = entry_signature(signing_key, tenant_id, row.sequence, row.entry_hash)
        self._session.flush()
        return len(rows)

class PolicyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, tenant_id: str, name: str, policy: TenantPolicy) -> None:
        row = self._session.get(TenantRow, tenant_id)
        if row is None:
            row = TenantRow(tenant_id=tenant_id, name=name)
            self._session.add(row)

        row.name = name
        row.autonomous_actions = sorted(str(item) for item in policy.autonomous_actions)
        row.forbidden_actions = sorted(str(item) for item in policy.forbidden_actions)
        row.readable_domains = sorted(str(item) for item in policy.readable_domains)
        row.confidence_floor = policy.confidence_floor
        row.approver_role = policy.approver_role
        row.escalation_role = policy.escalation_role
        self._session.flush()

    def load(self, tenant_id: str) -> TenantPolicy | None:
        row = self._session.get(TenantRow, tenant_id)
        if row is None:
            return None

        return TenantPolicy(
            tenant_id=tenant_id,
            autonomous_actions=frozenset(ActionType(item) for item in row.autonomous_actions),
            forbidden_actions=frozenset(ActionType(item) for item in row.forbidden_actions),
            readable_domains=frozenset(RestrictedDomain(item) for item in row.readable_domains),
            confidence_floor=row.confidence_floor,
            approver_role=row.approver_role,
            escalation_role=row.escalation_role,
        )

class ModelIntegrityError(ModelError):
    """A stored model failed its signature check. It is never loaded."""

def _model_message(
    tenant_id: str, version: int, gate: str, payload_digest: str, data_hash: str
) -> tuple[str, ...]:
    return ("aegis-model-v1", tenant_id, str(version), gate, payload_digest, data_hash)

class ModelRepository:
    """Versioned attrition models. Nothing is overwritten; a retrain adds a row.

    The blob is an .npz of numeric arrays (see AttritionModel.to_bytes), so reading it cannot
    execute code, and it is signed with a key the database does not hold, so someone who can only
    write the column cannot substitute a model, flip its gate or move it to another tenant."""

    def __init__(self, session: Session, signing_key: bytes | None = None) -> None:
        self._session = session
        self._key = signing_key or model_signing_key()

    def _sign(self, tenant_id: str, version: int, gate: str, blob: bytes, data_hash: str) -> str:
        digest = hashlib.sha256(blob).hexdigest()
        return sign(self._key, *_model_message(tenant_id, version, gate, digest, data_hash))

    def add_version(
        self,
        tenant_id: str,
        model: AttritionModel,
        rows: int,
        positives: int,
        importance: Sequence[tuple[str, float]],
        gate: str,
        fidelity: dict[str, Any],
        created_by: str,
        activate: bool,
    ) -> ModelVersionRow:
        # Two trainings at once must not both claim the next version number.
        LedgerRepository(self._session).lock_tenant(tenant_id)
        latest = self._session.scalar(
            select(func.max(ModelVersionRow.version)).where(ModelVersionRow.tenant_id == tenant_id)
        )
        version = int(latest or 0) + 1
        blob = model.to_bytes()
        row = ModelVersionRow(
            tenant_id=tenant_id,
            version=version,
            algorithm=model.algorithm,
            rows=rows,
            positives=positives,
            feature_importance=dict(importance),
            data_hash=model.data_hash,
            format="npz-v1",
            payload=base64.b64encode(blob).decode("ascii"),
            signature=self._sign(tenant_id, version, gate, blob, model.data_hash),
            gate=gate,
            fidelity=fidelity,
            active=False,
            created_by=created_by,
        )
        self._session.add(row)
        self._session.flush()
        if activate:
            self.activate(tenant_id, version)
        return row

    def versions(self, tenant_id: str) -> list[ModelVersionRow]:
        return list(
            self._session.scalars(
                select(ModelVersionRow)
                .where(ModelVersionRow.tenant_id == tenant_id)
                .order_by(ModelVersionRow.version.desc())
            )
        )

    def get(self, tenant_id: str, version: int) -> ModelVersionRow | None:
        return self._session.scalars(
            select(ModelVersionRow).where(
                ModelVersionRow.tenant_id == tenant_id, ModelVersionRow.version == version
            )
        ).first()

    def active(self, tenant_id: str) -> ModelVersionRow | None:
        return self._session.scalars(
            select(ModelVersionRow).where(
                ModelVersionRow.tenant_id == tenant_id, ModelVersionRow.active.is_(True)
            )
        ).first()

    def describe(self, tenant_id: str) -> ModelVersionRow | None:
        return self.active(tenant_id)

    def activate(self, tenant_id: str, version: int) -> ModelVersionRow:
        target = self.get(tenant_id, version)
        if target is None:
            raise ModelError(f"no model version {version}")
        if target.gate == "BLOCK":
            raise ModelError(f"version {version} was blocked by the governance gate")
        # The signature covers the gate, so a row whose gate was edited cannot be activated.
        self._verified_blob(target)
        for other in self.versions(tenant_id):
            if other.active and other.version != version:
                other.active = False
        self._session.flush()
        target.active = True
        target.activated_at = datetime.now(UTC)
        self._session.flush()
        return target

    def previous_eligible(self, tenant_id: str) -> ModelVersionRow | None:
        """The newest older version that is allowed to serve, for a rollback."""
        current = self.active(tenant_id)
        ceiling = current.version if current else 10**9
        for row in self.versions(tenant_id):
            if row.version < ceiling and row.gate != "BLOCK":
                try:
                    self._verified_blob(row)
                except ModelIntegrityError:
                    continue
                return row
        return None

    def _verified_blob(self, row: ModelVersionRow) -> bytes:
        try:
            blob = base64.b64decode(row.payload.encode("ascii"), validate=True)
        except (ValueError, UnicodeEncodeError) as error:
            raise ModelIntegrityError(
                f"model version {row.version} payload is not valid"
            ) from error
        expected = self._sign(row.tenant_id, row.version, row.gate, blob, row.data_hash)
        if not constant_time_equal(expected, row.signature):
            raise ModelIntegrityError(
                f"model version {row.version} failed its signature check and was not loaded"
            )
        return blob

    def load(self, tenant_id: str, version: int | None = None) -> AttritionModel | None:
        row = self.get(tenant_id, version) if version is not None else self.active(tenant_id)
        if row is None:
            return None
        model = AttritionModel.from_bytes(self._verified_blob(row))
        if model.data_hash != row.data_hash:
            raise ModelIntegrityError(f"model version {row.version} does not match its data hash")
        return model

    def import_legacy(
        self, tenant_id: str, model: AttritionModel, rows: int, positives: int,
        importance: dict[str, float], trained_at: datetime,
    ) -> ModelVersionRow:
        """Bring a model from the old one-pickle-per-tenant table in as an active WARN version:
        it never passed a fidelity gate, and the record says so."""
        record = self.add_version(
            tenant_id, model, rows, positives, tuple(importance.items()), "WARN",
            {
                "gate": "WARN",
                "score": None,
                "findings": [
                    "converted from a legacy pickle; it was never fidelity-gated and carries "
                    "no drift reference"
                ],
            },
            "migration", activate=True,
        )
        record.created_at = trained_at
        self._session.flush()
        return record

class ApiKeyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def issue(
        self,
        tenant_id: str,
        label: str,
        key_hash: str,
        roles: Sequence[str] = (),
        created_by: str | None = None,
    ) -> ApiKeyRow:
        row = ApiKeyRow(
            key_hash=key_hash,
            tenant_id=tenant_id,
            label=label,
            roles=list(roles),
            key_id=str(uuid4()),
            created_by=created_by,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def list_for_tenant(self, tenant_id: str) -> list[ApiKeyRow]:
        return list(
            self._session.scalars(
                select(ApiKeyRow)
                .where(ApiKeyRow.tenant_id == tenant_id)
                .order_by(ApiKeyRow.created_at.desc(), ApiKeyRow.key_id)
            )
        )

    def by_key_id(self, tenant_id: str, key_id: str) -> ApiKeyRow | None:
        return self._session.scalars(
            select(ApiKeyRow).where(
                ApiKeyRow.tenant_id == tenant_id, ApiKeyRow.key_id == key_id
            )
        ).first()

    def state(self, key_id: str) -> tuple[str, bool, datetime | None] | None:
        """(tenant, active, not_before) for a key id, or None. This is how a presented token is
        checked against its key before the tenant is known; on PostgreSQL it goes through a
        SECURITY DEFINER function that returns only that."""
        bind = self._session.get_bind()
        if bind.dialect.name == "postgresql":
            found = self._session.execute(
                text("SELECT * FROM aegis_key_state(:key_id)"), {"key_id": key_id}
            ).mappings().first()
            if found is None:
                return None
            return found["tenant_id"], bool(found["active"]), found["not_before"]
        row = self._session.scalars(select(ApiKeyRow).where(ApiKeyRow.key_id == key_id)).first()
        return None if row is None else (row.tenant_id, bool(row.active), row.not_before)

    def revoke_by_id(self, tenant_id: str, key_id: str) -> ApiKeyRow | None:
        row = self.by_key_id(tenant_id, key_id)
        if row is None:
            return None
        row.active = False
        row.revoked_at = datetime.now(UTC)
        self._session.flush()
        return row

    def invalidate_tokens(self, tenant_id: str, key_id: str) -> ApiKeyRow | None:
        row = self.by_key_id(tenant_id, key_id)
        if row is None:
            return None
        row.not_before = datetime.now(UTC)
        self._session.flush()
        return row

    def resolve(self, key_hash: str) -> ApiKeyRow | None:
        """Look a key up before anyone knows which tenant it belongs to.

        On PostgreSQL that is a SECURITY DEFINER function, the one deliberate gap in row-level
        security:
        it returns the key's own row only, and only for the exact hash presented."""
        bind = self._session.get_bind()
        if bind.dialect.name == "postgresql":
            found = self._session.execute(
                text("SELECT * FROM aegis_resolve_api_key(:hash)"), {"hash": key_hash}
            ).mappings().first()
            if found is None:
                return None
            return ApiKeyRow(
                key_hash=found["key_hash"], tenant_id=found["tenant_id"], label=found["label"],
                roles=found["roles"], active=found["active"], revoked_at=found["revoked_at"],
                key_id=found["key_id"], not_before=found["not_before"],
            )
        row = self._session.get(ApiKeyRow, key_hash)
        if row is None or not row.active:
            return None
        return row

    def revoke(self, key_hash: str) -> bool:
        row = self._session.get(ApiKeyRow, key_hash)
        if row is None:
            return False
        row.active = False
        row.revoked_at = datetime.now(UTC)
        self._session.flush()
        return True

    def purge(self, tenant_id: str) -> None:
        self._session.execute(delete(ApiKeyRow).where(ApiKeyRow.tenant_id == tenant_id))
        self._session.flush()


class ScreeningRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        tenant_id: str,
        subject_key: str,
        requirement: str,
        score: float,
        recommendation: str,
        rationale: str,
        signals: Sequence[str],
        model: str,
        prompt_fingerprint: str,
    ) -> ScreeningRow:
        row = ScreeningRow(
            tenant_id=tenant_id,
            subject_key=subject_key,
            requirement=requirement,
            score=score,
            recommendation=recommendation,
            rationale=rationale,
            signals=list(signals),
            model=model,
            prompt_fingerprint=prompt_fingerprint,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def search(
        self,
        tenant_id: str,
        recommendation: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[tuple[ScreeningRow, ...], int]:
        conditions = [ScreeningRow.tenant_id == tenant_id]
        if recommendation:
            conditions.append(ScreeningRow.recommendation == recommendation)
        if query:
            needle = f"%{query.strip().lower().replace('%', '').replace('_', '')}%"
            conditions.append(func.lower(ScreeningRow.subject_key).like(needle))
        total = self._session.scalar(
            select(func.count()).select_from(ScreeningRow).where(*conditions)
        )
        rows = self._session.scalars(
            select(ScreeningRow)
            .where(*conditions)
            .order_by(ScreeningRow.created_at.desc(), ScreeningRow.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return tuple(rows), int(total or 0)

    def by_recommendation(self, tenant_id: str) -> dict[str, int]:
        rows = self._session.execute(
            select(ScreeningRow.recommendation, func.count())
            .where(ScreeningRow.tenant_id == tenant_id)
            .group_by(ScreeningRow.recommendation)
        ).all()
        return {recommendation: int(count) for recommendation, count in rows}

class ImpactReportRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, row: ImpactReportRow) -> ImpactReportRow:
        self._session.add(row)
        self._session.flush()
        return row

    def get(self, tenant_id: str, report_id: int) -> ImpactReportRow | None:
        row = self._session.get(ImpactReportRow, report_id)
        return row if row is not None and row.tenant_id == tenant_id else None

    def search(
        self, tenant_id: str, verdict: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[tuple[ImpactReportRow, ...], int]:
        conditions = [ImpactReportRow.tenant_id == tenant_id]
        if verdict:
            conditions.append(ImpactReportRow.verdict == verdict)
        total = self._session.scalar(
            select(func.count()).select_from(ImpactReportRow).where(*conditions)
        )
        rows = self._session.scalars(
            select(ImpactReportRow)
            .where(*conditions)
            .order_by(ImpactReportRow.created_at.desc(), ImpactReportRow.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return tuple(rows), int(total or 0)

    def by_verdict(self, tenant_id: str) -> dict[str, int]:
        rows = self._session.execute(
            select(ImpactReportRow.verdict, func.count())
            .where(ImpactReportRow.tenant_id == tenant_id)
            .group_by(ImpactReportRow.verdict)
        ).all()
        return {verdict: int(count) for verdict, count in rows}

class RiskScoreRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(
        self,
        tenant_id: str,
        subject_key: str,
        probability: float,
        band: str,
        needs_intervention: bool,
        drivers: Sequence[dict[str, object]],
        model_version: int | None = None,
        features: dict[str, float] | None = None,
    ) -> None:
        # Append-only history first; the row below stays the roster's "latest score".
        self._session.add(
            RiskScoreHistoryRow(
                tenant_id=tenant_id,
                subject_key=subject_key,
                probability=probability,
                band=band,
                needs_intervention=needs_intervention,
                drivers=[dict(driver) for driver in drivers],
                features=dict(features or {}),
                model_version=model_version,
                scored_at=datetime.now(UTC),
            )
        )
        row = self._session.scalars(
            select(RiskScoreRow).where(
                RiskScoreRow.tenant_id == tenant_id, RiskScoreRow.subject_key == subject_key
            )
        ).first()
        if row is None:
            row = RiskScoreRow(tenant_id=tenant_id, subject_key=subject_key)
            self._session.add(row)
        row.probability = probability
        row.band = band
        row.needs_intervention = needs_intervention
        row.drivers = [dict(driver) for driver in drivers]
        row.scored_at = datetime.now(UTC)
        self._session.flush()

    def history(
        self, tenant_id: str, subject_key: str, limit: int = 100, offset: int = 0
    ) -> tuple[tuple[RiskScoreHistoryRow, ...], int]:
        conditions = [
            RiskScoreHistoryRow.tenant_id == tenant_id,
            RiskScoreHistoryRow.subject_key == subject_key,
        ]
        total = self._session.scalar(
            select(func.count()).select_from(RiskScoreHistoryRow).where(*conditions)
        )
        rows = self._session.scalars(
            select(RiskScoreHistoryRow)
            .where(*conditions)
            .order_by(RiskScoreHistoryRow.scored_at.desc(), RiskScoreHistoryRow.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return tuple(rows), int(total or 0)

    def recent_features(self, tenant_id: str, since: datetime, limit: int = 5000) -> list[
        dict[str, float]
    ]:
        rows = self._session.scalars(
            select(RiskScoreHistoryRow.features)
            .where(
                RiskScoreHistoryRow.tenant_id == tenant_id,
                RiskScoreHistoryRow.scored_at >= since,
            )
            .order_by(RiskScoreHistoryRow.id.desc())
            .limit(limit)
        ).all()
        return [dict(item) for item in rows if item]

    def search(
        self, tenant_id: str, band: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[tuple[RiskScoreRow, ...], int]:
        conditions = [RiskScoreRow.tenant_id == tenant_id]
        if band:
            conditions.append(RiskScoreRow.band == band)
        total = self._session.scalar(
            select(func.count()).select_from(RiskScoreRow).where(*conditions)
        )
        rows = self._session.scalars(
            select(RiskScoreRow)
            .where(*conditions)
            .order_by(RiskScoreRow.probability.desc(), RiskScoreRow.id)
            .limit(limit)
            .offset(offset)
        ).all()
        return tuple(rows), int(total or 0)

    def by_band(self, tenant_id: str) -> dict[str, int]:
        rows = self._session.execute(
            select(RiskScoreRow.band, func.count())
            .where(RiskScoreRow.tenant_id == tenant_id)
            .group_by(RiskScoreRow.band)
        ).all()
        return {band: int(count) for band, count in rows}

class VerificationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, row: VerificationReportRow) -> VerificationReportRow:
        self._session.add(row)
        self._session.flush()
        return row

    def search(
        self, tenant_id: str, kind: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[tuple[VerificationReportRow, ...], int]:
        conditions = [VerificationReportRow.tenant_id == tenant_id]
        if kind:
            conditions.append(VerificationReportRow.kind == kind)
        total = self._session.scalar(
            select(func.count()).select_from(VerificationReportRow).where(*conditions)
        )
        rows = self._session.scalars(
            select(VerificationReportRow)
            .where(*conditions)
            .order_by(VerificationReportRow.created_at.desc(), VerificationReportRow.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return tuple(rows), int(total or 0)

class AlertRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        tenant_id: str,
        kind: str,
        message: str,
        payload: dict[str, Any],
        dedupe_key: str | None = None,
    ) -> AlertRow | None:
        """Queue an alert. With a dedupe key, an undelivered or recent alert with the same key
        suppresses a repeat, so a condition that persists raises one alert, not one per check."""
        if dedupe_key is not None:
            existing = self._session.scalars(
                select(AlertRow)
                .where(AlertRow.tenant_id == tenant_id, AlertRow.dedupe_key == dedupe_key)
                .order_by(AlertRow.id.desc())
                .limit(1)
            ).first()
            if existing is not None:
                return None
        row = AlertRow(
            tenant_id=tenant_id, kind=kind, message=message, payload=payload, dedupe_key=dedupe_key
        )
        self._session.add(row)
        self._session.flush()
        return row

    def clear_dedupe(self, tenant_id: str, dedupe_key: str) -> None:
        """The condition has cleared: let the next occurrence alert again."""
        for row in self._session.scalars(
            select(AlertRow).where(
                AlertRow.tenant_id == tenant_id, AlertRow.dedupe_key == dedupe_key
            )
        ):
            row.dedupe_key = None
        self._session.flush()

    def pending(self, tenant_id: str, max_attempts: int = 5) -> list[AlertRow]:
        return list(
            self._session.scalars(
                select(AlertRow)
                .where(
                    AlertRow.tenant_id == tenant_id,
                    AlertRow.delivered_at.is_(None),
                    AlertRow.attempts < max_attempts,
                )
                .order_by(AlertRow.id)
            )
        )

    def mark(self, row: AlertRow, error: str | None) -> None:
        row.attempts += 1
        row.last_error = error
        row.delivered_at = None if error else datetime.now(UTC)
        self._session.flush()

    def recent(self, tenant_id: str, limit: int = 50, offset: int = 0) -> tuple[
        tuple[AlertRow, ...], int
    ]:
        total = self._session.scalar(
            select(func.count()).select_from(AlertRow).where(AlertRow.tenant_id == tenant_id)
        )
        rows = self._session.scalars(
            select(AlertRow)
            .where(AlertRow.tenant_id == tenant_id)
            .order_by(AlertRow.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return tuple(rows), int(total or 0)

class CalendarRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def slots(self, tenant_id: str, attendee: str) -> list[CalendarSlotRow]:
        return list(
            self._session.scalars(
                select(CalendarSlotRow)
                .where(CalendarSlotRow.tenant_id == tenant_id, CalendarSlotRow.attendee == attendee)
                .order_by(CalendarSlotRow.starts_at)
            )
        )

    def add(
        self, tenant_id: str, attendees: Sequence[str], starts_at: datetime, minutes: int,
        event_ref: str,
    ) -> None:
        for attendee in attendees:
            self._session.add(
                CalendarSlotRow(
                    tenant_id=tenant_id, attendee=attendee, starts_at=starts_at,
                    minutes=minutes, event_ref=event_ref,
                )
            )
        self._session.flush()

def purge_tenant(session: Session, tenant_id: str) -> None:
    """Remove everything a tenant owns. Used by the demo reset; there is deliberately no API."""
    run_ids = select(RunRow.run_id).where(RunRow.tenant_id == tenant_id)
    session.execute(delete(StepRow).where(StepRow.run_id.in_(run_ids)))
    for table in (
        RunRow,
        LedgerRow,
        ScreeningRow,
        ImpactReportRow,
        RiskScoreRow,
        RiskScoreHistoryRow,
        ModelRow,
        ModelVersionRow,
        ApiKeyRow,
        VerificationReportRow,
        AlertRow,
        CalendarSlotRow,
    ):
        session.execute(delete(table).where(table.tenant_id == tenant_id))
    session.execute(delete(TenantRow).where(TenantRow.tenant_id == tenant_id))
    session.flush()
