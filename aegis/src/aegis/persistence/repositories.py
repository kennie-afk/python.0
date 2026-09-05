from __future__ import annotations

import base64
import pickle
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
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
from aegis.persistence.models import ApiKeyRow, LedgerRow, ModelRow, RunRow, StepRow, TenantRow


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

    def head(self, tenant_id: str) -> tuple[int, str]:
        row = self._session.scalars(
            select(LedgerRow)
            .where(LedgerRow.tenant_id == tenant_id)
            .order_by(LedgerRow.sequence.desc())
            .limit(1)
        ).first()
        return (row.sequence + 1, row.entry_hash) if row else (0, GENESIS)

    def entries(self, tenant_id: str, subject_id: str | None = None) -> tuple[LedgerEntry, ...]:
        statement = select(LedgerRow).where(LedgerRow.tenant_id == tenant_id)
        if subject_id:
            statement = statement.where(LedgerRow.subject_id == subject_id)

        rows = self._session.scalars(statement.order_by(LedgerRow.sequence)).all()
        return tuple(
            LedgerEntry(
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
            for row in rows
        )

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

    def issue(
        self, tenant_id: str, label: str, key_hash: str, roles: Sequence[str] = ()
    ) -> None:
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
