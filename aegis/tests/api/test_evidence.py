"""Signed head, signed evidence packs, and the offline verifier."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from aegis.api.app import Platform
from aegis.ledger.evidence import VERIFIER_SOURCE
from aegis.ledger.record import GENESIS, _canonical, _encode_reasons
from aegis.persistence import Database
from aegis.persistence.models import LedgerRow
from aegis.security.signing import SigningKeyError
from conftest import OTHER, TENANT, Gov

KEY = "ledger-signing-key-for-tests-0123456789"
VERIFIER = Path(__file__).resolve().parents[2] / "src/aegis/ledger/verify_evidence.py"


def populate(gov: Gov, count: int = 4) -> None:
    for index in range(count):
        gov.client.post(
            "/v1/bias/adverse-impact",
            json={
                "label": f"analysis {index}",
                "outcomes": [
                    {"group": "a", "selected": 40, "total": 100},
                    {"group": "b", "selected": 38, "total": 100},
                ],
            },
            headers=gov.headers("OPERATOR"),
        )


def run_verifier(path: Path, *args: str, key: str | None = KEY) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin"}
    if key:
        env["AEGIS_LEDGER_SIGNING_KEY"] = key
    return subprocess.run(
        [sys.executable, "-I", str(VERIFIER), str(path), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_every_entry_is_signed_when_a_key_is_configured(signed_gov: Gov) -> None:
    populate(signed_gov)
    verify = signed_gov.client.get(
        "/v1/ledger/verify", headers=signed_gov.headers("AUDITOR")
    ).json()

    assert verify["intact"] and verify["signed"] == 4 and verify["unsigned"] == 0
    assert verify["signatures_checked"] is True


def test_the_head_is_attested_and_moves_with_the_chain(signed_gov: Gov) -> None:
    headers = signed_gov.headers("AUDITOR")
    empty = signed_gov.client.get("/v1/ledger/head", headers=headers).json()
    assert empty["sequence"] is None and empty["entry_hash"] == GENESIS and empty["signed"]

    populate(signed_gov, 2)
    first = signed_gov.client.get("/v1/ledger/head", headers=headers).json()
    populate(signed_gov, 1)
    second = signed_gov.client.get("/v1/ledger/head", headers=headers).json()

    assert first["sequence"] == 1 and second["sequence"] == 2
    assert first["entry_hash"] != second["entry_hash"]
    assert first["signature"] and first["signature"] != second["signature"]
    assert (
        first["key_fingerprint"] == second["key_fingerprint"]
        and len(first["key_fingerprint"]) == 16
    )


def test_the_head_is_unsigned_without_a_key(gov: Gov) -> None:
    populate(gov, 1)
    head = gov.client.get("/v1/ledger/head", headers=gov.headers("AUDITOR")).json()

    assert head["signed"] is False and head["signature"] is None


def test_the_evidence_pack_has_a_manifest_and_verifies_offline(
    signed_gov: Gov, tmp_path: Path
) -> None:
    populate(signed_gov, 5)
    response = signed_gov.client.get("/v1/ledger/evidence", headers=signed_gov.headers("AUDITOR"))
    pack = response.json()
    manifest = pack["manifest"]

    assert manifest["count"] == 5 == len(pack["entries"])
    assert manifest["first_sequence"] == 0 and manifest["last_sequence"] == 4
    assert manifest["anchor_previous_hash"] == GENESIS and manifest["signed"] is True
    assert manifest["head"]["entry_hash"] == pack["entries"][-1]["entry_hash"]
    assert manifest["verifier_sha256"] == hashlib.sha256(VERIFIER_SOURCE.encode()).hexdigest()
    assert pack["manifest_signature"]

    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(pack))
    result = run_verifier(path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK: 5 entries, hashes and signatures" in result.stdout


def test_ndjson_evidence_verifies_too(signed_gov: Gov, tmp_path: Path) -> None:
    populate(signed_gov, 3)
    response = signed_gov.client.get(
        "/v1/ledger/evidence?format=ndjson", headers=signed_gov.headers("AUDITOR")
    )
    lines = response.text.strip().split("\n")

    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert len(lines) == 1 + 3 and "manifest" in json.loads(lines[0])
    path = tmp_path / "evidence.ndjson"
    path.write_text(response.text)
    assert run_verifier(path).returncode == 0


def test_a_date_range_returns_a_verifiable_slice(signed_gov: Gov, tmp_path: Path) -> None:
    populate(signed_gov, 6)
    with signed_gov.platform.database.session(TENANT) as session:
        rows = session.scalars(select(LedgerRow).order_by(LedgerRow.sequence)).all()
        boundary = rows[3].recorded_at.isoformat()
    sliced = signed_gov.client.get(
        "/v1/ledger/evidence", params={"from": boundary}, headers=signed_gov.headers("AUDITOR")
    ).json()
    manifest = sliced["manifest"]

    assert manifest["first_sequence"] >= 3 and manifest["count"] == 6 - manifest["first_sequence"]
    assert manifest["anchor_previous_hash"] != GENESIS  # it links back into the full chain
    assert manifest["range"]["from"] is not None
    path = tmp_path / "slice.json"
    path.write_text(json.dumps(sliced))
    assert run_verifier(path).returncode == 0
    assert (
        signed_gov.client.get(
            "/v1/ledger/evidence?from=not-a-date", headers=signed_gov.headers("AUDITOR")
        ).status_code
        == 422
    )


def rebuild_chain_in_database(gov: Gov, outcome_for_sequence: dict[int, str]) -> None:
    """Edit entries and recompute every hash after them, as someone with write access could."""
    with gov.platform.database.session(TENANT) as session:
        previous = GENESIS
        for row in session.scalars(select(LedgerRow).order_by(LedgerRow.sequence)):
            row.outcome = outcome_for_sequence.get(row.sequence, row.outcome)
            row.previous_hash = previous
            canonical = _canonical(
                row.sequence,
                previous,
                [
                    row.tenant_id,
                    row.workflow,
                    row.run_id,
                    row.step,
                    row.action_type,
                    row.subject_id,
                    row.agent,
                    row.outcome,
                    _encode_reasons(row.reasons),
                    row.approver,
                ],
                row.recorded_at.replace(tzinfo=__import__("datetime").UTC),
            )
            row.entry_hash = hashlib.sha256(canonical.encode()).hexdigest()
            previous = row.entry_hash


def test_a_rebuilt_chain_fails_verification_and_the_offline_verifier(
    signed_gov: Gov, tmp_path: Path
) -> None:
    populate(signed_gov, 5)
    before = signed_gov.client.get("/v1/ledger/head", headers=signed_gov.headers("AUDITOR")).json()
    rebuild_chain_in_database(signed_gov, {2: "FLAGGED"})

    verify = signed_gov.client.get(
        "/v1/ledger/verify", headers=signed_gov.headers("AUDITOR")
    ).json()
    assert verify["intact"] is False and "signature" in verify["reason"]

    pack = signed_gov.client.get(
        "/v1/ledger/evidence", headers=signed_gov.headers("AUDITOR")
    ).json()
    path = tmp_path / "forged.json"
    path.write_text(json.dumps(pack))
    result = run_verifier(path)
    assert result.returncode == 1 and "signature does not verify" in result.stdout
    # And a head recorded earlier outside the database no longer matches.
    mismatch = run_verifier(path, "--expect-head-hash", before["entry_hash"])
    assert "not the one you recorded earlier" in mismatch.stdout


def test_the_offline_verifier_catches_an_edited_pack(signed_gov: Gov, tmp_path: Path) -> None:
    populate(signed_gov, 4)
    pack = signed_gov.client.get(
        "/v1/ledger/evidence", headers=signed_gov.headers("AUDITOR")
    ).json()
    pack["entries"][1]["outcome"] = "FLAGGED"
    path = tmp_path / "edited.json"
    path.write_text(json.dumps(pack))
    result = run_verifier(path)

    assert result.returncode == 1 and "content does not match its hash" in result.stdout

    dropped = signed_gov.client.get(
        "/v1/ledger/evidence", headers=signed_gov.headers("AUDITOR")
    ).json()
    del dropped["entries"][2]
    path.write_text(json.dumps(dropped))
    assert run_verifier(path).returncode == 1  # count, gap and link all fail


def test_the_verifier_without_the_key_says_signatures_were_not_checked(
    signed_gov: Gov, tmp_path: Path
) -> None:
    populate(signed_gov, 2)
    pack = signed_gov.client.get(
        "/v1/ledger/evidence", headers=signed_gov.headers("AUDITOR")
    ).json()
    path = tmp_path / "pack.json"
    path.write_text(json.dumps(pack))
    result = run_verifier(path, key=None)

    assert (
        result.returncode == 0 and "NOT checked" in result.stderr and "hashes only" in result.stdout
    )
    assert run_verifier(path, key="the-wrong-key-the-wrong-key-the-wrong").returncode == 1


def test_the_verifier_is_served_and_matches_the_shipped_script(signed_gov: Gov) -> None:
    response = signed_gov.client.get(
        "/v1/ledger/evidence/verifier", headers=signed_gov.headers("AUDITOR")
    )

    assert response.status_code == 200 and response.text == VERIFIER.read_text()


def test_evidence_is_for_auditors_and_one_tenant_only(signed_gov: Gov) -> None:
    populate(signed_gov, 2)

    assert (
        signed_gov.client.get(
            "/v1/ledger/evidence", headers=signed_gov.headers("OPERATOR")
        ).status_code
        == 403
    )
    assert (
        signed_gov.client.get("/v1/ledger/head", headers=signed_gov.headers("VIEWER")).status_code
        == 403
    )
    other = signed_gov.client.get(
        "/v1/ledger/evidence", headers=signed_gov.headers("AUDITOR", tenant=OTHER)
    ).json()
    assert other["manifest"]["count"] == 0 and other["entries"] == []


def test_production_refuses_to_start_without_a_ledger_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AEGIS_ENV", "production")
    monkeypatch.delenv("AEGIS_LEDGER_SIGNING_KEY", raising=False)

    with pytest.raises(SigningKeyError, match="AEGIS_LEDGER_SIGNING_KEY"):
        Platform(database=Database("sqlite+pysqlite:///:memory:"))


def test_a_short_ledger_key_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AEGIS_LEDGER_SIGNING_KEY", "short")
    with pytest.raises(SigningKeyError, match="at least 32"):
        Platform(database=Database("sqlite+pysqlite:///:memory:"))


def test_an_evidence_pack_too_large_to_build_asks_for_a_date_range(
    signed_gov: Gov, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    monkeypatch.setattr(importlib.import_module("aegis.api.app"), "MAX_EVIDENCE", 2)
    populate(signed_gov, 3)
    response = signed_gov.client.get("/v1/ledger/evidence", headers=signed_gov.headers("AUDITOR"))

    assert response.status_code == 413 and "from and to" in response.json()["detail"]
