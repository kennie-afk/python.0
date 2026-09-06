from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from sifa.core.types import ScoredItem


@dataclass(frozen=True, slots=True)
class PolicyConfig:
    diversity_lambda: float = 0.3
    freshness_half_life: timedelta = timedelta(days=3)
    freshness_weight: float = 0.15
    max_per_source: int | None = None
    max_per_author: int | None = 3

    def __post_init__(self) -> None:
        if not 0.0 <= self.diversity_lambda <= 1.0:
            raise ValueError("diversity_lambda must sit between 0 and 1")
        if self.freshness_half_life <= timedelta(0):
            raise ValueError("the freshness half life must be positive")


def freshness_decay(age: timedelta, half_life: timedelta) -> float:
    if age <= timedelta(0):
        return 1.0
    return float(math.pow(0.5, age / half_life))


def apply_freshness(
    items: list[ScoredItem],
    published_at: dict[str, datetime],
    now: datetime,
    config: PolicyConfig,
) -> list[ScoredItem]:
    adjusted: list[ScoredItem] = []

    for item in items:
        published = published_at.get(item.item_id)
        if published is None:
            adjusted.append(item)
            continue

        decay = freshness_decay(now - published, config.freshness_half_life)
        blended = (1.0 - config.freshness_weight) * item.score + config.freshness_weight * (
            item.score * decay
        )
        adjusted.append(
            ScoredItem(
                item_id=item.item_id,
                score=blended,
                retrieval_score=item.retrieval_score,
                source=item.source,
                features=item.features,
                reasons=(*item.reasons, f"freshness x{decay:.2f}"),
            )
        )

    adjusted.sort(key=lambda item: item.score, reverse=True)
    return adjusted


def similarity_matrix(
    items: list[ScoredItem], vectors: dict[str, np.ndarray]
) -> np.ndarray:
    if not items:
        return np.zeros((0, 0), dtype=np.float32)

    dimension = next(
        (len(vectors[item.item_id]) for item in items if item.item_id in vectors), 0
    )
    if dimension == 0:
        return np.zeros((len(items), len(items)), dtype=np.float32)

    matrix = np.zeros((len(items), dimension), dtype=np.float32)
    for row, item in enumerate(items):
        vector = vectors.get(item.item_id)
        if vector is None:
            continue
        norm = float(np.linalg.norm(vector))
        if norm > 0.0:
            matrix[row] = np.asarray(vector, dtype=np.float32) / norm

    return matrix @ matrix.T


def maximal_marginal_relevance(
    items: list[ScoredItem],
    similarity: Callable[[str, str], float] | np.ndarray,
    k: int,
    lambda_: float,
) -> list[ScoredItem]:
    if not items or k <= 0:
        return []

    if isinstance(similarity, np.ndarray):
        matrix = similarity
    else:
        matrix = np.array(
            [[similarity(a.item_id, b.item_id) for b in items] for a in items],
            dtype=np.float32,
        )

    scores = np.array([item.score for item in items], dtype=np.float32)
    remaining = np.ones(len(items), dtype=bool)

    first = 0
    remaining[first] = False
    order = [first]
    worst = matrix[first].copy()

    while len(order) < min(k, len(items)):
        values = lambda_ * scores - (1.0 - lambda_) * worst
        values[~remaining] = -np.inf
        pick = int(np.argmax(values))
        if not np.isfinite(values[pick]):
            break
        remaining[pick] = False
        order.append(pick)
        worst = np.maximum(worst, matrix[pick])

    selected = [items[order[0]]]
    for position in order[1:]:
        chosen = items[position]
        selected.append(
            ScoredItem(
                item_id=chosen.item_id,
                score=chosen.score,
                retrieval_score=chosen.retrieval_score,
                source=chosen.source,
                features=chosen.features,
                reasons=(*chosen.reasons, "kept for diversity"),
            )
        )

    return selected


def cap_per_attribute(
    items: list[ScoredItem], attribute: dict[str, str], limit: int
) -> list[ScoredItem]:
    counts: dict[str, int] = {}
    kept: list[ScoredItem] = []

    for item in items:
        key = attribute.get(item.item_id, "")
        if key and counts.get(key, 0) >= limit:
            continue
        counts[key] = counts.get(key, 0) + 1
        kept.append(item)

    return kept


def cosine_similarity_lookup(
    vectors: dict[str, np.ndarray],
) -> Callable[[str, str], float]:
    def similarity(left: str, right: str) -> float:
        a = vectors.get(left)
        b = vectors.get(right)
        if a is None or b is None:
            return 0.0
        denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denominator == 0.0:
            return 0.0
        return float(np.dot(a, b)) / denominator

    return similarity
