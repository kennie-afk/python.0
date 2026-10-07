"""governed model versions, key lifecycle, signed ledger, durable calendar

  * attrition_model_versions: versioned, signed models in a safe format with their fidelity report
    and an active flag, replacing the one-pickle-per-tenant table (rows are converted here; the old
    table is left in place, unused, to be dropped by a later migration once the operator is happy);
  * attrition_score_history: every score ever given, append-only, with the model version;
  * api_keys: a surrogate key_id for tokens to carry, and not_before / created_by for lifecycle;
  * decision_ledger.signature: an HMAC made with a key held outside the database;
  * verification_reports, alerts, calendar_slots: results that feed the ledger, the alert outbox,
    and interview slots that survive a restart;
  * on PostgreSQL: row-level security for every new table, a SECURITY DEFINER lookup of a key's
    state by its id (so a presented token can be checked before its tenant is known).

Revision ID: 5f2c8a1d9b30
Revises: e7f1a2b3c4d5
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "5f2c8a1d9b30"
down_revision = "e7f1a2b3c4d5"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

NEW_TENANT_TABLES = [
    "attrition_model_versions",
    "attrition_score_history",
    "verification_reports",
    "alerts",
    "calendar_slots",
]


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "attrition_model_versions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("algorithm", sa.String(60), nullable=False),
        sa.Column("rows", sa.Integer, nullable=False),
        sa.Column("positives", sa.Integer, nullable=False),
        sa.Column("feature_importance", sa.JSON, nullable=True),
        sa.Column("data_hash", sa.String(64), nullable=False),
        sa.Column("format", sa.String(20), nullable=False, server_default="npz-v1"),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column("gate", sa.String(10), nullable=False),
        sa.Column("fidelity", sa.JSON, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "version", name="uq_model_tenant_version"),
    )
    op.create_index(
        "uq_model_one_active",
        "attrition_model_versions",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("active"),
        sqlite_where=sa.text("active"),
    )

    op.create_table(
        "attrition_score_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("subject_key", sa.String(200), nullable=False),
        sa.Column("probability", sa.Float, nullable=False),
        sa.Column("band", sa.String(20), nullable=False),
        sa.Column("needs_intervention", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("drivers", sa.JSON, nullable=True),
        sa.Column("features", sa.JSON, nullable=True),
        sa.Column("model_version", sa.Integer, nullable=True),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_score_history_subject",
        "attrition_score_history",
        ["tenant_id", "subject_key", "scored_at"],
    )
    op.create_index(
        "ix_score_history_tenant_time", "attrition_score_history", ["tenant_id", "scored_at"]
    )

    op.create_table(
        "verification_reports",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("verdict", sa.String(40), nullable=False),
        sa.Column("report", sa.JSON, nullable=True),
        sa.Column("ledger_sequence", sa.Integer, nullable=True),
        sa.Column("created_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_verification_tenant_created", "verification_reports", ["tenant_id", "created_at"]
    )

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("payload", sa.JSON, nullable=True),
        sa.Column("dedupe_key", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text, nullable=True),
    )
    op.create_index("ix_alerts_tenant_created", "alerts", ["tenant_id", "created_at"])

    op.create_table(
        "calendar_slots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("attendee", sa.String(200), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("minutes", sa.Integer, nullable=False),
        sa.Column("event_ref", sa.String(100), nullable=False),
    )
    op.create_index(
        "ix_calendar_attendee", "calendar_slots", ["tenant_id", "attendee", "starts_at"]
    )

    op.add_column("api_keys", sa.Column("key_id", sa.String(36), nullable=True))
    op.add_column("api_keys", sa.Column("not_before", sa.DateTime(timezone=True), nullable=True))
    op.add_column("api_keys", sa.Column("created_by", sa.String(200), nullable=True))
    if bind.dialect.name == "postgresql":
        op.execute("UPDATE api_keys SET key_id = gen_random_uuid()::text WHERE key_id IS NULL")
    else:
        import uuid

        for (key_hash,) in bind.execute(sa.text("SELECT key_hash FROM api_keys")).all():
            bind.execute(
                sa.text("UPDATE api_keys SET key_id = :k WHERE key_hash = :h"),
                {"k": str(uuid.uuid4()), "h": key_hash},
            )
    op.create_index("uq_api_keys_key_id", "api_keys", ["key_id"], unique=True)

    op.add_column("decision_ledger", sa.Column("signature", sa.String(64), nullable=True))

    if bind.dialect.name == "postgresql":
        for table in NEW_TENANT_TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY tenant_isolation ON {table} "
                f"USING (tenant_id = aegis_tenant()) WITH CHECK (tenant_id = aegis_tenant())"
            )
        op.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aegis_app"
        )
        op.execute("REVOKE ALL ON alembic_version FROM aegis_app")
        op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO aegis_app")

        # The key lookup returns the key's row, which now has more columns: recreate it.
        op.execute("DROP FUNCTION IF EXISTS aegis_resolve_api_key(text)")
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

        # A presented token names its key by key_id; this says whether that key still stands and
        # from when its tokens count, and returns nothing else about it.
        op.execute(
            """
            CREATE FUNCTION aegis_key_state(p_key_id text)
                RETURNS TABLE (tenant_id text, active boolean, not_before timestamptz)
                LANGUAGE sql STABLE SECURITY DEFINER
                SET search_path = pg_catalog, public
                AS $$ SELECT k.tenant_id::text, k.active, k.not_before
                      FROM api_keys k WHERE k.key_id = p_key_id $$
            """
        )
        op.execute("REVOKE ALL ON FUNCTION aegis_key_state(text) FROM PUBLIC")
        op.execute("GRANT EXECUTE ON FUNCTION aegis_key_state(text) TO aegis_app")

    _convert_legacy_models(bind)


def _convert_legacy_models(bind: sa.engine.Connection) -> None:
    """Bring old pickled models across without ever calling pickle.loads on them."""
    from aegis.persistence.legacy import convert_legacy_rows
    from aegis.security.signing import SigningKeyError, model_signing_key

    exists = sa.inspect(bind).has_table("attrition_models")
    if not exists:
        return
    try:
        key = model_signing_key()
    except SigningKeyError as error:
        logger.warning(
            "legacy attrition models were NOT converted (%s). Set AEGIS_JWT_SECRET or "
            "AEGIS_MODEL_SIGNING_KEY and re-run the conversion; until then those tenants have no "
            "active model.",
            error,
        )
        return
    count = convert_legacy_rows(bind, key)
    logger.info("converted %d legacy attrition model(s) to the safe format", count)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS aegis_key_state(text)")
        for table in NEW_TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.drop_column("decision_ledger", "signature")
    op.drop_index("uq_api_keys_key_id", table_name="api_keys")
    op.drop_column("api_keys", "created_by")
    op.drop_column("api_keys", "not_before")
    op.drop_column("api_keys", "key_id")
    for table in reversed(NEW_TENANT_TABLES):
        op.drop_table(table)
