from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression

from sifa.core.errors import NotTrainedError
from sifa.core.types import Candidate, ScoredItem


@dataclass(frozen=True, slots=True)
class RankerConfig:
    feature_order: tuple[str, ...]
    n_estimators: int = 120
    max_depth: int = 3
    learning_rate: float = 0.1
    seed: int = 29
    calibration_fraction: float = 0.25

    def __post_init__(self) -> None:
        if not self.feature_order:
            raise ValueError("a ranker needs at least one feature")
        if not 0.0 < self.calibration_fraction < 0.9:
            raise ValueError("calibration_fraction must sit between 0 and 0.9")


@dataclass(frozen=True, slots=True)
class TrainingReport:
    rows: int
    positives: int
    features: tuple[str, ...]
    importance: dict[str, float]
    calibrated: bool
    holdout_auc: float


class PlattCalibrator:
    def __init__(self) -> None:
        self._model: LogisticRegression | None = None

    def fit(self, scores: np.ndarray, labels: np.ndarray) -> None:
        if len(np.unique(labels)) < 2:
            self._model = None
            return
        self._model = LogisticRegression(max_iter=1000)
        self._model.fit(scores.reshape(-1, 1), labels)

    def apply(self, scores: np.ndarray) -> np.ndarray:
        if self._model is None:
            return scores
        return np.asarray(self._model.predict_proba(scores.reshape(-1, 1))[:, 1], dtype=np.float64)

    @property
    def is_fitted(self) -> bool:
        return self._model is not None


class LearningToRank:
    def __init__(self, config: RankerConfig) -> None:
        self._config = config
        self._model: GradientBoostingClassifier | None = None
        self._calibrator = PlattCalibrator()
        self._report: TrainingReport | None = None

    @property
    def is_trained(self) -> bool:
        return self._model is not None

    @property
    def report(self) -> TrainingReport:
        if self._report is None:
            raise NotTrainedError("the ranker has not been trained")
        return self._report

    @property
    def feature_order(self) -> tuple[str, ...]:
        return self._config.feature_order

    def _matrix(self, rows: list[dict[str, float]]) -> np.ndarray:
        return np.array(
            [[row.get(name, 0.0) for name in self._config.feature_order] for row in rows],
            dtype=np.float64,
        )

    def fit(self, rows: list[dict[str, float]], labels: list[int]) -> TrainingReport:
        if len(rows) != len(labels):
            raise ValueError("rows and labels must be the same length")
        if len(rows) < 20:
            raise NotTrainedError("a ranker needs at least twenty examples to be meaningful")

        matrix = self._matrix(rows)
        targets = np.asarray(labels, dtype=np.int64)

        if len(np.unique(targets)) < 2:
            raise NotTrainedError("training data must contain both clicked and unclicked rows")

        rng = np.random.default_rng(self._config.seed)
        order = rng.permutation(len(targets))
        split = int(len(order) * (1.0 - self._config.calibration_fraction))
        train_idx, holdout_idx = order[:split], order[split:]

        self._model = GradientBoostingClassifier(
            n_estimators=self._config.n_estimators,
            max_depth=self._config.max_depth,
            learning_rate=self._config.learning_rate,
            random_state=self._config.seed,
        )
        self._model.fit(matrix[train_idx], targets[train_idx])

        holdout_scores = self._model.predict_proba(matrix[holdout_idx])[:, 1]
        self._calibrator.fit(holdout_scores, targets[holdout_idx])

        auc = _roc_auc(holdout_scores, targets[holdout_idx])

        self._report = TrainingReport(
            rows=len(rows),
            positives=int(targets.sum()),
            features=self._config.feature_order,
            importance={
                name: float(value)
                for name, value in zip(
                    self._config.feature_order, self._model.feature_importances_, strict=True
                )
            },
            calibrated=self._calibrator.is_fitted,
            holdout_auc=auc,
        )
        return self._report

    def score(self, rows: list[dict[str, float]]) -> np.ndarray:
        if self._model is None:
            raise NotTrainedError("the ranker has not been trained")
        if not rows:
            return np.empty(0, dtype=np.float64)
        raw = self._model.predict_proba(self._matrix(rows))[:, 1]
        return self._calibrator.apply(raw)

    def rank(
        self, candidates: list[Candidate], features: dict[str, dict[str, float]]
    ) -> list[ScoredItem]:
        if not candidates:
            return []

        rows = [features.get(candidate.item_id, {}) for candidate in candidates]
        scores = self.score(rows)

        ranked = [
            ScoredItem(
                item_id=candidate.item_id,
                score=float(score),
                retrieval_score=candidate.retrieval_score,
                source=candidate.source,
                features=dict(row),
            )
            for candidate, row, score in zip(candidates, rows, scores, strict=True)
        ]
        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked


def _roc_auc(scores: np.ndarray, labels: np.ndarray) -> float:
    positives = scores[labels == 1]
    negatives = scores[labels == 0]
    if len(positives) == 0 or len(negatives) == 0:
        return 0.5

    order = np.argsort(np.concatenate([positives, negatives]))
    ranks = np.empty(len(order), dtype=np.float64)
    ranks[order] = np.arange(1, len(order) + 1)
    positive_rank_sum = ranks[: len(positives)].sum()

    n_pos, n_neg = len(positives), len(negatives)
    return float((positive_rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))
