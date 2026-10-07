"""A plain SHA-256 chain can be rebuilt by anyone who can write the table. The signatures cannot."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from aegis.ledger.record import (
    GENESIS,
    DecisionLedger,
    LedgerEntry,
    _canonical,
    _encode_reasons,
)

KEY = b"a-ledger-signing-key-of-32-bytes!!"
WRONG = b"another-signing-key-of-32-bytes!!"


def build(key: bytes | None, count: int = 5) -> DecisionLedger:
    ledger = DecisionLedger(key)
    for index in range(count):
        ledger.append(
            "t",
            "wf",
            f"run-{index}",
            "step",
            "SEND_MESSAGE",
            f"s{index}",
            "agent",
            "EXECUTED",
            ("reason",),
            None,
        )
    return ledger


def recompute_chain(
    entries: list[LedgerEntry], edit: dict[int, dict[str, object]]
) -> list[LedgerEntry]:
    """What an attacker with write access does: edit an entry, then rewrite every hash after it.
    No key is needed for that, so the signatures are left as they were (or dropped)."""
    out: list[LedgerEntry] = []
    previous = GENESIS
    for entry in entries:
        entry = replace(entry, **edit.get(entry.sequence, {}), previous_hash=previous)  # type: ignore[arg-type]
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
        entry = replace(entry, entry_hash=hashlib.sha256(canonical.encode()).hexdigest())
        out.append(entry)
        previous = entry.entry_hash
    return out


def verify(entries: list[LedgerEntry], key: bytes | None, require_signed: bool = False):  # type: ignore[no-untyped-def]
    ledger = DecisionLedger()
    ledger._entries.extend(entries)
    return ledger.verify(key, require_signed)


def test_an_untouched_signed_ledger_verifies_with_the_key() -> None:
    report = build(KEY).verify(KEY)

    assert report.intact and report.signed == 5 and report.unsigned == 0
    assert report.signatures_checked is True


def test_a_chain_recomputed_without_the_key_fails_verification() -> None:
    entries = list(build(KEY).entries)
    rebuilt = recompute_chain(entries, {2: {"outcome": "REJECTED"}})

    assert verify(rebuilt, None).intact  # the plain hash chain alone is fooled
    report = verify(rebuilt, KEY)
    assert not report.intact and report.broken_at == 2
    assert "signature" in (report.reason or "")


def test_a_chain_rebuilt_with_signatures_stripped_fails() -> None:
    entries = list(build(KEY).entries)
    stripped = recompute_chain(entries, {2: {"outcome": "REJECTED"}})
    stripped = [replace(e, signature=None) if e.sequence >= 2 else e for e in stripped]

    report = verify(stripped, KEY)
    assert not report.intact and "stripped" in (report.reason or "")


def test_a_deployment_that_signs_from_the_start_can_require_every_signature() -> None:
    unsigned = list(build(None).entries)

    assert verify(unsigned, KEY).intact  # a legacy, unsigned history is tolerated by default
    strict = verify(unsigned, KEY, require_signed=True)
    assert not strict.intact and "requires every entry to be signed" in (strict.reason or "")


def test_signing_after_unsigned_history_is_accepted_as_a_prefix() -> None:
    legacy = list(build(None, 3).entries)
    ledger = DecisionLedger(KEY)
    ledger._entries.extend(legacy)
    ledger.append("t", "wf", "r", "s", "SEND_MESSAGE", "x", "agent", "EXECUTED", (), None)

    report = ledger.verify(KEY)
    assert report.intact and report.unsigned == 3 and report.signed == 1


def test_the_wrong_key_does_not_verify() -> None:
    report = build(KEY).verify(WRONG)

    assert not report.intact and report.broken_at == 0


def test_a_signature_is_bound_to_its_tenant_and_position() -> None:
    entries = list(build(KEY).entries)
    moved = [replace(entries[0], tenant_id="other")]
    moved = recompute_chain(moved, {})

    assert not verify(moved, KEY).intact


def test_legacy_entries_can_be_signed_once_the_chain_checks_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aegis.api.app import PersistentLedger
    from aegis.governance.policy import TenantPolicy
    from aegis.ops.sign_ledger import sign_all
    from aegis.persistence import Database, LedgerRepository, PolicyRepository

    tenant = "11111111-1111-4111-8111-111111111111"
    monkeypatch.setenv("AEGIS_LEDGER_SIGNING_KEY", KEY.decode())
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    with database.session(tenant) as session:
        PolicyRepository(session).upsert(tenant, "Acme", TenantPolicy.conservative(tenant))
        ledger = PersistentLedger(session, tenant)  # written before signing was on
        for index in range(3):
            ledger.append(tenant, "wf", f"r{index}", "s", "A", "x", "agent", "OK")

    assert sign_all(database, database) == {tenant: 3}
    with database.session(tenant) as session:
        report = LedgerRepository(session).verify(tenant, KEY, require_signed=True)
    assert report.intact and report.signed == 3 and report.unsigned == 0
    assert sign_all(database, database) == {tenant: 0}  # nothing left to sign

    with database.session(tenant) as session:
        from aegis.persistence.models import LedgerRow

        session.query(LedgerRow).first().outcome = "EDITED"  # type: ignore[union-attr]
        session.query(LedgerRow).update({LedgerRow.signature: None})
    refused = sign_all(database, database)[tenant]
    assert isinstance(refused, str) and refused.startswith("refused: refusing to sign a broken")
