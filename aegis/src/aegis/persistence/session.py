from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from aegis.persistence.models import Base

DEFAULT_URL = "postgresql+psycopg://aegis:aegis@localhost:5432/aegis"

def database_url() -> str:
    return os.environ.get("AEGIS_DATABASE_URL", DEFAULT_URL)

def admin_database_url() -> str:
    """The owner connection: migrations, provisioning a tenant, the demo seed. Never the runtime
    API.

    The API connects as an unprivileged role (see migrations tenant_isolation) that owns nothing and
    cannot bypass row-level security. Operations that legitimately span tenants use this URL
    instead:
    AEGIS_ADMIN_DATABASE_URL, else AEGIS_MIGRATION_URL, else the ordinary URL (development and
    SQLite).
    """
    return (
        os.environ.get("AEGIS_ADMIN_DATABASE_URL")
        or os.environ.get("AEGIS_MIGRATION_URL")
        or database_url()
    )

def build_engine(url: str | None = None, echo: bool = False) -> Engine:
    resolved = url or database_url()

    if resolved.startswith("sqlite"):
        in_memory = ":memory:" in resolved
        return create_engine(
            resolved,
            echo=echo,
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool if in_memory else None,
        )

    return create_engine(resolved, echo=echo, future=True, pool_pre_ping=True)

class Database:
    def __init__(self, url: str | None = None, echo: bool = False) -> None:
        self._engine = build_engine(url, echo)
        self._factory = sessionmaker(bind=self._engine, expire_on_commit=False, future=True)

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def is_sqlite(self) -> bool:
        return self._engine.dialect.name == "sqlite"

    def create_all(self) -> None:
        Base.metadata.create_all(self._engine)

    def drop_all(self) -> None:
        Base.metadata.drop_all(self._engine)

    @contextmanager
    def session(self, tenant_id: str | None = None) -> Iterator[Session]:
        """One transaction. On PostgreSQL the tenant is bound for it, so row-level security
        shows the
        caller only that tenant's rows. SQLite (the unit tests) has no row-level security, so there
        the argument changes nothing and the repositories' own tenant filters are the only guard.

        A session opened without a tenant sees no tenant rows at all on PostgreSQL: the few
        operations that cannot know the tenant yet (an API key being exchanged) go through
        SECURITY DEFINER functions instead."""
        session = self._factory()
        try:
            if tenant_id is not None and not self.is_sqlite:
                session.execute(
                    text("SELECT set_config('aegis.tenant_id', :tenant, true)"), {"tenant":
                    tenant_id}
                )
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def assert_runtime_role_is_safe(self) -> None:
        """Refuse to start as a role that row-level security does not apply to.

        A superuser or BYPASSRLS role silently sees every tenant, which turns the database backstop
        into decoration. Skipped on SQLite."""
        if self.is_sqlite:
            return
        with self._engine.connect() as connection:
            row = connection.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).one()
        if row.rolsuper or row.rolbypassrls:
            raise RuntimeError(
                "AEGIS_DATABASE_URL connects as a superuser or BYPASSRLS role, so tenant "
                "isolation in the database does not apply. Connect as the unprivileged runtime "
                "role (aegis_app) and keep the owner credentials in AEGIS_MIGRATION_URL for "
                "migrations only."
            )

    def dispose(self) -> None:
        self._engine.dispose()
