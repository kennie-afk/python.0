"""Models are stored as arrays and served from them; scores must match scikit-learn exactly."""

from __future__ import annotations

import io
import random

import numpy as np
import pytest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from aegis.attrition.features import FEATURE_NAMES, EmployeeSnapshot
from aegis.attrition.frozen import FrozenPipeline
from aegis.attrition.model import AttritionModel, ModelError, _estimator

ALGORITHMS = ("gradient_boosting", "random_forest", "logistic_regression")


def snapshots(count: int = 160) -> tuple[list[EmployeeSnapshot], list[bool]]:
    rng = random.Random(3)
    left = [i % 2 == 0 for i in range(count)]
    rows = [
        EmployeeSnapshot(
            subject_key=f"s{i}",
            tenure_years=rng.uniform(0.5, 9),
            months_since_promotion=rng.uniform(30, 60) if flag else rng.uniform(1, 12),
            salary=rng.uniform(70_000, 90_000) if flag else rng.uniform(95_000, 125_000),
            band_midpoint=100_000.0,
            peer_median_salary=100_000.0,
            manager_changes_24m=rng.randint(0, 4),
            commute_minutes=rng.uniform(5, 70),
            engagement_score=rng.uniform(1, 2.5) if flag else rng.uniform(3, 5),
            training_hours_12m=rng.uniform(0, 40),
            overtime_hours_monthly=rng.uniform(0, 20),
            internal_applications_12m=rng.randint(0, 3),
        )
        for i, flag in enumerate(left)
    ]
    return rows, left


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_the_frozen_arrays_score_exactly_like_scikit_learn(algorithm: str) -> None:
    rows, left = snapshots()
    model = AttritionModel(algorithm)
    model.train(rows, left)
    matrix = model.features_matrix(rows)

    pipeline = Pipeline([("scale", StandardScaler()), ("estimate", _estimator(algorithm))])
    pipeline.fit(matrix, np.asarray(left, dtype=int))
    expected = pipeline.predict_proba(matrix)[:, 1]

    assert np.allclose(model.predict_matrix(matrix), expected, atol=1e-9)
    assert np.allclose(
        FrozenPipeline.from_sklearn(pipeline).predict_proba(matrix), expected, atol=1e-9
    )


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_a_model_survives_the_round_trip_through_bytes(algorithm: str) -> None:
    rows, left = snapshots()
    model = AttritionModel(algorithm)
    model.train(rows, left)
    restored = AttritionModel.from_bytes(model.to_bytes())

    assert restored.data_hash == model.data_hash
    assert restored.feature_importance() == model.feature_importance()
    for row in rows[:20]:
        assert restored.score(row).probability == pytest.approx(model.score(row).probability)
        assert restored.score(row).drivers == model.score(row).drivers
    assert restored.reference.shape[1] == len(FEATURE_NAMES)


def test_the_stored_form_holds_no_pickled_objects() -> None:
    rows, left = snapshots()
    model = AttritionModel()
    model.train(rows, left)
    with np.load(io.BytesIO(model.to_bytes()), allow_pickle=False) as archive:
        assert all(archive[name].dtype != object for name in archive.files)


def test_a_blob_that_is_not_a_model_is_refused() -> None:
    with pytest.raises(ModelError):
        AttritionModel.from_bytes(b"not an npz")
    buffer = io.BytesIO()
    np.savez(buffer, meta=np.array('{"format": 99}'))
    with pytest.raises(ModelError, match="unsupported"):
        AttritionModel.from_bytes(buffer.getvalue())


def test_an_object_array_cannot_smuggle_a_pickle_in() -> None:
    buffer = io.BytesIO()
    np.savez(buffer, meta=np.array([object()], dtype=object))
    with pytest.raises(ModelError):
        AttritionModel.from_bytes(buffer.getvalue())


def test_training_data_is_summarised_by_a_hash() -> None:
    rows, left = snapshots()
    first, second = AttritionModel(), AttritionModel()
    first.train(rows, left)
    second.train(rows, left)
    third = AttritionModel()
    third.train(rows, [not flag for flag in left])

    assert first.data_hash == second.data_hash != third.data_hash
