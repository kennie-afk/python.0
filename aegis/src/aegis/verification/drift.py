from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from scipy import stats

EPSILON = 1e-6


class DriftSeverity(StrEnum):
    STABLE = "STABLE"
    MODERATE = "MODERATE"
    SIGNIFICANT = "SIGNIFICANT"


class DriftError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DriftReport:
    feature: str
    metric: str
    statistic: float
    severity: DriftSeverity
    baseline_size: int
    candidate_size: int
    p_value: float | None = None
    contributions: tuple[tuple[str, float], ...] = ()

    @property
    def has_drifted(self) -> bool:
        return self.severity is not DriftSeverity.STABLE

    def top_contributors(self, limit: int = 3) -> tuple[tuple[str, float], ...]:
        ranked = sorted(self.contributions, key=lambda item: item[1], reverse=True)
        return tuple(ranked[:limit])


def _severity(statistic: float, moderate: float, significant: float) -> DriftSeverity:
    if statistic >= significant:
        return DriftSeverity.SIGNIFICANT
    if statistic >= moderate:
        return DriftSeverity.MODERATE
    return DriftSeverity.STABLE


def population_stability_index(
    feature: str,
    baseline: Sequence[float],
    candidate: Sequence[float],
    buckets: int = 10,
    moderate: float = 0.10,
    significant: float = 0.25,
) -> DriftReport:
    if len(baseline) < buckets or len(candidate) < 1:
        raise DriftError(
            f"feature {feature!r} needs at least {buckets} baseline and 1 candidate observation"
        )

    baseline_array = np.asarray(baseline, dtype=float)
    candidate_array = np.asarray(candidate, dtype=float)

    quantiles = np.linspace(0, 100, buckets + 1)
    edges = np.unique(np.percentile(baseline_array, quantiles))
    if edges.size < 2:
        return DriftReport(
            feature=feature,
            metric="psi",
            statistic=0.0,
            severity=DriftSeverity.STABLE,
            baseline_size=baseline_array.size,
            candidate_size=candidate_array.size,
        )

    edges[0], edges[-1] = -np.inf, np.inf

    baseline_counts, _ = np.histogram(baseline_array, bins=edges)
    candidate_counts, _ = np.histogram(candidate_array, bins=edges)

    baseline_ratio = np.clip(baseline_counts / baseline_array.size, EPSILON, None)
    candidate_ratio = np.clip(candidate_counts / candidate_array.size, EPSILON, None)

    per_bucket = (candidate_ratio - baseline_ratio) * np.log(candidate_ratio / baseline_ratio)
    statistic = float(np.sum(per_bucket))

    contributions = tuple(
        (f"bucket_{index}", float(value)) for index, value in enumerate(per_bucket)
    )

    return DriftReport(
        feature=feature,
        metric="psi",
        statistic=statistic,
        severity=_severity(statistic, moderate, significant),
        baseline_size=baseline_array.size,
        candidate_size=candidate_array.size,
        contributions=contributions,
    )


def categorical_drift(
    feature: str,
    baseline: Sequence[str],
    candidate: Sequence[str],
    moderate: float = 0.10,
    significant: float = 0.25,
) -> DriftReport:
    if not baseline or not candidate:
        raise DriftError(f"feature {feature!r} needs non-empty baseline and candidate samples")

    baseline_counts = Counter(baseline)
    candidate_counts = Counter(candidate)
    categories = sorted(set(baseline_counts) | set(candidate_counts))

    baseline_total = len(baseline)
    candidate_total = len(candidate)

    statistic = 0.0
    contributions: list[tuple[str, float]] = []

    for category in categories:
        baseline_ratio = max(baseline_counts.get(category, 0) / baseline_total, EPSILON)
        candidate_ratio = max(candidate_counts.get(category, 0) / candidate_total, EPSILON)
        contribution = (candidate_ratio - baseline_ratio) * float(
            np.log(candidate_ratio / baseline_ratio)
        )
        statistic += contribution
        contributions.append((category, contribution))

    return DriftReport(
        feature=feature,
        metric="categorical_psi",
        statistic=statistic,
        severity=_severity(statistic, moderate, significant),
        baseline_size=baseline_total,
        candidate_size=candidate_total,
        contributions=tuple(contributions),
    )


def distribution_shift(
    feature: str,
    baseline: Sequence[float],
    candidate: Sequence[float],
    alpha: float = 0.05,
) -> DriftReport:
    if len(baseline) < 2 or len(candidate) < 2:
        raise DriftError(f"feature {feature!r} needs at least two observations on each side")

    result = stats.ks_2samp(np.asarray(baseline, dtype=float), np.asarray(candidate, dtype=float))
    statistic = float(result.statistic)
    p_value = float(result.pvalue)

    if p_value >= alpha:
        severity = DriftSeverity.STABLE
    elif statistic < 0.2:
        severity = DriftSeverity.MODERATE
    else:
        severity = DriftSeverity.SIGNIFICANT

    return DriftReport(
        feature=feature,
        metric="ks_2samp",
        statistic=statistic,
        severity=severity,
        baseline_size=len(baseline),
        candidate_size=len(candidate),
        p_value=p_value,
    )
