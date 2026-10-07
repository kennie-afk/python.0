#!/usr/bin/env python3
"""Verify an Aegis evidence pack offline. Standard library only; trusts nothing from the service.

    AEGIS_LEDGER_SIGNING_KEY=... python verify_evidence.py evidence.json
    AEGIS_LEDGER_SIGNING_KEY=... python verify_evidence.py evidence.ndjson \\
        --expect-head-hash <hash you recorded earlier from GET /v1/ledger/head>

Checks, in order: the manifest signature; that the entry count and sequence range match the
manifest; for every entry, that its hash recomputes from its content, that it links to the previous
entry (the first links to the manifest's anchor), and that its HMAC signature verifies; and that the
head attestation verifies. A chain rebuilt by someone without the signing key recomputes fine but
fails the signature checks. Exit status 0 means everything held, 1 means something did not.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from typing import Any

GENESIS = "0" * 64


def sign(key: bytes, *parts: str) -> str:
    message = "".join(f"{len(part)}:{part}" for part in parts).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _encode_reasons(reasons: list[str]) -> str:
    return "".join(f"{len(reason)}:{reason}" for reason in reasons)


def entry_hash(entry: dict[str, Any]) -> str:
    fields = [
        entry["tenant_id"],
        entry["workflow"],
        entry["run_id"],
        entry["step"],
        entry["action_type"],
        entry["subject_id"],
        entry["agent"],
        entry["outcome"],
        _encode_reasons(entry["reasons"]),
        entry["approver"],
    ]
    parts = [str(entry["sequence"]), entry["previous_hash"], entry["recorded_at"]]
    parts.extend("" if value is None else value for value in fields)
    canonical = "".join(f"{len(part)}:{part}" for part in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load(path: str) -> tuple[dict[str, Any], str | None, list[dict[str, Any]]]:
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if path.endswith(".ndjson"):
        lines = [json.loads(line) for line in text.splitlines() if line.strip()]
        header, entries = lines[0], lines[1:]
    else:
        document = json.loads(text)
        header, entries = document, document["entries"]
    return header["manifest"], header.get("manifest_signature"), entries


def verify(
    manifest: dict[str, Any],
    manifest_signature: str | None,
    entries: list[dict[str, Any]],
    key: bytes | None,
    expect_head_hash: str | None = None,
) -> list[str]:
    problems: list[str] = []

    if key is not None:
        if manifest_signature is None:
            problems.append("manifest is unsigned although a key was supplied")
        else:
            expected = sign(key, "aegis-evidence-manifest", json.dumps(manifest, sort_keys=True))
            if not hmac.compare_digest(expected, manifest_signature):
                problems.append("manifest signature does not verify")

    if manifest["count"] != len(entries):
        problems.append(f"manifest says {manifest['count']} entries, the pack holds {len(entries)}")

    previous = manifest["anchor_previous_hash"]
    expected_sequence = manifest["first_sequence"]
    for entry in entries:
        where = f"entry {entry['sequence']}"
        if entry["tenant_id"] != manifest["tenant_id"]:
            problems.append(f"{where}: belongs to another tenant")
        if entry["sequence"] != expected_sequence:
            problems.append(f"{where}: expected sequence {expected_sequence} (gap or reorder)")
        if entry["previous_hash"] != previous:
            problems.append(f"{where}: does not link to the previous entry")
        if entry_hash(entry) != entry["entry_hash"]:
            problems.append(f"{where}: content does not match its hash")
        if key is not None:
            signature = entry.get("signature")
            if signature is None:
                problems.append(f"{where}: unsigned")
            else:
                want = sign(
                    key,
                    "aegis-ledger-entry",
                    entry["tenant_id"],
                    str(entry["sequence"]),
                    entry["entry_hash"],
                )
                if not hmac.compare_digest(want, signature):
                    problems.append(f"{where}: signature does not verify")
        previous = entry["entry_hash"]
        expected_sequence = entry["sequence"] + 1

    head = manifest.get("head") or {}
    if key is not None and head.get("signature"):
        want = sign(
            key,
            "aegis-ledger-head",
            manifest["tenant_id"],
            str(head["sequence"]),
            head["entry_hash"],
        )
        if not hmac.compare_digest(want, head["signature"]):
            problems.append("head attestation does not verify")
    if expect_head_hash is not None and head.get("entry_hash") != expect_head_hash:
        problems.append(
            "the head hash in this pack is not the one you recorded earlier: the chain was "
            "rebuilt, truncated or extended"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify an Aegis evidence pack")
    parser.add_argument("pack", help="evidence.json or evidence.ndjson")
    parser.add_argument("--key-env", default="AEGIS_LEDGER_SIGNING_KEY")
    parser.add_argument("--expect-head-hash", default=None)
    args = parser.parse_args(argv)

    manifest, signature, entries = load(args.pack)
    raw_key = os.environ.get(args.key_env)
    key = raw_key.encode("utf-8") if raw_key else None
    problems = verify(manifest, signature, entries, key, args.expect_head_hash)

    if key is None:
        print(f"note: {args.key_env} is not set, so signatures were NOT checked", file=sys.stderr)
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1
    scope = "hashes and signatures" if key else "hashes only"
    print(f"OK: {len(entries)} entries, {scope}, head {manifest['head']['entry_hash'][:16]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
