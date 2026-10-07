from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from aegis.security.signing import constant_time_equal, sign

GENESIS = "0" * 64

def entry_signature(key: bytes, tenant_id: str, sequence: int, entry_hash: str) -> str:
    """HMAC that binds an entry to a key the database does not hold. A plain SHA-256 chain can be
    recomputed end to end by anyone who can write the table; this cannot."""
    return sign(key, "aegis-ledger-entry", tenant_id, str(sequence), entry_hash)

def head_signature(key: bytes, tenant_id: str, sequence: int, entry_hash: str) -> str:
    """Attestation of the chain's head: how long it is and what it ends in."""
    return sign(key, "aegis-ledger-head", tenant_id, str(sequence), entry_hash)

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
    signature: str | None = None

    @property
    def was_human_approved(self) -> bool:
        return self.approver is not None

@dataclass(frozen=True, slots=True)
class IntegrityReport:
    intact: bool
    entries_checked: int
    broken_at: int | None = None
    reason: str | None = None
    signed: int = 0
    unsigned: int = 0
    signatures_checked: bool = False

def _canonical(
    sequence: int,
    previous_hash: str,
    fields: Sequence[str | None],
    recorded_at: datetime,
) -> str:
    parts = [str(sequence), previous_hash, recorded_at.isoformat()]
    parts.extend("" if value is None else value for value in fields)
    return "".join(f"{len(part)}:{part}" for part in parts)

def _encode_reasons(reasons: Sequence[str]) -> str:
    # Length-prefix each reason individually rather than joining with "|". A plain
    # join is not collision-resistant: ("a", "b") and ("a|b",) join to the same
    # string, so an entry recorded with two separate reasons is byte-identical,
    # post-join, to one recorded with a single reason that merely contains a "|" -
    # meaning either could be silently rewritten into the other without touching
    # entry_hash. Length-prefixing closes that the same way the outer canonical
    # encoding already does for every other field.
    return "".join(f"{len(reason)}:{reason}" for reason in reasons)

def make_entry(
    sequence: int,
    previous_hash: str,
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
    signing_key: bytes | None = None,
) -> LedgerEntry:
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
            _encode_reasons(reason_tuple),
            approver,
        ],
        recorded_at,
    )

    entry_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return LedgerEntry(
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
        entry_hash=entry_hash,
        signature=entry_signature(signing_key, tenant_id, sequence, entry_hash)
        if signing_key
        else None,
    )

class DecisionLedger:
    def __init__(self, signing_key: bytes | None = None) -> None:
        self._entries: list[LedgerEntry] = []
        self._signing_key = signing_key

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
        entry = make_entry(
            sequence=len(self._entries),
            previous_hash=self.head_hash,
            tenant_id=tenant_id,
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
        self._entries.append(entry)
        return entry

    def verify(
        self, signing_key: bytes | None = None, require_signed: bool = False
    ) -> IntegrityReport:
        """Check hashes and links; with a key, also every signature.

        Signatures are what stop a recomputed chain: with the key, an entry whose signature does
        not match is a break, and so is an unsigned entry after a signed one (a stripped
        signature). `require_signed` additionally refuses an unsigned prefix, for deployments
        that have signed from the start."""
        expected_previous = GENESIS
        signed = unsigned = 0
        seen_signed = False

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
                    _encode_reasons(entry.reasons),
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

            if entry.signature is not None:
                signed += 1
                seen_signed = True
                if signing_key is not None and not constant_time_equal(
                    entry_signature(signing_key, entry.tenant_id, entry.sequence, entry.entry_hash),
                    entry.signature,
                ):
                    return IntegrityReport(
                        intact=False,
                        entries_checked=index,
                        broken_at=entry.sequence,
                        reason="signature does not verify: the entry or the chain was rewritten "
                        "by someone without the signing key",
                        signed=signed,
                        unsigned=unsigned,
                        signatures_checked=True,
                    )
            else:
                unsigned += 1
                if signing_key is not None and (seen_signed or require_signed):
                    return IntegrityReport(
                        intact=False,
                        entries_checked=index,
                        broken_at=entry.sequence,
                        reason="entry is unsigned"
                        + (" after signed entries (a signature was stripped)" if seen_signed
                           else " but this deployment requires every entry to be signed"),
                        signed=signed,
                        unsigned=unsigned,
                        signatures_checked=True,
                    )

            expected_previous = entry.entry_hash

        return IntegrityReport(
            intact=True,
            entries_checked=len(self._entries),
            signed=signed,
            unsigned=unsigned,
            signatures_checked=signing_key is not None,
        )

    def for_subject(self, subject_id: str) -> tuple[LedgerEntry, ...]:
        return tuple(entry for entry in self._entries if entry.subject_id == subject_id)

    def for_tenant(self, tenant_id: str) -> tuple[LedgerEntry, ...]:
        return tuple(entry for entry in self._entries if entry.tenant_id == tenant_id)

    def human_approvals(self) -> tuple[LedgerEntry, ...]:
        return tuple(entry for entry in self._entries if entry.was_human_approved)
