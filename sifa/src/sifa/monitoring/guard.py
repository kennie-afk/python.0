from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from sifa.evaluation.metrics import expected_calibration_error
from sifa.registry.models import ModelRegistry


@dataclass(frozen=True, slots=True)
class GuardThresholds:
    min_ctr_ratio: float = 0.85
    max_calibration_error: float = 0.15
    max_latency_ms: float = 250.0
    minimum_samples: int = 300


@dataclass(slots=True)
class ServingWindow:
    impressions: int = 0
    clicks: int = 0
    latencies: deque[float] = field(default_factory=lambda: deque(maxlen=2000))
    probabilities: list[float] = field(default_factory=list)
    outcomes: list[int] = field(default_factory=list)

    def record(self, clicked: bool, probability: float, latency_ms: float) -> None:
        self.impressions += 1
        self.clicks += int(clicked)
        self.latencies.append(latency_ms)
        self.probabilities.append(probability)
        self.outcomes.append(int(clicked))

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0

    @property
    def p95_latency(self) -> float:
        if not self.latencies:
            return 0.0
        ordered = sorted(self.latencies)
        return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]

    @property
    def calibration_error(self) -> float:
        return expected_calibration_error(self.probabilities, self.outcomes)


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    healthy: bool
    reasons: tuple[str, ...]
    ctr_ratio: float
    calibration_error: float
    p95_latency_ms: float


class RolloutGuard:
    def __init__(self, thresholds: GuardThresholds | None = None) -> None:
        self._thresholds = thresholds or GuardThresholds()

    def assess(self, baseline: ServingWindow, candidate: ServingWindow) -> GuardVerdict:
        reasons: list[str] = []

        ratio = candidate.ctr / baseline.ctr if baseline.ctr > 0 else 1.0
        calibration = candidate.calibration_error
        latency = candidate.p95_latency

        if candidate.impressions < self._thresholds.minimum_samples:
            return GuardVerdict(
                healthy=True,
                reasons=("not enough traffic to judge yet",),
                ctr_ratio=ratio,
                calibration_error=calibration,
                p95_latency_ms=latency,
            )

        if ratio < self._thresholds.min_ctr_ratio:
            reasons.append(
                f"click through is {ratio:.2f} of the live model against a floor of "
                f"{self._thresholds.min_ctr_ratio:.2f}"
            )
        if calibration > self._thresholds.max_calibration_error:
            reasons.append(
                f"calibration error {calibration:.3f} exceeds "
                f"{self._thresholds.max_calibration_error:.3f}"
            )
        if latency > self._thresholds.max_latency_ms:
            reasons.append(
                f"p95 latency {latency:.0f}ms exceeds {self._thresholds.max_latency_ms:.0f}ms"
            )

        return GuardVerdict(
            healthy=not reasons,
            reasons=tuple(reasons),
            ctr_ratio=ratio,
            calibration_error=calibration,
            p95_latency_ms=latency,
        )

    def enforce(
        self,
        registry: ModelRegistry,
        name: str,
        baseline: ServingWindow,
        candidate: ServingWindow,
    ) -> GuardVerdict:
        verdict = self.assess(baseline, candidate)
        if not verdict.healthy:
            registry.rollback(name, "; ".join(verdict.reasons))
        return verdict
