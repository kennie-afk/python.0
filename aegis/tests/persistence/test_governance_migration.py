"""The upgrade path and the new tables, on a real PostgreSQL, as the role the API runs as.

Builds the schema at the previous head, seeds it the way a deployed system would look (an API key,
a ledger, a pickled model), upgrades to head, and checks what the migration did with it. Skipped,
not failed, when no PostgreSQL superuser answers; CI starts one and fails if these skip.
"""

from __future__ import annotations

import base64
import os
import pickle
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, OperationalError

from aegis.api.app import Platform, app, get_platform
from aegis.attrition.model import AttritionModel, _estimator
from aegis.auth.tokens import hash_api_key
from aegis.ledger.record import DecisionLedger
from aegis.persistence import (
    AlertRepository,
    ApiKeyRepository,
    CalendarRepository,
    Database,
    ModelRepository,
    RiskScoreRepository,
    VerificationRepository,
)
from aegis.persistence.models import LedgerRow, VerificationReportRow
from aegis.security.signing import model_signing_key

ADMIN_URL = os.environ.get(
    "AEGIS_TEST_PG_ADMIN_URL", "postgresql+psycopg://postgres:ownerpw@localhost:55439/postgres"
)
SCRATCH = "aegis_gov_it"
APP_PASSWORD = "app-password-for-tests-0002"
TENANT_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
TENANT_C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
KEY_A = "legacy-key-for-tenant-a-0123"
PREVIOUS_HEAD = "e7f1a2b3c4d5"


def _scratch_url(user: str | None = None, password: str | None = None) -> URL:
    base = make_url(ADMIN_URL).set(database=SCRATCH)
    if user:
        base = base.set(username=user, password=password)
    return base


def _config() -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    return config


def _migrate(target: str, down: bool = False) -> None:
    previous = {k: os.environ.get(k) for k in ("AEGIS_MIGRATION_URL", "AEGIS_APP_PASSWORD")}
    os.environ["AEGIS_MIGRATION_URL"] = _scratch_url().render_as_string(hide_password=False)
    os.environ["AEGIS_APP_PASSWORD"] = APP_PASSWORD
    try:
        (command.downgrade if down else command.upgrade)(_config(), target)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _legacy_payload() -> str:
    """What the old code stored: a pickled AttritionModel holding a fitted scikit-learn pipeline."""
    rng = np.random.default_rng(1)
    from aegis.attrition.features import FEATURE_NAMES

    matrix = rng.normal(size=(120, len(FEATURE_NAMES)))
    labels = (matrix[:, 0] + rng.normal(scale=0.5, size=120) > 0).astype(int)
    pipeline = Pipeline(
        [("scale", StandardScaler()), ("estimate", _estimator("gradient_boosting"))]
    )
    pipeline.fit(matrix, labels)
    legacy = AttritionModel.__new__(AttritionModel)
    legacy.__dict__.update(
        {
            "_algorithm": "gradient_boosting",
            "_pipeline": pipeline,
            "_baseline": matrix.mean(axis=0),
            "_importance": tuple((name, 1.0 / len(FEATURE_NAMES)) for name in FEATURE_NAMES),
        }
    )
    return base64.b64encode(pickle.dumps(legacy)).decode("ascii")


@pytest.fixture(scope="module")
def upgraded() -> Iterator[tuple[Database, Database]]:
    try:
        admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
            connection.execute(text(f"CREATE DATABASE {SCRATCH}"))
    except (OperationalError, DBAPIError) as unavailable:
        pytest.skip(f"no PostgreSQL superuser at AEGIS_TEST_PG_ADMIN_URL: {unavailable}")

    _migrate(PREVIOUS_HEAD)

    owner_url = _scratch_url().render_as_string(hide_password=False)
    engine = create_engine(owner_url)
    ledger = DecisionLedger()
    for step in ("one", "two"):
        ledger.append(TENANT_A, "wf", f"run-{step}", step, "SEND_MESSAGE", "s", "agent", "OK")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO api_keys (key_hash, tenant_id, label, roles, active, created_at) "
                "VALUES (:h, :t, 'legacy', '[\"ADMIN\"]', true, now())"
            ),
            {"h": hash_api_key(KEY_A), "t": TENANT_A},
        )
        for entry in ledger.entries:
            connection.execute(
                text(
                    "INSERT INTO decision_ledger (tenant_id, sequence, workflow, run_id, step, "
                    "action_type, subject_id, agent, outcome, reasons, approver, recorded_at, "
                    "previous_hash, entry_hash) VALUES (:t, :s, :w, :r, :st, :a, :su, :ag, :o, "
                    "'[]', NULL, :at, :p, :h)"
                ),
                {
                    "t": TENANT_A,
                    "s": entry.sequence,
                    "w": entry.workflow,
                    "r": entry.run_id,
                    "st": entry.step,
                    "a": entry.action_type,
                    "su": entry.subject_id,
                    "ag": entry.agent,
                    "o": entry.outcome,
                    "at": entry.recorded_at,
                    "p": entry.previous_hash,
                    "h": entry.entry_hash,
                },
            )
        for tenant, payload in ((TENANT_A, _legacy_payload()), (TENANT_B, "bm90LWEtcGlja2xl")):
            connection.execute(
                text(
                    "INSERT INTO attrition_models (tenant_id, algorithm, rows, positives, "
                    "feature_importance, payload, trained_at) VALUES (:t, 'gradient_boosting', "
                    "120, 60, '{}', :p, :at)"
                ),
                {"t": tenant, "p": payload, "at": datetime(2026, 1, 2, tzinfo=UTC)},
            )
    engine.dispose()

    _migrate("head")

    owner = Database(owner_url)
    app_db = Database(_scratch_url("aegis_app", APP_PASSWORD).render_as_string(hide_password=False))
    yield owner, app_db
    app_db.dispose()
    owner.dispose()


def test_existing_keys_get_an_id_and_still_resolve(upgraded: tuple[Database, Database]) -> None:
    _, app_db = upgraded
    with app_db.session() as session:
        row = ApiKeyRepository(session).resolve(hash_api_key(KEY_A))
        assert row is not None and row.tenant_id == TENANT_A
        assert len(row.key_id) == 36 and row.not_before is None
        assert session.execute(text("SELECT count(*) FROM api_keys")).scalar() == 0
        state = ApiKeyRepository(session).state(row.key_id)
        assert state is not None and state[0] == TENANT_A and state[1] is True
        assert ApiKeyRepository(session).state("00000000-0000-0000-0000-000000000000") is None


def test_the_key_state_function_does_not_reveal_a_revoked_keys_tenant_to_a_guess(
    upgraded: tuple[Database, Database],
) -> None:
    owner, app_db = upgraded
    with owner.session() as session:
        issued = ApiKeyRepository(session).issue(
            TENANT_B, "short-lived", hash_api_key("k" * 30), []
        )
        key_id = issued.key_id
        ApiKeyRepository(session).revoke(hash_api_key("k" * 30))
    with app_db.session() as session:
        state = ApiKeyRepository(session).state(key_id)
        assert state is not None and state[1] is False  # known, and no longer standing


def test_the_old_pickle_was_converted_not_unpickled_and_is_signed(
    upgraded: tuple[Database, Database],
) -> None:
    _, app_db = upgraded
    with app_db.session(TENANT_A) as session:
        repository = ModelRepository(session)
        active = repository.active(TENANT_A)
        assert active is not None and active.gate == "WARN" and active.created_by == "migration"
        assert active.version == 1 and active.signature
        model = repository.load(TENANT_A)
        assert model is not None and model.is_trained
    with app_db.session(TENANT_B) as session:
        assert ModelRepository(session).active(TENANT_B) is None  # the corrupt one was skipped


def test_the_ledger_that_predates_signing_still_verifies_and_new_entries_are_signed(
    upgraded: tuple[Database, Database],
) -> None:
    _, app_db = upgraded
    from aegis.api.app import PersistentLedger
    from aegis.persistence import LedgerRepository

    key = b"ledger-signing-key-for-tests-0123456789"
    with app_db.session(TENANT_A) as session:
        before = LedgerRepository(session).verify(TENANT_A, key)
        assert before.intact and before.unsigned == 2 and before.signed == 0
        PersistentLedger(session, TENANT_A, key).append(
            tenant_id=TENANT_A,
            workflow="wf",
            run_id="r",
            step="s",
            action_type="A",
            subject_id="x",
            agent="a",
            outcome="OK",
        )
    with app_db.session(TENANT_A) as session:
        after = LedgerRepository(session).verify(TENANT_A, key)
        assert after.intact and after.signed == 1 and after.unsigned == 2
        assert after.signatures_checked
        session.query(LedgerRow).filter(LedgerRow.sequence == 2).one().outcome = "EDITED"
    with app_db.session(TENANT_A) as session:
        assert not LedgerRepository(session).verify(TENANT_A, key).intact


def test_every_new_table_is_isolated_per_tenant_for_the_runtime_role(
    upgraded: tuple[Database, Database],
) -> None:
    owner, app_db = upgraded
    key = model_signing_key()
    with owner.session() as session:
        for tenant in (TENANT_A, TENANT_B):
            AlertRepository(session).add(tenant, "drift", f"for {tenant[:4]}", {})
            RiskScoreRepository(session).upsert(tenant, "emp", 0.5, "MEDIUM", False, [], 1, {})
            session.add(
                VerificationReportRow(
                    tenant_id=tenant,
                    kind="drift",
                    label="l",
                    verdict="STABLE",
                    report={},
                    created_by="t",
                )
            )
        from aegis.integrations.calendar import Slot

        CalendarRepository(session).add(
            TENANT_B, ["someone"], Slot(datetime(2099, 1, 1, tzinfo=UTC), 30).starts_at, 30, "e"
        )
        _ = key

    with app_db.session(TENANT_A) as session:
        assert {a.tenant_id for a in AlertRepository(session).recent(TENANT_A, 50)[0]} == {TENANT_A}
        assert RiskScoreRepository(session).history(TENANT_A, "emp")[1] == 1
        assert RiskScoreRepository(session).history(TENANT_B, "emp")[1] == 0  # another tenant's
        assert VerificationRepository(session).search(TENANT_A)[1] == 1
        assert VerificationRepository(session).search(TENANT_B)[1] == 0
        assert CalendarRepository(session).slots(TENANT_B, "someone") == []
        for table in (
            "attrition_model_versions",
            "attrition_score_history",
            "verification_reports",
            "alerts",
            "calendar_slots",
        ):
            owned = session.execute(text(f"SELECT DISTINCT tenant_id FROM {table}")).scalars().all()
            assert set(owned) <= {TENANT_A}, table

    with app_db.session() as session:  # no tenant bound: nothing at all
        for table in ("attrition_model_versions", "alerts", "calendar_slots"):
            assert session.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0


def test_the_runtime_role_cannot_write_another_tenants_new_rows(
    upgraded: tuple[Database, Database],
) -> None:
    _, app_db = upgraded
    with (
        pytest.raises(DBAPIError, match="row-level security"),
        app_db.session(TENANT_A) as session,
    ):
        AlertRepository(session).add(TENANT_B, "drift", "forged", {})


def test_the_api_end_to_end_on_postgres_revocation_and_signed_evidence(
    upgraded: tuple[Database, Database], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, app_db = upgraded
    monkeypatch.setenv("AEGIS_LEDGER_SIGNING_KEY", "ledger-signing-key-for-tests-0123456789")
    platform = Platform(database=app_db)
    app.dependency_overrides[get_platform] = lambda: platform
    try:
        with TestClient(app) as client:
            admin: dict[str, Any] = {
                "Authorization": "Bearer "
                + platform.tokens.mint(TENANT_C, "root", frozenset({"ADMIN", "AUDITOR"}))
            }
            made = client.post(
                "/v1/keys", json={"label": "pg", "roles": ["VIEWER"]}, headers=admin
            ).json()
            exchanged = client.post("/v1/auth/token", json={"api_key": made["api_key"]})
            viewer = {"Authorization": f"Bearer {exchanged.json()['token']}"}
            assert client.get("/v1/overview", headers=viewer).status_code == 200

            client.post(f"/v1/keys/{made['key_id']}/revoke", headers=admin)
            assert client.get("/v1/overview", headers=viewer).status_code == 401

            pack = client.get("/v1/ledger/evidence", headers=admin).json()
            assert pack["manifest"]["count"] == 2 and pack["manifest"]["signed"] is True
            assert client.get("/v1/ledger/head", headers=admin).json()["signature"]
            verified = client.get("/v1/ledger/verify", headers=admin).json()
            assert verified["intact"] and verified["signatures_checked"] and verified["signed"] == 2
    finally:
        app.dependency_overrides.clear()


def test_the_migration_can_be_reversed_and_applied_again(
    upgraded: tuple[Database, Database],
) -> None:
    owner, _ = upgraded
    _migrate(PREVIOUS_HEAD, down=True)
    with owner.engine.connect() as connection:
        tables = set(
            connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).scalars()
        )
        assert "attrition_model_versions" not in tables and "attrition_models" in tables
    _migrate("head")
    with owner.session() as session:
        assert ModelRepository(session).active(TENANT_A) is not None  # converted again
