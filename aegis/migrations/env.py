from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from aegis.persistence.models import Base
from aegis.persistence.runtime_role import apply_runtime_role_password
from aegis.persistence.session import admin_database_url

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Migrations run as the table owner, never as the unprivileged runtime role.
config.set_main_option("sqlalchemy.url", admin_database_url())
target_metadata = Base.metadata

def run_migrations_offline() -> None:
    context.configure(
        url=admin_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        if connection.dialect.name == "postgresql":
            # Two migrations racing (several replicas starting together) would both try to create
            # the same tables. A session-level advisory lock makes the second wait, then find
            # nothing left to do.
            connection.execute(text("SELECT pg_advisory_lock(7381151)"))
            connection.commit()
        try:
            with context.begin_transaction():
                context.run_migrations()
                apply_runtime_role_password(connection)
        finally:
            if connection.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_unlock(7381151)"))
                connection.commit()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
