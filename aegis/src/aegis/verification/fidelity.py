from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from aegis.verification.determinism import DeterminismReport, Stability
from aegis.verification.drift import DriftReport, DriftSeverity

STABILITY_WEIGHT: dict[Stability, float] = {
    Stability.DETERMINISTIC: 1.0,
    Stability.NEAR_DETERMINISTIC: 0.85,
    Stability.UNSTABLE: 0.45,
    Stability.NON_DETERMINISTIC: 0.0,
}

DRIFT_WEIGHT: dict[DriftSeverity, float] = {
    DriftSeverity.STABLE: 1.0,
    DriftSeverity.MODERATE: 0.6,
    DriftSeverity.SIGNIFICANT: 0.0,
}


class Gate(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"


@dataclass(frozen=True, slots=True)
class FidelityReport:
    score: float
    gate: Gate
    determinism_score: float
    drift_score: float
    findings: tuple[str, ...]

    @property
    def deployable(self) -> bool:
        return self.gate is not Gate.BLOCK


class FidelityScorer:
    def __init__(self, warn_below: float = 0.90, block_below: float = 0.70) -> None:
        if not 0.0 <= block_below <= warn_below <= 1.0:
            raise ValueError("thresholds must satisfy 0 <= block <= warn <= 1")
        self._warn_below = warn_below
        self._block_below = block_below

    def score(
        self,
        determinism: Sequence[DeterminismReport],
        drift: Sequence[DriftReport],
    ) -> FidelityReport:
        determinism_score = self._mean(
            [STABILITY_WEIGHT[report.stability] for report in determinism]
        )
        drift_score = self._mean([DRIFT_WEIGHT[report.severity] for report in drift])

        if determinism and drift:
            combined = 0.6 * determinism_score + 0.4 * drift_score
        elif determinism:
            combined = determinism_score
        elif drift:
            combined = drift_score
        else:
            raise ValueError("fidelity requires at least one determinism or drift report")

        return FidelityReport(
            score=combined,
            gate=self._gate(combined),
            determinism_score=determinism_score,
            drift_score=drift_score,
            findings=self._findings(determinism, drift),
        )

    def _gate(self, score: float) -> Gate:
        if score < self._block_below:
            return Gate.BLOCK
        if score < self._warn_below:
            return Gate.WARN
        return Gate.PASS

    @staticmethod
    def _mean(values: Sequence[float]) -> float:
        return sum(values) / len(values) if values else 1.0

    @staticmethod
    def _findings(
        determinism: Sequence[DeterminismReport],
        drift: Sequence[DriftReport],
    ) -> tuple[str, ...]:
        findings: list[str] = []

        for stability_report in determinism:
            if not stability_report.passes:
                findings.append(
                    f"{stability_report.case}: {stability_report.stability} across "
                    f"{stability_report.repetitions} runs, "
                    f"{stability_report.distinct_outputs} distinct outputs, "
                    f"modal share {stability_report.modal_share:.0%}"
                )

        for drift_report in drift:
            if drift_report.has_drifted:
                findings.append(
                    f"{drift_report.feature}: {drift_report.severity} drift, "
                    f"{drift_report.metric} {drift_report.statistic:.4f}"
                )

        return tuple(findings)
