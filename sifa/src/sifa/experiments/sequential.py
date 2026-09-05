from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from sifa.core.errors import ExperimentError


class Decision(StrEnum):
    CONTINUE = "continue"
    TREATMENT_WINS = "treatment_wins"
    CONTROL_WINS = "control_wins"
    NO_DIFFERENCE = "no_difference"


@dataclass(frozen=True, slots=True)
class SequentialResult:
    decision: Decision
    likelihood_ratio: float
    control_rate: float
    treatment_rate: float
    lift: float
    samples: int
    threshold: float


@dataclass(frozen=True, slots=True)
class MixtureSprt:
    alpha: float = 0.05
    tau: float = 0.01
    minimum_samples: int = 200


    def __post_init__(self) -> None:
        if not 0.0 < self.alpha < 0.5:
            raise ExperimentError("alpha must sit between 0 and 0.5")
        if self.tau <= 0:
            raise ExperimentError("tau must be positive")

    @property
    def threshold(self) -> float:
        return 1.0 / self.alpha

    def evaluate(
        self,
        control_successes: int,
        control_trials: int,
        treatment_successes: int,
        treatment_trials: int,
    ) -> SequentialResult:
        for value, name in [
            (control_successes, "control_successes"),
            (control_trials, "control_trials"),
            (treatment_successes, "treatment_successes"),
            (treatment_trials, "treatment_trials"),
        ]:
            if value < 0:
                raise ExperimentError(f"{name} cannot be negative")
        if control_successes > control_trials or treatment_successes > treatment_trials:
            raise ExperimentError("successes cannot exceed trials")

        samples = control_trials + treatment_trials
        control_rate = control_successes / control_trials if control_trials else 0.0
        treatment_rate = treatment_successes / treatment_trials if treatment_trials else 0.0
        lift = (treatment_rate - control_rate) / control_rate if control_rate > 0 else 0.0

        if control_trials == 0 or treatment_trials == 0 or samples < self.minimum_samples:
            return SequentialResult(
                decision=Decision.CONTINUE,
                likelihood_ratio=1.0,
                control_rate=control_rate,
                treatment_rate=treatment_rate,
                lift=lift,
                samples=samples,
                threshold=self.threshold,
            )

        pooled = (control_successes + treatment_successes) / samples
        variance = pooled * (1.0 - pooled)

        if variance <= 0.0:
            return SequentialResult(
                decision=Decision.CONTINUE,
                likelihood_ratio=1.0,
                control_rate=control_rate,
                treatment_rate=treatment_rate,
                lift=lift,
                samples=samples,
                threshold=self.threshold,
            )

        effective = 1.0 / (1.0 / control_trials + 1.0 / treatment_trials)
        difference = treatment_rate - control_rate

        prior_variance = self.tau**2
        spread = variance + effective * prior_variance
        shrink = variance / spread
        exponent = (effective**2 * prior_variance * difference**2) / (
            2.0 * variance * spread
        )
        ratio = math.sqrt(shrink) * math.exp(min(exponent, 700.0))

        if ratio >= self.threshold:
            decision = Decision.TREATMENT_WINS if difference > 0 else Decision.CONTROL_WINS
        elif ratio <= 1.0 / self.threshold:
            decision = Decision.NO_DIFFERENCE
        else:
            decision = Decision.CONTINUE

        return SequentialResult(
            decision=decision,
            likelihood_ratio=ratio,
            control_rate=control_rate,
            treatment_rate=treatment_rate,
            lift=lift,
            samples=samples,
            threshold=self.threshold,
        )
