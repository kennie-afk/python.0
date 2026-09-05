from __future__ import annotations

import math
from collections.abc import Sequence


def dcg(relevances: Sequence[float], k: int | None = None) -> float:
    limit = len(relevances) if k is None else min(k, len(relevances))
    total = sum((2.0 ** relevances[i] - 1.0) / math.log2(i + 2) for i in range(limit))
    return float(total)


def ndcg(relevances: Sequence[float], k: int | None = None) -> float:
    ideal = dcg(sorted(relevances, reverse=True), k)
    if ideal == 0.0:
        return 0.0
    return dcg(relevances, k) / ideal


def recall_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    hits = len(set(retrieved[:k]) & relevant)
    return hits / len(relevant)


def precision_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    if k == 0:
        return 0.0
    return len(set(retrieved[:k]) & relevant) / k


def mean_reciprocal_rank(retrieved: Sequence[str], relevant: set[str]) -> float:
    for position, item in enumerate(retrieved, start=1):
        if item in relevant:
            return 1.0 / position
    return 0.0


def average_precision(retrieved: Sequence[str], relevant: set[str]) -> float:
    if not relevant:
        return 0.0
    hits = 0
    total = 0.0
    for position, item in enumerate(retrieved, start=1):
        if item in relevant:
            hits += 1
            total += hits / position
    return total / len(relevant)


def expected_calibration_error(
    probabilities: Sequence[float], outcomes: Sequence[int], bins: int = 10
) -> float:
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must be the same length")
    if not probabilities:
        return 0.0

    total = len(probabilities)
    error = 0.0

    for index in range(bins):
        low = index / bins
        high = (index + 1) / bins
        members = [
            (p, o)
            for p, o in zip(probabilities, outcomes, strict=True)
            if (low < p <= high) or (index == 0 and p == 0.0)
        ]
        if not members:
            continue
        confidence = sum(p for p, _ in members) / len(members)
        accuracy = sum(o for _, o in members) / len(members)
        error += (len(members) / total) * abs(accuracy - confidence)

    return error


def intra_list_diversity(similarities: Sequence[Sequence[float]]) -> float:
    n = len(similarities)
    if n < 2:
        return 0.0
    total = 0.0
    pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            total += 1.0 - similarities[i][j]
            pairs += 1
    return total / pairs if pairs else 0.0
