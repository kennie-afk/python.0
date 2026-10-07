from __future__ import annotations

import json
from dataclasses import asdict, dataclass

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

    def parameters(self) -> tuple[float, float]:
        assert self._model is not None
        return float(self._model.coef_[0, 0]), float(self._model.intercept_[0])

@dataclass(frozen=True, slots=True)
class FrozenBoostedTrees:
    """A fitted gradient-boosted classifier reduced to plain arrays.

    Scoring needs only tree traversal and a logistic link, so the model can be stored as numbers
    (npz, no pickle) and served without scikit-learn. Trees are padded to the largest node count;
    a node is a leaf when its left child is -1.
    """

    left: np.ndarray
    right: np.ndarray
    feature: np.ndarray
    threshold: np.ndarray
    value: np.ndarray
    init_raw: float
    learning_rate: float
    depth: int

    @classmethod
    def from_sklearn(cls, model: GradientBoostingClassifier) -> FrozenBoostedTrees:
        trees = [stage[0].tree_ for stage in model.estimators_]
        width = max(tree.node_count for tree in trees)
        shape = (len(trees), width)
        left = np.full(shape, -1, dtype=np.int64)
        right = np.full(shape, -1, dtype=np.int64)
        feature = np.zeros(shape, dtype=np.int64)
        threshold = np.zeros(shape, dtype=np.float64)
        value = np.zeros(shape, dtype=np.float64)
        for position, tree in enumerate(trees):
            count = tree.node_count
            left[position, :count] = tree.children_left
            right[position, :count] = tree.children_right
            feature[position, :count] = np.maximum(tree.feature, 0)
            threshold[position, :count] = tree.threshold
            value[position, :count] = tree.value[:, 0, 0]
        prior = float(model.init_.predict_proba(np.zeros((1, model.n_features_in_)))[0, 1])
        return cls(
            left, right, feature, threshold, value,
            init_raw=float(np.log(prior / (1.0 - prior))),
            learning_rate=float(model.learning_rate),
            depth=int(max(tree.max_depth for tree in trees)),
        )

    def raw(self, matrix: np.ndarray) -> np.ndarray:
        # scikit-learn compares float32 features with float64 thresholds; do the same.
        x = np.asarray(matrix, dtype=np.float32)
        n_trees = self.left.shape[0]
        node = np.zeros((n_trees, len(x)), dtype=np.int64)
        trees = np.arange(n_trees).reshape(-1, 1)
        rows = np.arange(len(x)).reshape(1, -1)
        for _ in range(self.depth + 1):
            is_leaf = self.left[trees, node] == -1
            goes_left = x[rows, self.feature[trees, node]] <= self.threshold[trees, node]
            step = np.where(goes_left, self.left[trees, node], self.right[trees, node])
            node = np.where(is_leaf, node, step)
        return self.init_raw + self.learning_rate * self.value[trees, node].sum(axis=0)

    def predict(self, matrix: np.ndarray) -> np.ndarray:
        return np.asarray(1.0 / (1.0 + np.exp(-self.raw(matrix))), dtype=np.float64)


class LearningToRank:
    def __init__(self, config: RankerConfig) -> None:
        self._config = config
        self._model: GradientBoostingClassifier | None = None
        self._frozen: FrozenBoostedTrees | None = None
        self._platt: tuple[float, float] | None = None
        self._calibrator = PlattCalibrator()
        self._report: TrainingReport | None = None

    @property
    def is_trained(self) -> bool:
        return self._model is not None or self._frozen is not None

    @property
    def config(self) -> RankerConfig:
        return self._config

    def to_arrays(self) -> dict[str, np.ndarray]:
        """The trained model as plain arrays for np.savez; no pickle is involved."""
        if self._model is None and self._frozen is None:
            raise NotTrainedError("the ranker has not been trained")
        frozen = self._frozen or FrozenBoostedTrees.from_sklearn(self._model)
        platt = self._platt
        if platt is None and self._calibrator.is_fitted:
            platt = self._calibrator.parameters()
        report = self.report
        meta = {
            "config": asdict(self._config),
            "report": {**asdict(report), "features": list(report.features)},
            "init_raw": frozen.init_raw,
            "learning_rate": frozen.learning_rate,
            "depth": frozen.depth,
            "platt": list(platt) if platt else None,
        }
        return {
            "left": frozen.left, "right": frozen.right, "feature": frozen.feature,
            "threshold": frozen.threshold, "value": frozen.value,
            "meta": np.array(json.dumps(meta, sort_keys=True)),
        }

    @classmethod
    def from_arrays(cls, arrays: dict[str, np.ndarray]) -> LearningToRank:
        meta = json.loads(str(arrays["meta"]))
        config = dict(meta["config"])
        config["feature_order"] = tuple(config["feature_order"])
        ranker = cls(RankerConfig(**config))
        ranker._frozen = FrozenBoostedTrees(
            arrays["left"], arrays["right"], arrays["feature"], arrays["threshold"],
            arrays["value"], float(meta["init_raw"]), float(meta["learning_rate"]),
            int(meta["depth"]),
        )
        ranker._platt = tuple(meta["platt"]) if meta["platt"] else None
        report = dict(meta["report"])
        report["features"] = tuple(report["features"])
        ranker._report = TrainingReport(**report)
        return ranker

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
        if self._model is None and self._frozen is None:
            raise NotTrainedError("the ranker has not been trained")
        if not rows:
            return np.empty(0, dtype=np.float64)
        if self._frozen is not None:
            raw = self._frozen.predict(self._matrix(rows))
            if self._platt is None:
                return raw
            coef, intercept = self._platt
            return np.asarray(1.0 / (1.0 + np.exp(-(coef * raw + intercept))), dtype=np.float64)
        assert self._model is not None
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
