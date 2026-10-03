"""Tenant isolation where it is enforced: in PostgreSQL, as the role the API really runs as.

Applies the real Alembic migrations to a scratch database, then talks to it as `aegis_app` so
a forgotten tenant filter in application code cannot hide a gap. Needs a PostgreSQL superuser to
create the scratch database; skipped (not failed) when none answers. Start one with

    docker run -d --name aegis-it-pg -e POSTGRES_PASSWORD=ownerpw -p 55439:5432 postgres:16-alpine

or point AEGIS_TEST_PG_ADMIN_URL at another (a SQLAlchemy URL for the `postgres` database).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, OperationalError

from aegis.auth.tokens import hash_api_key
from aegis.persistence import ApiKeyRepository, Database, RunRepository
from aegis.persistence.models import RunRow, ScreeningRow, StepRow, TenantRow

ADMIN_URL = os.environ.get(
    "AEGIS_TEST_PG_ADMIN_URL", "postgresql+psycopg://postgres:ownerpw@localhost:55439/postgres"
)
SCRATCH = "aegis_rls_it"
APP_PASSWORD = "app-password-for-tests-0001"
TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
KEY_A = "key-for-tenant-a-0123456789"


def _scratch_url(user: str | None = None, password: str | None = None) -> URL:
    base = make_url(ADMIN_URL).set(database=SCRATCH)
    if user:
        base = base.set(username=user, password=password)
    return base


@pytest.fixture(scope="module")
def databases() -> Iterator[tuple[Database, Database]]:
    """(owner, app): the same scratch database, migrated, seeded, opened as two roles."""
    try:
        admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
            connection.execute(text(f"CREATE DATABASE {SCRATCH}"))
    except (OperationalError, DBAPIError) as unavailable:
        pytest.skip(f"no PostgreSQL superuser at AEGIS_TEST_PG_ADMIN_URL: {unavailable}")

    owner_url = _scratch_url().render_as_string(hide_password=False)
    previous = {k: os.environ.get(k) for k in ("AEGIS_MIGRATION_URL", "AEGIS_APP_PASSWORD")}
    os.environ["AEGIS_MIGRATION_URL"] = owner_url
    os.environ["AEGIS_APP_PASSWORD"] = APP_PASSWORD
    try:
        root = Path(__file__).resolve().parents[2]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "migrations"))
        command.upgrade(config, "head")
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    owner = Database(owner_url)
    with owner.session() as session:
        for tenant in (TENANT_A, TENANT_B):
            session.add(TenantRow(tenant_id=tenant, name=f"Tenant {tenant[:4]}"))
            session.add(
                RunRow(run_id=f"run-{tenant[:4]}", tenant_id=tenant, workflow="w", subject_id="s")
            )
            session.add(StepRow(run_id=f"run-{tenant[:4]}", step_key="k", status="DONE"))
            session.add(
                ScreeningRow(
                    tenant_id=tenant, subject_key="s", requirement="r", score=0.5,
                    recommendation="ADVANCE", rationale="x", model="m", prompt_fingerprint="f",
                )
            )
        ApiKeyRepository(session).issue(TENANT_A, "a-key", hash_api_key(KEY_A), ["ADMIN"])

    app = Database(_scratch_url("aegis_app", APP_PASSWORD).render_as_string(hide_password=False))
    yield owner, app
    app.dispose()
    owner.dispose()


def test_the_runtime_role_is_unprivileged_and_the_guard_accepts_it(
    databases: tuple[Database, Database],
) -> None:
    _, app = databases
    app.assert_runtime_role_is_safe()
    with app.engine.connect() as connection:
        flags = connection.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
    assert (flags.rolsuper, flags.rolbypassrls) == (False, False)


def test_the_guard_refuses_a_superuser_connection(databases: tuple[Database, Database]) -> None:
    owner, _ = databases
    with pytest.raises(RuntimeError, match="superuser or BYPASSRLS"):
        owner.assert_runtime_role_is_safe()


def test_a_tenant_sees_only_its_own_rows_in_every_table(
    databases: tuple[Database, Database],
) -> None:
    _, app = databases
    with app.session(TENANT_A) as session:
        assert session.query(TenantRow).count() == 1
        assert session.query(RunRow).count() == 1
        assert session.query(StepRow).count() == 1
        assert session.query(ScreeningRow).count() == 1
        assert {r.tenant_id for r in session.query(RunRow)} == {TENANT_A}


def test_without_a_tenant_the_role_sees_nothing(databases: tuple[Database, Database]) -> None:
    _, app = databases
    with app.session() as session:
        assert session.query(TenantRow).count() == 0
        assert session.query(RunRow).count() == 0
        assert session.query(StepRow).count() == 0


def test_a_tenant_cannot_load_another_tenants_run_by_id(
    databases: tuple[Database, Database],
) -> None:
    _, app = databases
    with app.session(TENANT_A) as session:
        assert session.get(RunRow, f"run-{TENANT_B[:4]}") is None
        assert RunRepository(session).load(TENANT_A, f"run-{TENANT_B[:4]}") is None


def test_a_tenant_cannot_write_a_row_for_another_tenant(
    databases: tuple[Database, Database],
) -> None:
    _, app = databases
    with (
        pytest.raises(DBAPIError, match="row-level security"),
        app.session(TENANT_A) as session,
    ):
        session.add(RunRow(run_id="leak", tenant_id=TENANT_B, workflow="w", subject_id="s"))


def test_an_update_or_delete_aimed_at_another_tenant_touches_nothing(
    databases: tuple[Database, Database],
) -> None:
    _, app = databases
    with app.session(TENANT_A) as session:
        updated = session.execute(
            text("UPDATE workflow_runs SET subject_id = 'hacked' WHERE tenant_id = :t"),
            {"t": TENANT_B},
        ).rowcount
        deleted = session.execute(
            text("DELETE FROM screenings WHERE tenant_id = :t"), {"t": TENANT_B}
        ).rowcount
    assert (updated, deleted) == (0, 0)


def test_an_api_key_resolves_without_a_tenant_through_the_definer_function(
    databases: tuple[Database, Database],
) -> None:
    _, app = databases
    with app.session() as session:
        row = ApiKeyRepository(session).resolve(hash_api_key(KEY_A))
        assert row is not None and row.tenant_id == TENANT_A and row.label == "a-key"
        assert ApiKeyRepository(session).resolve(hash_api_key("not-a-key")) is None
        # The function is not a way to read the table: a direct read sees nothing.
        assert session.execute(text("SELECT count(*) FROM api_keys")).scalar() == 0


def test_a_revoked_key_no_longer_resolves(databases: tuple[Database, Database]) -> None:
    owner, app = databases
    with owner.session() as session:
        ApiKeyRepository(session).issue(TENANT_A, "old", hash_api_key("revoked-key-0123456789"), [])
        ApiKeyRepository(session).revoke(hash_api_key("revoked-key-0123456789"))
    with app.session() as session:
        assert ApiKeyRepository(session).resolve(hash_api_key("revoked-key-0123456789")) is None
