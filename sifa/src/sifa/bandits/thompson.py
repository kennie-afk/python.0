from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(slots=True)
class ArmState:
    successes: float = 1.0
    failures: float = 1.0

    @property
    def trials(self) -> float:
        return self.successes + self.failures - 2.0

    @property
    def mean(self) -> float:
        return self.successes / (self.successes + self.failures)


@dataclass(slots=True)
class ThompsonSampler:
    seed: int = 41
    decay: float = 1.0
    arms: dict[str, ArmState] = field(default_factory=dict)
    _rng: np.random.Generator = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not 0.0 < self.decay <= 1.0:
            raise ValueError("decay must sit in (0, 1]")
        self._rng = np.random.default_rng(self.seed)

    def register(self, arm: str) -> None:
        self.arms.setdefault(arm, ArmState())

    def select(self, arms: list[str]) -> str:
        if not arms:
            raise ValueError("cannot choose from an empty arm list")
        for arm in arms:
            self.register(arm)

        draws = {
            arm: float(self._rng.beta(self.arms[arm].successes, self.arms[arm].failures))
            for arm in arms
        }
        return max(draws, key=lambda arm: draws[arm])

    def update(self, arm: str, reward: float) -> None:
        if not 0.0 <= reward <= 1.0:
            raise ValueError("reward must sit between 0 and 1")
        self.register(arm)
        state = self.arms[arm]

        if self.decay < 1.0:
            state.successes = 1.0 + (state.successes - 1.0) * self.decay
            state.failures = 1.0 + (state.failures - 1.0) * self.decay

        state.successes += reward
        state.failures += 1.0 - reward

    def posterior(self, arm: str) -> tuple[float, float]:
        self.register(arm)
        state = self.arms[arm]
        total = state.successes + state.failures
        mean = state.successes / total
        variance = (state.successes * state.failures) / (total**2 * (total + 1.0))
        return mean, float(np.sqrt(variance))
