from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

GENESIS = "0" * 64


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    sequence: int
    tenant_id: str
    workflow: str
    run_id: str
    step: str
    action_type: str
    subject_id: str
    agent: str
    outcome: str
    reasons: tuple[str, ...]
    approver: str | None
    recorded_at: datetime
    previous_hash: str
    entry_hash: str

    @property
    def was_human_approved(self) -> bool:
        return self.approver is not None


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    intact: bool
    entries_checked: int
    broken_at: int | None = None
    reason: str | None = None


def _canonical(
    sequence: int,
    previous_hash: str,
    fields: Sequence[str | None],
    recorded_at: datetime,
) -> str:
    parts = [str(sequence), previous_hash, recorded_at.isoformat()]
    parts.extend("" if value is None else value for value in fields)
    return "".join(f"{len(part)}:{part}" for part in parts)


class DecisionLedger:
    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(self._entries)

    @property
    def head_hash(self) -> str:
        return self._entries[-1].entry_hash if self._entries else GENESIS

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
        sequence = len(self._entries)
        previous_hash = self.head_hash
        recorded_at = datetime.now(UTC)
        reason_tuple = tuple(reasons)

        canonical = _canonical(
            sequence,
            previous_hash,
            [
                tenant_id,
                workflow,
                run_id,
                step,
                action_type,
                subject_id,
                agent,
                outcome,
                "|".join(reason_tuple),
                approver,
            ],
            recorded_at,
        )

        entry = LedgerEntry(
            sequence=sequence,
            tenant_id=tenant_id,
            workflow=workflow,
            run_id=run_id,
            step=step,
            action_type=action_type,
            subject_id=subject_id,
            agent=agent,
            outcome=outcome,
            reasons=reason_tuple,
            approver=approver,
            recorded_at=recorded_at,
            previous_hash=previous_hash,
            entry_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
        self._entries.append(entry)
        return entry

    def verify(self) -> IntegrityReport:
        expected_previous = GENESIS

        for index, entry in enumerate(self._entries):
            if entry.sequence != index:
                return IntegrityReport(
                    intact=False,
                    entries_checked=index,
                    broken_at=entry.sequence,
                    reason=f"sequence gap: expected {index}",
                )
            if entry.previous_hash != expected_previous:
                return IntegrityReport(
                    intact=False,
                    entries_checked=index,
                    broken_at=entry.sequence,
                    reason="previous-hash link does not match the preceding entry",
                )

            canonical = _canonical(
                entry.sequence,
                entry.previous_hash,
                [
                    entry.tenant_id,
                    entry.workflow,
                    entry.run_id,
                    entry.step,
                    entry.action_type,
                    entry.subject_id,
                    entry.agent,
                    entry.outcome,
                    "|".join(entry.reasons),
                    entry.approver,
                ],
                entry.recorded_at,
            )
            recomputed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if recomputed != entry.entry_hash:
                return IntegrityReport(
                    intact=False,
                    entries_checked=index,
                    broken_at=entry.sequence,
                    reason="entry content does not match its stored hash",
                )

            expected_previous = entry.entry_hash

        return IntegrityReport(intact=True, entries_checked=len(self._entries))

    def for_subject(self, subject_id: str) -> tuple[LedgerEntry, ...]:
        return tuple(entry for entry in self._entries if entry.subject_id == subject_id)

    def for_tenant(self, tenant_id: str) -> tuple[LedgerEntry, ...]:
        return tuple(entry for entry in self._entries if entry.tenant_id == tenant_id)

    def human_approvals(self) -> tuple[LedgerEntry, ...]:
        return tuple(entry for entry in self._entries if entry.was_human_approved)
