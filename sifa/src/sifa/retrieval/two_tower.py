from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sifa.core.errors import NotTrainedError
from sifa.core.types import Candidate
from sifa.index.hnsw import HnswConfig, HnswIndex, normalise


@dataclass(frozen=True, slots=True)
class TwoTowerConfig:
    dimension: int = 64
    learning_rate: float = 0.08
    epochs: int = 12
    negatives: int = 8
    seed: int = 23
    l2: float = 1e-5


class TwoTowerModel:
    def __init__(self, config: TwoTowerConfig | None = None) -> None:
        self._config = config or TwoTowerConfig()
        self._rng = np.random.default_rng(self._config.seed)
        self._users: dict[str, int] = {}
        self._items: dict[str, int] = {}
        self._user_vectors: np.ndarray | None = None
        self._item_vectors: np.ndarray | None = None
        self._item_keys: list[str] = []

    @property
    def dimension(self) -> int:
        return self._config.dimension

    @property
    def is_trained(self) -> bool:
        return self._user_vectors is not None and self._item_vectors is not None

    def fit(self, interactions: list[tuple[str, str]]) -> dict[str, float]:
        if not interactions:
            raise NotTrainedError("a two tower model needs at least one interaction")

        users = sorted({user for user, _ in interactions})
        items = sorted({item for _, item in interactions})
        self._users = {user: i for i, user in enumerate(users)}
        self._items = {item: i for i, item in enumerate(items)}
        self._item_keys = items

        d = self._config.dimension
        scale = 1.0 / np.sqrt(d)
        user_vectors = self._rng.normal(scale=scale, size=(len(users), d)).astype(np.float32)
        item_vectors = self._rng.normal(scale=scale, size=(len(items), d)).astype(np.float32)

        pairs = np.array(
            [(self._users[u], self._items[i]) for u, i in interactions], dtype=np.int64
        )
        losses: list[float] = []

        for _ in range(self._config.epochs):
            self._rng.shuffle(pairs)
            epoch_loss = 0.0

            for user_index, item_index in pairs:
                negatives = self._rng.integers(
                    0, len(items), size=self._config.negatives, dtype=np.int64
                )
                user_vector = user_vectors[user_index]
                positive = item_vectors[item_index]
                negative_vectors = item_vectors[negatives]

                positive_score = float(user_vector @ positive)
                negative_scores = negative_vectors @ user_vector

                logits = np.concatenate(([positive_score], negative_scores))
                logits -= logits.max()
                weights = np.exp(logits)
                weights /= weights.sum()

                epoch_loss += -float(np.log(max(weights[0], 1e-12)))

                grad_user = (weights[0] - 1.0) * positive
                for offset, negative_index in enumerate(negatives):
                    grad_user = grad_user + weights[offset + 1] * item_vectors[negative_index]

                item_vectors[item_index] -= self._config.learning_rate * (
                    (weights[0] - 1.0) * user_vector + self._config.l2 * positive
                )
                for offset, negative_index in enumerate(negatives):
                    item_vectors[negative_index] -= self._config.learning_rate * (
                        weights[offset + 1] * user_vector
                    )

                user_vectors[user_index] -= self._config.learning_rate * (
                    grad_user + self._config.l2 * user_vector
                )

            losses.append(epoch_loss / max(len(pairs), 1))

        self._user_vectors = np.vstack([normalise(v) for v in user_vectors]).astype(np.float32)
        self._item_vectors = np.vstack([normalise(v) for v in item_vectors]).astype(np.float32)

        return {
            "epochs": float(self._config.epochs),
            "first_loss": losses[0],
            "final_loss": losses[-1],
            "users": float(len(users)),
            "items": float(len(items)),
        }

    def user_vector(self, user_id: str) -> np.ndarray:
        if self._user_vectors is None:
            raise NotTrainedError("the two tower model has not been trained")
        index = self._users.get(user_id)
        if index is None:
            return np.zeros(self._config.dimension, dtype=np.float32)
        return np.asarray(self._user_vectors[index], dtype=np.float32)

    def item_vector(self, item_id: str) -> np.ndarray:
        if self._item_vectors is None:
            raise NotTrainedError("the two tower model has not been trained")
        index = self._items.get(item_id)
        if index is None:
            return np.zeros(self._config.dimension, dtype=np.float32)
        return np.asarray(self._item_vectors[index], dtype=np.float32)

    def build_index(self, config: HnswConfig | None = None) -> HnswIndex:
        if self._item_vectors is None:
            raise NotTrainedError("train the model before building an index")
        index = HnswIndex(self._config.dimension, config)
        for key, vector in zip(self._item_keys, self._item_vectors, strict=True):
            index.add(key, vector)
        return index


class Retriever:
    def __init__(self, model: TwoTowerModel, index: HnswIndex) -> None:
        self._model = model
        self._index = index

    @property
    def index(self) -> HnswIndex:
        return self._index

    @property
    def model(self) -> TwoTowerModel:
        return self._model

    def retrieve(
        self, user_id: str, k: int, exclude: set[str] | None = None, ef: int | None = None
    ) -> list[Candidate]:
        vector = self._model.user_vector(user_id)
        if not np.any(vector):
            return []

        blocked = exclude or set()
        overshoot = k + len(blocked)
        results = self._index.search(vector, overshoot, ef=ef)

        return [
            Candidate(item_id=key, retrieval_score=score, source="two_tower")
            for key, score in results
            if key not in blocked
        ][:k]
