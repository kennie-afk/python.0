from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sifa.core.errors import ExperimentError


@dataclass(frozen=True, slots=True)
class Variant:
    name: str
    weight: float

    def __post_init__(self) -> None:
        if self.weight <= 0:
            raise ExperimentError(f"variant {self.name!r} needs a positive weight")


@dataclass(frozen=True, slots=True)
class Experiment:
    key: str
    variants: tuple[Variant, ...]
    salt: str = "sifa"
    holdout: float = 0.0

    def __post_init__(self) -> None:
        if len(self.variants) < 2:
            raise ExperimentError("an experiment needs at least two variants")
        names = [variant.name for variant in self.variants]
        if len(names) != len(set(names)):
            raise ExperimentError("variant names must be unique")
        if not 0.0 <= self.holdout < 1.0:
            raise ExperimentError("holdout must sit between 0 and 1")

    @property
    def total_weight(self) -> float:
        return sum(variant.weight for variant in self.variants)


def _bucket(unit_id: str, key: str, salt: str) -> float:
    digest = hashlib.sha256(f"{salt}:{key}:{unit_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def assign(experiment: Experiment, unit_id: str) -> str:
    if not unit_id:
        raise ExperimentError("an assignment needs a stable unit id")

    position = _bucket(unit_id, experiment.key, experiment.salt)

    if position < experiment.holdout:
        return "holdout"

    remaining = (position - experiment.holdout) / (1.0 - experiment.holdout)
    cursor = 0.0
    for variant in experiment.variants:
        cursor += variant.weight / experiment.total_weight
        if remaining < cursor:
            return variant.name

    return experiment.variants[-1].name
