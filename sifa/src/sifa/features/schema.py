from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from sifa.core.errors import SchemaError


class FeatureKind(StrEnum):
    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    EMBEDDING = "embedding"


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    name: str
    kind: FeatureKind
    default: float = 0.0
    dimension: int = 1

    def __post_init__(self) -> None:
        if not self.name or " " in self.name:
            raise SchemaError(f"feature name {self.name!r} must be non-empty and unspaced")
        if self.kind is FeatureKind.EMBEDDING and self.dimension < 2:
            raise SchemaError("an embedding feature needs a dimension of at least 2")


@dataclass(frozen=True, slots=True)
class FeatureView:
    name: str
    entity: str
    features: tuple[FeatureSpec, ...]
    ttl: timedelta = timedelta(days=30)

    def __post_init__(self) -> None:
        if not self.features:
            raise SchemaError(f"feature view {self.name!r} declares no features")
        names = [feature.name for feature in self.features]
        if len(names) != len(set(names)):
            raise SchemaError(f"feature view {self.name!r} has duplicate feature names")
        if self.ttl <= timedelta(0):
            raise SchemaError("a feature view needs a positive ttl")

    @property
    def feature_names(self) -> tuple[str, ...]:
        return tuple(feature.name for feature in self.features)

    def spec(self, name: str) -> FeatureSpec:
        for feature in self.features:
            if feature.name == name:
                return feature
        raise SchemaError(f"feature view {self.name!r} has no feature {name!r}")
