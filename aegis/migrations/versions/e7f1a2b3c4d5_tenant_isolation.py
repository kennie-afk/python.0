"""database-level tenant isolation

Until now every query carried its own tenant filter and nothing at the database checked it: one
forgotten filter was a cross-tenant leak, and the API connected as the table owner, which row-level
security never applies to. From here on, on PostgreSQL:

  * the API connects as `aegis_app`, which owns nothing, is not a superuser and cannot bypass
    row-level security (created here without a login; its password is applied from the environment
    after migrations, see aegis.persistence.runtime_role);
  * every tenant table has a policy: a row is visible only when its tenant_id equals
    `aegis.tenant_id`, which the application sets per transaction from the signed caller;
  * the one lookup that cannot know the tenant first, an API key being presented, goes through a
    SECURITY DEFINER function that returns the key's own row and nothing else.

SQLite (used by unit tests) has no row-level security and is left alone.

Revision ID: e7f1a2b3c4d5
Revises: c3a9d41e7f20
"""

from __future__ import annotations

from alembic import op

revision = "e7f1a2b3c4d5"
down_revision = "c3a9d41e7f20"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "tenants",
    "workflow_runs",
    "decision_ledger",
    "attrition_models",
    "api_keys",
    "screenings",
    "impact_reports",
    "attrition_scores",
]


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aegis_app') THEN
                CREATE ROLE aegis_app NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
            END IF;
        END
        $$
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO aegis_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aegis_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO aegis_app")
    op.execute("REVOKE ALL ON alembic_version FROM aegis_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO aegis_app"
    )
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO aegis_app")

    op.execute(
        """
        CREATE FUNCTION aegis_tenant() RETURNS text
            LANGUAGE sql STABLE
            AS $$ SELECT nullif(current_setting('aegis.tenant_id', true), '') $$
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION aegis_tenant() TO aegis_app")

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = aegis_tenant()) WITH CHECK (tenant_id = aegis_tenant())"
        )

    # workflow_steps carries no tenant of its own: it belongs to a run, and the run policy applies to
    # the sub-select, so a step is visible only when its run is.
    op.execute("ALTER TABLE workflow_steps ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON workflow_steps "
        "USING (EXISTS (SELECT 1 FROM workflow_runs r WHERE r.run_id = workflow_steps.run_id)) "
        "WITH CHECK (EXISTS (SELECT 1 FROM workflow_runs r WHERE r.run_id = workflow_steps.run_id))"
    )

    op.execute(
        """
        CREATE FUNCTION aegis_resolve_api_key(p_hash text) RETURNS SETOF api_keys
            LANGUAGE sql STABLE SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $$ SELECT * FROM api_keys WHERE key_hash = p_hash AND active $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION aegis_resolve_api_key(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION aegis_resolve_api_key(text) TO aegis_app")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP FUNCTION IF EXISTS aegis_resolve_api_key(text)")
    for table in [*TENANT_TABLES, "workflow_steps"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS aegis_tenant()")
