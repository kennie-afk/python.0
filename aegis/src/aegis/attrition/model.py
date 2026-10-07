from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from aegis.attrition.features import (
    FEATURE_NAMES,
    EmployeeSnapshot,
    FeatureError,
    RiskBand,
    assert_no_protected_attributes,
    build_features,
)
from aegis.attrition.frozen import FrozenPipeline

MINIMUM_TRAINING_ROWS = 40
# How many training rows are kept as the model's reference distribution for drift checks.
REFERENCE_ROWS = 2000
FORMAT_VERSION = 1

class ModelError(RuntimeError):
    pass

@dataclass(frozen=True, slots=True)
class Driver:
    feature: str
    contribution: float
    direction: str

@dataclass(frozen=True, slots=True)
class AttritionScore:
    subject_key: str
    probability: float
    band: RiskBand
    drivers: tuple[Driver, ...]

    @property
    def needs_intervention(self) -> bool:
        return self.band is RiskBand.HIGH

    def top_drivers(self, limit: int = 3) -> tuple[Driver, ...]:
        return self.drivers[:limit]

@dataclass(frozen=True, slots=True)
class TrainingReport:
    rows: int
    positives: int
    algorithm: str
    feature_importance: tuple[tuple[str, float], ...]

    @property
    def positive_rate(self) -> float:
        return self.positives / self.rows if self.rows else 0.0

def _estimator(algorithm: str) -> object:
    if algorithm == "gradient_boosting":
        return GradientBoostingClassifier(random_state=0)
    if algorithm == "random_forest":
        return RandomForestClassifier(n_estimators=200, random_state=0)
    if algorithm == "logistic_regression":
        return LogisticRegression(max_iter=1000, random_state=0)
    raise ModelError(f"unknown algorithm {algorithm!r}")

class AttritionModel:
    def __init__(self, algorithm: str = "gradient_boosting") -> None:
        self._algorithm = algorithm
        self._frozen: FrozenPipeline | None = None
        self._baseline: np.ndarray | None = None
        self._importance: tuple[tuple[str, float], ...] = ()
        self._reference: np.ndarray | None = None
        self._data_hash = ""

    @property
    def is_trained(self) -> bool:
        return self._frozen is not None

    @property
    def data_hash(self) -> str:
        """SHA-256 over the training matrix and outcomes: which data this model was fitted on."""
        return self._data_hash

    @property
    def reference(self) -> np.ndarray:
        """Up to REFERENCE_ROWS training rows, one column per feature, for drift checks."""
        if self._reference is None:
            raise ModelError("model must be trained before a reference is available")
        return self._reference

    def features_matrix(self, snapshots: Sequence[EmployeeSnapshot]) -> np.ndarray:
        return self._matrix(snapshots)

    def predict_matrix(self, matrix: np.ndarray) -> np.ndarray:
        if self._frozen is None:
            raise ModelError("model must be trained before scoring")
        return self._frozen.predict_proba(matrix)

    @property
    def algorithm(self) -> str:
        return self._algorithm

    def train(self, snapshots: Sequence[EmployeeSnapshot], left: Sequence[bool]) -> TrainingReport:
        if len(snapshots) != len(left):
            raise ModelError("snapshots and outcomes must be the same length")
        if len(snapshots) < MINIMUM_TRAINING_ROWS:
            raise ModelError(
                f"attrition training needs at least {MINIMUM_TRAINING_ROWS} rows, "
                f"got {len(snapshots)}; a model fitted on fewer is not evidence"
            )

        positives = sum(1 for value in left if value)
        if positives == 0 or positives == len(left):
            raise ModelError(
                "training data must contain both employees who left and employees who stayed"
            )

        matrix = self._matrix(snapshots)
        assert_no_protected_attributes(FEATURE_NAMES)

        pipeline = Pipeline(
            [("scale", StandardScaler()), ("estimate", _estimator(self._algorithm))]
        )
        pipeline.fit(matrix, np.asarray(left, dtype=int))

        self._frozen = FrozenPipeline.from_sklearn(pipeline)
        self._baseline = matrix.mean(axis=0)
        self._importance = self._extract_importance(pipeline)
        step = max(1, len(matrix) // REFERENCE_ROWS)
        self._reference = matrix[::step][:REFERENCE_ROWS].copy()
        digest = hashlib.sha256()
        digest.update(np.ascontiguousarray(matrix).tobytes())
        digest.update(np.asarray(left, dtype=np.int8).tobytes())
        self._data_hash = digest.hexdigest()

        return TrainingReport(
            rows=len(snapshots),
            positives=positives,
            algorithm=self._algorithm,
            feature_importance=self._importance,
        )

    def score(self, snapshot: EmployeeSnapshot) -> AttritionScore:
        if self._frozen is None or self._baseline is None:
            raise ModelError("model must be trained before scoring")

        features = build_features(snapshot)
        vector = np.array([[features[name] for name in FEATURE_NAMES]], dtype=float)
        probability = float(self._frozen.predict_proba(vector)[0])

        return AttritionScore(
            subject_key=snapshot.subject_key,
            probability=probability,
            band=RiskBand.from_probability(probability),
            drivers=self._drivers(vector[0]),
        )

    def score_all(self, snapshots: Sequence[EmployeeSnapshot]) -> tuple[AttritionScore, ...]:
        return tuple(self.score(snapshot) for snapshot in snapshots)

    def to_bytes(self) -> bytes:
        """The trained model as an .npz of plain numeric arrays. No pickle is involved."""
        if self._frozen is None or self._baseline is None or self._reference is None:
            raise ModelError("model must be trained before it can be stored")
        meta = {
            "format": FORMAT_VERSION,
            "algorithm": self._algorithm,
            "features": list(FEATURE_NAMES),
            "importance": [[name, weight] for name, weight in self._importance],
            "data_hash": self._data_hash,
        }
        buffer = io.BytesIO()
        np.savez(
            buffer,
            **{  # type: ignore[arg-type]
                **{f"m_{k}": v for k, v in self._frozen.to_arrays().items()},
                "baseline": self._baseline,
                "reference": self._reference,
                "meta": np.array(json.dumps(meta, sort_keys=True)),
            },
        )
        return buffer.getvalue()

    @classmethod
    def from_bytes(cls, blob: bytes) -> AttritionModel:
        try:
            with np.load(io.BytesIO(blob), allow_pickle=False) as archive:
                arrays = {name: archive[name] for name in archive.files}
            meta = json.loads(str(arrays["meta"]))
            if meta["format"] != FORMAT_VERSION:
                raise ModelError(f"unsupported model format {meta['format']}")
            if meta["features"] != list(FEATURE_NAMES):
                raise ModelError("the stored model was trained on a different feature set")
            model = cls(str(meta["algorithm"]))
            model._frozen = FrozenPipeline.from_arrays(
                {k[2:]: v for k, v in arrays.items() if k.startswith("m_")}
            )
            model._baseline = np.asarray(arrays["baseline"], dtype=float)
            model._reference = np.asarray(arrays["reference"], dtype=float)
            model._importance = tuple((str(n), float(w)) for n, w in meta["importance"])
            model._data_hash = str(meta["data_hash"])
        except ModelError:
            raise
        except Exception as error:  # a malformed blob is refused, never partly loaded
            raise ModelError(f"stored model could not be read: {error}") from error
        return model

    def feature_importance(self) -> tuple[tuple[str, float], ...]:
        if not self._importance:
            raise ModelError("model must be trained before importance is available")
        return self._importance

    def _matrix(self, snapshots: Sequence[EmployeeSnapshot]) -> np.ndarray:
        if not snapshots:
            raise FeatureError("no snapshots supplied")
        rows = []
        for snapshot in snapshots:
            features = build_features(snapshot)
            rows.append([features[name] for name in FEATURE_NAMES])
        return np.asarray(rows, dtype=float)

    def _drivers(self, vector: np.ndarray) -> tuple[Driver, ...]:
        if self._baseline is None:
            return ()

        deltas = vector - self._baseline
        spread = np.where(np.abs(self._baseline) > 1e-9, np.abs(self._baseline), 1.0)
        normalised = deltas / spread

        contributions = [
            (name, float(abs(normalised[index]) * weight), float(normalised[index]))
            for index, (name, weight) in enumerate(self._importance)
        ]
        contributions.sort(key=lambda item: item[1], reverse=True)

        return tuple(
            Driver(
                feature=name,
                contribution=round(score, 4),
                direction="above cohort" if delta > 0 else "below cohort",
            )
            for name, score, delta in contributions
            if score > 0.0
        )

    def _extract_importance(self, pipeline: Pipeline) -> tuple[tuple[str, float], ...]:
        estimator = pipeline.named_steps["estimate"]

        if hasattr(estimator, "feature_importances_"):
            weights = np.asarray(estimator.feature_importances_, dtype=float)
        else:
            weights = np.abs(np.asarray(estimator.coef_, dtype=float)).ravel()

        total = float(weights.sum())
        if total > 0:
            weights = weights / total

        return tuple(zip(FEATURE_NAMES, (float(value) for value in weights), strict=True))
