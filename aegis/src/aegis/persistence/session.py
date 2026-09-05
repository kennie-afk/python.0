from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from aegis.persistence.models import Base

DEFAULT_URL = "postgresql+psycopg://aegis:aegis@localhost:5432/aegis"


def database_url() -> str:
    return os.environ.get("AEGIS_DATABASE_URL", DEFAULT_URL)


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

    def create_all(self) -> None:
        Base.metadata.create_all(self._engine)

    def drop_all(self) -> None:
        Base.metadata.drop_all(self._engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self._engine.dispose()
