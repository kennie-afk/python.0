"""Signed evidence packs: the audit trail in a form someone else can verify without us."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aegis.ledger.record import GENESIS, LedgerEntry, head_signature
from aegis.security.signing import key_fingerprint, sign

VERIFIER_SOURCE = Path(__file__).with_name("verify_evidence.py").read_text(encoding="utf-8")


def entry_to_dict(entry: LedgerEntry) -> dict[str, Any]:
    return {
        "sequence": entry.sequence,
        "tenant_id": entry.tenant_id,
        "workflow": entry.workflow,
        "run_id": entry.run_id,
        "step": entry.step,
        "action_type": entry.action_type,
        "subject_id": entry.subject_id,
        "agent": entry.agent,
        "outcome": entry.outcome,
        "reasons": list(entry.reasons),
        "approver": entry.approver,
        "recorded_at": entry.recorded_at.isoformat(),
        "previous_hash": entry.previous_hash,
        "entry_hash": entry.entry_hash,
        "signature": entry.signature,
    }


def build_evidence_pack(
    tenant_id: str,
    entries: list[LedgerEntry],
    head: LedgerEntry | None,
    signing_key: bytes | None,
    start: datetime | None,
    end: datetime | None,
) -> dict[str, Any]:
    head_sequence = head.sequence if head else -1
    head_hash = head.entry_hash if head else GENESIS
    manifest: dict[str, Any] = {
        "format": 1,
        "tenant_id": tenant_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "count": len(entries),
        "first_sequence": entries[0].sequence if entries else 0,
        "last_sequence": entries[-1].sequence if entries else None,
        "anchor_previous_hash": entries[0].previous_hash if entries else GENESIS,
        "range": {
            "from": start.isoformat() if start else None,
            "to": end.isoformat() if end else None,
            "first_recorded_at": entries[0].recorded_at.isoformat() if entries else None,
            "last_recorded_at": entries[-1].recorded_at.isoformat() if entries else None,
        },
        "head": {
            "sequence": head_sequence,
            "entry_hash": head_hash,
            "signature": head_signature(signing_key, tenant_id, head_sequence, head_hash)
            if signing_key
            else None,
        },
        "signed": signing_key is not None,
        "key_fingerprint": key_fingerprint(signing_key) if signing_key else None,
        "entries_signed": sum(1 for e in entries if e.signature),
        "verifier": "/v1/ledger/evidence/verifier",
        "verifier_sha256": hashlib.sha256(VERIFIER_SOURCE.encode("utf-8")).hexdigest(),
    }
    signature = (
        sign(signing_key, "aegis-evidence-manifest", json.dumps(manifest, sort_keys=True))
        if signing_key
        else None
    )
    return {
        "manifest": manifest,
        "manifest_signature": signature,
        "entries": [entry_to_dict(entry) for entry in entries],
    }
