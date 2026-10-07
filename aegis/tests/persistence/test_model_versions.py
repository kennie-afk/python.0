"""Versioned, signed models: a doctored row is refused, and the old pickles convert safely."""

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
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from tests.attrition.test_safe_format import snapshots  # type: ignore[import-not-found]

from aegis.attrition.model import AttritionModel, ModelError, _estimator
from aegis.persistence import Database, ModelIntegrityError, ModelRepository, ModelVersionRow
from aegis.persistence.legacy import (
    LegacyPickleError,
    convert_legacy_rows,
    convert_pickle,
    restricted_loads,
)
from aegis.persistence.models import ModelRow
from aegis.security.signing import model_signing_key

TENANT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
KEY = b"k" * 32


@pytest.fixture
def database() -> Iterator[Database]:
    db = Database("sqlite+pysqlite:///:memory:")
    db.create_all()
    yield db
    db.dispose()


@pytest.fixture(scope="module")
def trained() -> AttritionModel:
    rows, left = snapshots()
    model = AttritionModel()
    model.train(rows, left)
    return model


def add(
    session: Any, tenant: str, model: AttritionModel, gate: str = "PASS", activate: bool = True
) -> ModelVersionRow:
    return ModelRepository(session, KEY).add_version(
        tenant, model, 160, 80, model.feature_importance(), gate, {"gate": gate}, "tester", activate
    )


def test_versions_accumulate_and_one_is_active(database: Database, trained: AttritionModel) -> None:
    with database.session() as session:
        add(session, TENANT, trained)
        add(session, TENANT, trained)
        repository = ModelRepository(session, KEY)

        assert [v.version for v in repository.versions(TENANT)] == [2, 1]
        assert [v.active for v in repository.versions(TENANT)] == [True, False]
        assert repository.active(TENANT).version == 2  # type: ignore[union-attr]
        assert repository.versions(OTHER) == []


def test_a_tampered_payload_is_refused(database: Database, trained: AttritionModel) -> None:
    with database.session() as session:
        row = add(session, TENANT, trained)
        blob = bytearray(base64.b64decode(row.payload))
        blob[len(blob) // 2] ^= 0xFF
        row.payload = base64.b64encode(bytes(blob)).decode()

        with pytest.raises(ModelIntegrityError, match="signature"):
            ModelRepository(session, KEY).load(TENANT)


def test_a_payload_replaced_with_another_valid_model_is_refused(
    database: Database, trained: AttritionModel
) -> None:
    rows, left = snapshots(120)
    other = AttritionModel("logistic_regression")
    other.train(rows, left)
    with database.session() as session:
        row = add(session, TENANT, trained)
        row.payload = base64.b64encode(other.to_bytes()).decode()  # a well-formed model, not ours

        with pytest.raises(ModelIntegrityError):
            ModelRepository(session, KEY).load(TENANT)


def test_a_model_cannot_be_moved_to_another_tenant(
    database: Database, trained: AttritionModel
) -> None:
    with database.session() as session:
        row = add(session, TENANT, trained)
        row.tenant_id = OTHER
        with pytest.raises(ModelIntegrityError):
            ModelRepository(session, KEY).load(OTHER)


def test_a_signature_made_with_another_key_is_refused(
    database: Database, trained: AttritionModel
) -> None:
    with database.session() as session:
        add(session, TENANT, trained)
        with pytest.raises(ModelIntegrityError):
            ModelRepository(session, b"x" * 32).load(TENANT)


def test_a_blocked_version_cannot_be_activated(database: Database, trained: AttritionModel) -> None:
    with database.session() as session:
        add(session, TENANT, trained)
        blocked = add(session, TENANT, trained, gate="BLOCK", activate=False)
        repository = ModelRepository(session, KEY)

        with pytest.raises(ModelError, match="blocked"):
            repository.activate(TENANT, blocked.version)
        assert repository.active(TENANT).version == 1  # type: ignore[union-attr]
        # Editing the gate to get past that fails the signature instead.
        blocked.gate = "PASS"
        with pytest.raises(ModelIntegrityError):
            repository.activate(TENANT, blocked.version)


def test_only_one_version_can_be_active_in_the_database(
    database: Database, trained: AttritionModel
) -> None:
    from sqlalchemy.exc import IntegrityError

    with database.session() as session:
        add(session, TENANT, trained)
        second = add(session, TENANT, trained, activate=False)
        second.active = True
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_rollback_skips_blocked_and_tampered_versions(
    database: Database, trained: AttritionModel
) -> None:
    with database.session() as session:
        add(session, TENANT, trained)  # v1 good
        add(session, TENANT, trained, "BLOCK", False)  # v2 blocked
        broken = add(session, TENANT, trained, "PASS", False)  # v3 will be tampered
        broken.payload = base64.b64encode(b"junk").decode()
        add(session, TENANT, trained)  # v4 active
        previous = ModelRepository(session, KEY).previous_eligible(TENANT)

        assert previous is not None and previous.version == 1


# --- the legacy pickles ---------------------------------------------------------------------
def legacy_payload(model: AttritionModel, rows: list[Any], left: list[bool]) -> str:
    """What the old code stored: a pickled AttritionModel holding a fitted sklearn Pipeline."""
    matrix = model.features_matrix(rows)
    pipeline = Pipeline([("scale", StandardScaler()), ("estimate", _estimator(model.algorithm))])
    pipeline.fit(matrix, np.asarray(left, dtype=int))
    legacy = AttritionModel.__new__(AttritionModel)
    legacy.__dict__.update(
        {
            "_algorithm": model.algorithm,
            "_pipeline": pipeline,
            "_baseline": matrix.mean(axis=0),
            "_importance": model.feature_importance(),
        }
    )
    return base64.b64encode(pickle.dumps(legacy)).decode("ascii")


@pytest.mark.parametrize("algorithm", ["gradient_boosting", "random_forest", "logistic_regression"])
def test_a_legacy_pickle_converts_and_scores_the_same(algorithm: str) -> None:
    rows, left = snapshots()
    model = AttritionModel(algorithm)
    model.train(rows, left)
    converted = convert_pickle(legacy_payload(model, rows, left))

    matrix = model.features_matrix(rows)
    assert np.allclose(converted.predict_matrix(matrix), model.predict_matrix(matrix), atol=1e-9)
    assert len(converted.reference) == 0  # the old rows never kept one


def test_a_malicious_pickle_is_refused_and_runs_nothing(tmp_path: Path) -> None:
    marker = tmp_path / "pwned"

    class Evil:
        def __reduce__(self) -> tuple[Any, ...]:
            return (os.system, (f"touch {marker}",))

    payload = base64.b64encode(pickle.dumps(Evil())).decode()
    with pytest.raises(LegacyPickleError, match=r"os\.system|posix\.system|not part"):
        convert_pickle(payload)
    with pytest.raises(LegacyPickleError):
        restricted_loads(pickle.dumps(Evil()))
    assert not marker.exists()


def test_legacy_rows_are_copied_into_versions(database: Database, trained: AttritionModel) -> None:
    rows, left = snapshots()
    with database.session() as session:
        session.add(
            ModelRow(
                tenant_id=TENANT,
                algorithm="gradient_boosting",
                rows=160,
                positives=80,
                feature_importance=dict(trained.feature_importance()),
                payload=legacy_payload(trained, rows, left),
                trained_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
        )
        session.add(
            ModelRow(
                tenant_id=OTHER,
                algorithm="gradient_boosting",
                rows=1,
                positives=1,
                feature_importance={},
                payload=base64.b64encode(b"junk").decode(),
                trained_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
        )
    skipped: list[str] = []
    with database.engine.begin() as connection:
        converted = convert_legacy_rows(connection, model_signing_key(), skipped.append)

    assert converted == 1 and len(skipped) == 1 and OTHER in skipped[0]
    with database.session() as session:
        repository = ModelRepository(session)
        active = repository.active(TENANT)
        assert active is not None and active.gate == "WARN" and active.created_by == "migration"
        assert "never fidelity-gated" in active.fidelity["findings"][0]
        assert repository.load(TENANT) is not None  # signed, so it loads through the normal path
        assert repository.active(OTHER) is None

    with database.engine.begin() as connection:  # running it again changes nothing
        assert convert_legacy_rows(connection, model_signing_key()) == 0
