"""A fitted scikit-learn pipeline reduced to plain arrays.

Scoring a standard scaler followed by logistic regression, gradient boosting or a random forest
needs only a few numbers per model. Keeping exactly those, and serving from them, means a model
can be stored as an .npz of numeric arrays and never as a pickle: loading it cannot run code, and
what is stored is, by construction, what scores.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np


class FrozenModelError(ValueError):
    pass


def _tree_arrays(trees: list[Any], *, probability: bool) -> dict[str, np.ndarray]:
    width = max(tree.tree_.node_count for tree in trees)
    shape = (len(trees), width)
    left = np.full(shape, -1, dtype=np.int64)
    right = np.full(shape, -1, dtype=np.int64)
    feature = np.zeros(shape, dtype=np.int64)
    threshold = np.zeros(shape, dtype=np.float64)
    value = np.zeros(shape, dtype=np.float64)
    depth = 0
    for position, tree in enumerate(trees):
        t = tree.tree_
        count = t.node_count
        left[position, :count] = t.children_left
        right[position, :count] = t.children_right
        feature[position, :count] = np.maximum(t.feature, 0)
        threshold[position, :count] = t.threshold
        if probability:
            counts = t.value[:, 0, :]
            totals = counts.sum(axis=1, keepdims=True)
            value[position, :count] = counts[:, 1] / np.where(totals == 0, 1.0, totals)[:, 0]
        else:
            value[position, :count] = t.value[:, 0, 0]
        depth = max(depth, int(t.max_depth))
    return {
        "left": left,
        "right": right,
        "feature": feature,
        "threshold": threshold,
        "value": value,
        "depth": np.array(depth),
    }


class FrozenPipeline:
    def __init__(self, arrays: dict[str, np.ndarray], kind: str) -> None:
        self.arrays = arrays
        self.kind = kind

    @classmethod
    def from_sklearn(cls, pipeline: Any) -> FrozenPipeline:
        scaler = pipeline.named_steps["scale"]
        estimator = pipeline.named_steps["estimate"]
        arrays: dict[str, np.ndarray] = {
            "scale_mean": np.asarray(scaler.mean_, dtype=np.float64),
            "scale_scale": np.asarray(scaler.scale_, dtype=np.float64),
        }
        name = type(estimator).__name__
        if name == "LogisticRegression":
            arrays["coef"] = np.asarray(estimator.coef_, dtype=np.float64).ravel()
            arrays["intercept"] = np.asarray(estimator.intercept_, dtype=np.float64).ravel()
            return cls(arrays, "logistic_regression")
        if name == "GradientBoostingClassifier":
            trees = [stage[0] for stage in estimator.estimators_]
            arrays.update(_tree_arrays(trees, probability=False))
            prior = float(estimator.init_.predict_proba(np.zeros((1, scaler.mean_.shape[0])))[0, 1])
            arrays["init_raw"] = np.array(np.log(prior / (1.0 - prior)))
            arrays["learning_rate"] = np.array(float(estimator.learning_rate))
            return cls(arrays, "gradient_boosting")
        if name == "RandomForestClassifier":
            arrays.update(_tree_arrays(list(estimator.estimators_), probability=True))
            return cls(arrays, "random_forest")
        raise FrozenModelError(f"cannot freeze an estimator of type {name}")

    def _traverse(self, x: np.ndarray) -> np.ndarray:
        a = self.arrays
        n_trees = a["left"].shape[0]
        node = np.zeros((n_trees, len(x)), dtype=np.int64)
        trees = np.arange(n_trees).reshape(-1, 1)
        rows = np.arange(len(x)).reshape(1, -1)
        for _ in range(int(a["depth"]) + 1):
            is_leaf = a["left"][trees, node] == -1
            goes_left = x[rows, a["feature"][trees, node]] <= a["threshold"][trees, node]
            step = np.where(goes_left, a["left"][trees, node], a["right"][trees, node])
            node = np.where(is_leaf, node, step)
        return np.asarray(a["value"][trees, node], dtype=np.float64)

    def predict_proba(self, matrix: np.ndarray) -> np.ndarray:
        """Probability of the positive class for each row."""
        a = self.arrays
        scaled = (np.asarray(matrix, dtype=np.float64) - a["scale_mean"]) / a["scale_scale"]
        if self.kind == "logistic_regression":
            raw = scaled @ a["coef"] + a["intercept"][0]
            return np.asarray(1.0 / (1.0 + np.exp(-raw)), dtype=np.float64)
        # scikit-learn compares float32 features with float64 thresholds; do the same.
        x = scaled.astype(np.float32)
        values = self._traverse(x)
        if self.kind == "gradient_boosting":
            raw = float(a["init_raw"]) + float(a["learning_rate"]) * values.sum(axis=0)
            return np.asarray(1.0 / (1.0 + np.exp(-raw)), dtype=np.float64)
        return np.asarray(values.mean(axis=0), dtype=np.float64)

    def to_arrays(self) -> dict[str, np.ndarray]:
        return {**self.arrays, "kind": np.array(self.kind)}

    @classmethod
    def from_arrays(cls, arrays: dict[str, np.ndarray]) -> FrozenPipeline:
        kind = str(arrays["kind"])
        if kind not in {"logistic_regression", "gradient_boosting", "random_forest"}:
            raise FrozenModelError(f"unknown model kind {kind!r}")
        return cls({k: v for k, v in arrays.items() if k != "kind"}, kind)


def encode_meta(meta: dict[str, Any]) -> np.ndarray:
    return np.array(json.dumps(meta, sort_keys=True))
