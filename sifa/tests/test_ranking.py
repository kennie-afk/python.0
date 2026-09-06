from __future__ import annotations

import numpy as np
import pytest

from sifa.core.errors import NotTrainedError
from sifa.core.types import Candidate
from sifa.ranking.ranker import LearningToRank, PlattCalibrator, RankerConfig

FEATURES = ("affinity", "popularity", "recency")


def make_rows(n: int = 900, seed: int = 12) -> tuple[list[dict[str, float]], list[int]]:
    rng = np.random.default_rng(seed)
    rows: list[dict[str, float]] = []
    labels: list[int] = []
    for _ in range(n):
        affinity = float(rng.random())
        popularity = float(rng.random())
        recency = float(rng.random())
        logit = 3.0 * affinity + 1.0 * popularity - 0.5 * recency - 2.0
        probability = 1.0 / (1.0 + np.exp(-logit))
        rows.append({"affinity": affinity, "popularity": popularity, "recency": recency})
        labels.append(int(rng.random() < probability))
    return rows, labels


@pytest.fixture(scope="module")
def trained() -> LearningToRank:
    model = LearningToRank(RankerConfig(feature_order=FEATURES, seed=3))
    rows, labels = make_rows()
    model.fit(rows, labels)
    return model


def test_an_untrained_model_reports_itself_untrained() -> None:
    assert LearningToRank(RankerConfig(feature_order=FEATURES)).is_trained is False


def test_an_untrained_model_refuses_to_score() -> None:
    model = LearningToRank(RankerConfig(feature_order=FEATURES))
    with pytest.raises(NotTrainedError):
        model.score([{"affinity": 0.5, "popularity": 0.5, "recency": 0.5}])


def test_training_reports_what_it_saw(trained: LearningToRank) -> None:
    report = trained.report
    assert report.rows == 900
    assert 0 < report.positives < 900
    assert report.features == FEATURES


def test_training_beats_a_coin_flip(trained: LearningToRank) -> None:
    assert trained.report.holdout_auc > 0.70


def test_the_model_calibrates_itself(trained: LearningToRank) -> None:
    assert trained.report.calibrated is True


def test_importance_finds_the_dominant_feature(trained: LearningToRank) -> None:
    importance = trained.report.importance
    assert importance["affinity"] == max(importance.values())
    assert sum(importance.values()) == pytest.approx(1.0, abs=1e-5)


def test_scores_are_probabilities(trained: LearningToRank) -> None:
    rows, _ = make_rows(n=200, seed=99)
    scores = trained.score(rows)
    assert scores.shape == (200,)
    assert scores.min() >= 0.0
    assert scores.max() <= 1.0


def test_scores_track_the_signal(trained: LearningToRank) -> None:
    low = trained.score([{"affinity": 0.02, "popularity": 0.1, "recency": 0.5}])[0]
    high = trained.score([{"affinity": 0.98, "popularity": 0.9, "recency": 0.5}])[0]
    assert high > low


def test_scoring_is_deterministic(trained: LearningToRank) -> None:
    rows, _ = make_rows(n=50, seed=5)
    assert np.allclose(trained.score(rows), trained.score(rows))


def test_a_missing_feature_is_treated_as_zero(trained: LearningToRank) -> None:
    partial = trained.score([{"affinity": 0.5}])[0]
    explicit = trained.score([{"affinity": 0.5, "popularity": 0.0, "recency": 0.0}])[0]
    assert partial == pytest.approx(explicit)


def test_feature_order_is_stable(trained: LearningToRank) -> None:
    assert trained.feature_order == FEATURES


def test_ranking_returns_items_sorted_by_score(trained: LearningToRank) -> None:
    candidates = [
        Candidate(item_id="dull", retrieval_score=0.9, source="tower"),
        Candidate(item_id="sharp", retrieval_score=0.1, source="tower"),
    ]
    features = {
        "dull": {"affinity": 0.05, "popularity": 0.1, "recency": 0.5},
        "sharp": {"affinity": 0.95, "popularity": 0.9, "recency": 0.5},
    }
    ranked = trained.rank(candidates, features)
    assert [row.item_id for row in ranked] == ["sharp", "dull"]
    assert ranked[0].score >= ranked[1].score


def test_ranking_preserves_the_retrieval_score(trained: LearningToRank) -> None:
    candidates = [Candidate(item_id="a", retrieval_score=0.42, source="tower")]
    ranked = trained.rank(candidates, {"a": {"affinity": 0.5, "popularity": 0.5, "recency": 0.5}})
    assert ranked[0].retrieval_score == pytest.approx(0.42)
    assert ranked[0].source == "tower"


def test_ranking_an_empty_candidate_set_is_empty(trained: LearningToRank) -> None:
    assert trained.rank([], {}) == []


def test_training_needs_both_classes() -> None:
    model = LearningToRank(RankerConfig(feature_order=FEATURES))
    rows = [{"affinity": 0.5, "popularity": 0.5, "recency": 0.5}] * 40
    with pytest.raises(NotTrainedError):
        model.fit(rows, [1] * 40)


def test_training_rejects_mismatched_labels() -> None:
    model = LearningToRank(RankerConfig(feature_order=FEATURES))
    rows, labels = make_rows(n=30)
    with pytest.raises(ValueError):
        model.fit(rows, labels[:10])


def test_calibrator_is_unfitted_until_it_sees_data() -> None:
    assert PlattCalibrator().is_fitted is False


def test_calibrator_maps_scores_into_the_unit_interval() -> None:
    rng = np.random.default_rng(21)
    scores = rng.normal(size=800) * 3.0
    labels = (scores + rng.normal(size=800) > 0).astype(int)

    calibrator = PlattCalibrator()
    calibrator.fit(scores, labels)
    applied = calibrator.apply(scores)

    assert calibrator.is_fitted is True
    assert applied.min() >= 0.0
    assert applied.max() <= 1.0


def test_calibration_is_monotonic() -> None:
    rng = np.random.default_rng(22)
    scores = rng.normal(size=600)
    labels = (scores > 0).astype(int)

    calibrator = PlattCalibrator()
    calibrator.fit(scores, labels)
    probe = np.array([-3.0, -1.0, 0.0, 1.0, 3.0])
    applied = calibrator.apply(probe)
    assert np.all(np.diff(applied) > 0)


def test_calibration_lowers_the_calibration_error() -> None:
    from sifa.evaluation.metrics import expected_calibration_error

    rng = np.random.default_rng(23)
    truth = rng.random(2_000)
    labels = (rng.random(2_000) < truth).astype(int)
    raw = np.clip(truth * 0.4 + 0.55, 0.0, 1.0)

    calibrator = PlattCalibrator()
    calibrator.fit(raw, labels)

    before = expected_calibration_error(raw.tolist(), labels.tolist())
    after = expected_calibration_error(calibrator.apply(raw).tolist(), labels.tolist())
    assert after < before
