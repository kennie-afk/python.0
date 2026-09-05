from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import stats


@dataclass(frozen=True, slots=True)
class DriftReport:
    feature: str
    psi: float
    ks_statistic: float
    p_value: float
    drifted: bool
    severity: str


LOW_CARDINALITY = 12


def _snap(values: np.ndarray, categories: np.ndarray) -> np.ndarray:
    positions = np.abs(values.reshape(-1, 1) - categories.reshape(1, -1)).argmin(axis=1)
    return np.asarray(positions, dtype=np.int64)


def _discrete_psi(reference: np.ndarray, live: np.ndarray, categories: np.ndarray) -> float:
    reference_bins = np.bincount(_snap(reference, categories), minlength=len(categories))
    live_bins = np.bincount(_snap(live, categories), minlength=len(categories))

    reference_share = np.clip(reference_bins / len(reference), 1e-6, None)
    live_share = np.clip(live_bins / len(live), 1e-6, None)

    return float(np.sum((live_share - reference_share) * np.log(live_share / reference_share)))


def population_stability_index(
    reference: Sequence[float], live: Sequence[float], bins: int = 10
) -> float:
    if len(reference) < 2 or len(live) < 2:
        return 0.0

    reference_array = np.asarray(reference, dtype=np.float64)
    live_array = np.asarray(live, dtype=np.float64)

    categories = np.unique(np.round(reference_array, 9))
    if len(categories) <= LOW_CARDINALITY:
        return _discrete_psi(reference_array, live_array, categories)

    edges = np.quantile(reference_array, np.linspace(0, 1, bins + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    edges = np.unique(edges)
    if len(edges) < 3:
        return 0.0

    reference_counts, _ = np.histogram(reference_array, bins=edges)
    live_counts, _ = np.histogram(live_array, bins=edges)

    reference_share = np.clip(reference_counts / len(reference_array), 1e-6, None)
    live_share = np.clip(live_counts / len(live_array), 1e-6, None)

    return float(np.sum((live_share - reference_share) * np.log(live_share / reference_share)))


def detect_drift(
    feature: str,
    reference: Sequence[float],
    live: Sequence[float],
    psi_warn: float = 0.1,
    psi_alert: float = 0.25,
    alpha: float = 0.01,
) -> DriftReport:
    psi = population_stability_index(reference, live)

    if len(reference) >= 2 and len(live) >= 2:
        statistic, p_value = stats.ks_2samp(reference, live)
    else:
        statistic, p_value = 0.0, 1.0

    if psi >= psi_alert:
        severity = "alert"
    elif psi >= psi_warn:
        severity = "warn"
    else:
        severity = "stable"

    return DriftReport(
        feature=feature,
        psi=psi,
        ks_statistic=float(statistic),
        p_value=float(p_value),
        drifted=bool(severity != "stable" and float(p_value) < alpha),
        severity=severity,
    )
