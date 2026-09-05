from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from aegis.verification.normalizers import Normalizer, identity


class Stability(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    NEAR_DETERMINISTIC = "NEAR_DETERMINISTIC"
    UNSTABLE = "UNSTABLE"
    NON_DETERMINISTIC = "NON_DETERMINISTIC"


class ProbeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DeterminismReport:
    case: str
    repetitions: int
    distinct_outputs: int
    modal_output: str
    modal_share: float
    stability: Stability
    variants: tuple[tuple[str, int], ...] = field(default=())
    failures: int = 0

    @property
    def is_deterministic(self) -> bool:
        return self.stability is Stability.DETERMINISTIC

    @property
    def passes(self) -> bool:
        return self.stability in (Stability.DETERMINISTIC, Stability.NEAR_DETERMINISTIC)

    def divergence_examples(self, limit: int = 3) -> tuple[str, ...]:
        return tuple(output for output, _ in self.variants if output != self.modal_output)[:limit]


class DeterminismProbe:
    def __init__(
        self,
        repetitions: int = 10,
        normalizer: Normalizer = identity,
        near_threshold: float = 0.95,
        unstable_threshold: float = 0.60,
        tolerate_failures: bool = False,
    ) -> None:
        if repetitions < 2:
            raise ValueError("determinism requires at least two repetitions to compare")
        if not 0.0 < unstable_threshold <= near_threshold <= 1.0:
            raise ValueError("thresholds must satisfy 0 < unstable <= near <= 1")

        self._repetitions = repetitions
        self._normalizer = normalizer
        self._near_threshold = near_threshold
        self._unstable_threshold = unstable_threshold
        self._tolerate_failures = tolerate_failures

    def probe(self, case: str, invoke: Callable[[], str]) -> DeterminismReport:
        outputs: list[str] = []
        failures = 0

        for _ in range(self._repetitions):
            try:
                outputs.append(self._normalizer(invoke()))
            except Exception as error:
                if not self._tolerate_failures:
                    raise ProbeError(f"case {case!r} raised during probing: {error}") from error
                failures += 1

        if not outputs:
            raise ProbeError(f"case {case!r} produced no successful executions")

        counts = Counter(outputs)
        modal_output, modal_count = counts.most_common(1)[0]
        modal_share = modal_count / len(outputs)

        return DeterminismReport(
            case=case,
            repetitions=len(outputs),
            distinct_outputs=len(counts),
            modal_output=modal_output,
            modal_share=modal_share,
            stability=self._classify(len(counts), modal_share),
            variants=tuple(counts.most_common()),
            failures=failures,
        )

    def probe_all(self, cases: Mapping[str, Callable[[], str]]) -> dict[str, DeterminismReport]:
        return {name: self.probe(name, invoke) for name, invoke in cases.items()}

    def _classify(self, distinct: int, modal_share: float) -> Stability:
        if distinct == 1:
            return Stability.DETERMINISTIC
        if modal_share >= self._near_threshold:
            return Stability.NEAR_DETERMINISTIC
        if modal_share >= self._unstable_threshold:
            return Stability.UNSTABLE
        return Stability.NON_DETERMINISTIC
