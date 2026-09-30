from __future__ import annotations

import base64
import pickle
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import ClassVar
from uuid import UUID

from sqlalchemy import delete, distinct, func, or_, select
from sqlalchemy.orm import Session

from aegis.agents.workflow import (
    StepState,
    StepStatus,
    WorkflowDefinition,
    WorkflowRun,
)
from aegis.attrition.model import AttritionModel
from aegis.governance.actions import ActionType, RestrictedDomain
from aegis.governance.policy import TenantPolicy
from aegis.hr.workflows import CATALOGUE
from aegis.ledger.record import GENESIS, DecisionLedger, IntegrityReport, LedgerEntry
from aegis.persistence.models import (
    ApiKeyRow,
    ImpactReportRow,
    LedgerRow,
    ModelRow,
    RiskScoreRow,
    RunRow,
    ScreeningRow,
    StepRow,
    TenantRow,
)


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
            recorded_at=row.recorded_at.replace(tzinfo=UTC)
            if row.recorded_at.tzinfo is None
            else row.recorded_at,
            previous_hash=row.previous_hash,
            entry_hash=row.entry_hash,
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

    def verify(self, tenant_id: str) -> IntegrityReport:
        ledger = DecisionLedger()
        ledger._entries.extend(self.entries(tenant_id))
        return ledger.verify()

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

class ModelRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(
        self,
        tenant_id: str,
        model: AttritionModel,
        rows: int,
        positives: int,
        importance: Sequence[tuple[str, float]],
    ) -> None:
        row = self._session.get(ModelRow, tenant_id)
        payload = base64.b64encode(pickle.dumps(model)).decode("ascii")

        if row is None:
            row = ModelRow(tenant_id=tenant_id, algorithm=model.algorithm, payload=payload)
            self._session.add(row)

        row.algorithm = model.algorithm
        row.rows = rows
        row.positives = positives
        row.feature_importance = dict(importance)
        row.payload = payload
        row.trained_at = datetime.now(UTC)
        self._session.flush()

    def describe(self, tenant_id: str) -> ModelRow | None:
        return self._session.get(ModelRow, tenant_id)

    def load(self, tenant_id: str) -> AttritionModel | None:
        row = self._session.get(ModelRow, tenant_id)
        if row is None:
            return None
        restored = pickle.loads(base64.b64decode(row.payload))
        if not isinstance(restored, AttritionModel):
            return None
        return restored

class ApiKeyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def issue(self, tenant_id: str, label: str, key_hash: str, roles: Sequence[str] = ()) -> None:
        self._session.add(
            ApiKeyRow(
                key_hash=key_hash,
                tenant_id=tenant_id,
                label=label,
                roles=list(roles),
            )
        )
        self._session.flush()

    def resolve(self, key_hash: str) -> ApiKeyRow | None:
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
    ) -> None:
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
        ModelRow,
        ApiKeyRow,
    ):
        session.execute(delete(table).where(table.tenant_id == tenant_id))
    session.execute(delete(TenantRow).where(TenantRow.tenant_id == tenant_id))
    session.flush()
