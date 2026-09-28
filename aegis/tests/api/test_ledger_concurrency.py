from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError

from aegis.api.app import PersistentLedger
from aegis.persistence import Database, LedgerRepository

TENANT = "88888888-8888-8888-8888-888888888888"


def _entry_kwargs(run_id: str) -> dict[str, str]:
    return {
        "tenant_id": TENANT,
        "workflow": "talent_acquisition",
        "run_id": run_id,
        "step": "schedule_interview",
        "action_type": "SEND_EMAIL",
        "subject_id": "candidate-1",
        "agent": "scheduler",
        "outcome": "COMPLETED",
    }


@pytest.fixture
def database() -> Iterator[Database]:
    db = Database("sqlite+pysqlite:///:memory:")
    db.create_all()
    yield db
    db.dispose()


def test_append_retries_after_a_lost_sequence_race(database: Database) -> None:
    """Two requests for the same tenant can both compute the same next
    sequence and race to insert it - the loser must retry against a freshly
    re-read head rather than surface the unique-constraint conflict to the
    caller after its side effect has already run."""
    original_append = LedgerRepository.append
    calls = {"n": 0}

    def flaky_append(self: LedgerRepository, tenant_id: str, entry: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("insert into ledger", {}, Exception("duplicate sequence"))
        original_append(self, tenant_id, entry)  # type: ignore[arg-type]

    with database.session() as session:
        ledger = PersistentLedger(session, TENANT)

        with patch.object(LedgerRepository, "append", flaky_append):
            entry = ledger.append(**_entry_kwargs("run-1"))

        assert calls["n"] == 2
        assert entry.sequence == 0

    with database.session() as session:
        stored = LedgerRepository(session).entries(TENANT)

    assert len(stored) == 1
    assert stored[0].sequence == 0


def test_append_gives_up_after_persistent_conflict(database: Database) -> None:
    def always_conflicts(self: LedgerRepository, tenant_id: str, entry: object) -> None:
        raise IntegrityError("insert into ledger", {}, Exception("duplicate sequence"))

    with database.session() as session:
        ledger = PersistentLedger(session, TENANT)

        with (
            patch.object(LedgerRepository, "append", always_conflicts),
            pytest.raises(RuntimeError, match="contention on the sequence"),
        ):
            ledger.append(**_entry_kwargs("run-2"))
