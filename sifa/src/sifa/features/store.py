from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sifa.core.clock import ensure_utc
from sifa.core.errors import LeakageError, SchemaError
from sifa.features.schema import FeatureView


@dataclass(frozen=True, slots=True)
class FeatureRow:
    entity_id: str
    event_time: datetime
    values: dict[str, float]


@dataclass(frozen=True, slots=True)
class TrainingExample:
    entity_id: str
    label_time: datetime
    label: float
    features: dict[str, float]


class FeatureStore:
    def __init__(self, view: FeatureView) -> None:
        self._view = view
        self._times: dict[str, list[float]] = defaultdict(list)
        self._rows: dict[str, list[FeatureRow]] = defaultdict(list)

    @property
    def view(self) -> FeatureView:
        return self._view

    def write(self, entity_id: str, event_time: datetime, values: dict[str, float]) -> None:
        moment = ensure_utc(event_time)

        unknown = set(values) - set(self._view.feature_names)
        if unknown:
            raise SchemaError(
                f"feature view {self._view.name!r} does not declare {sorted(unknown)}"
            )

        row = FeatureRow(entity_id=entity_id, event_time=moment, values=dict(values))
        key = moment.timestamp()
        times = self._times[entity_id]
        position = bisect.bisect_left(times, key)
        times.insert(position, key)
        self._rows[entity_id].insert(position, row)

    def write_many(self, rows: list[FeatureRow]) -> None:
        for row in rows:
            self.write(row.entity_id, row.event_time, row.values)

    def latest(self, entity_id: str, as_of: datetime | None = None) -> dict[str, float]:
        moment = ensure_utc(as_of) if as_of else None
        return self._as_of(entity_id, moment)

    def _as_of(self, entity_id: str, moment: datetime | None) -> dict[str, float]:
        resolved = {
            feature.name: feature.default for feature in self._view.features
        }

        times = self._times.get(entity_id)
        rows = self._rows.get(entity_id)
        if not times or not rows:
            return resolved

        cutoff = len(times) if moment is None else bisect.bisect_right(times, moment.timestamp())
        if cutoff == 0:
            return resolved

        horizon = None if moment is None else moment - self._view.ttl

        for index in range(cutoff - 1, -1, -1):
            row = rows[index]
            if horizon is not None and row.event_time < horizon:
                break
            for name, value in row.values.items():
                if name not in resolved or resolved[name] == self._view.spec(name).default:
                    resolved[name] = value

        return resolved

    def point_in_time_join(
        self,
        labels: list[tuple[str, datetime, float]],
        embargo: timedelta = timedelta(0),
    ) -> list[TrainingExample]:
        examples: list[TrainingExample] = []

        for entity_id, label_time, label in labels:
            moment = ensure_utc(label_time)
            cutoff = moment - embargo
            features = self._as_of(entity_id, cutoff)
            examples.append(
                TrainingExample(
                    entity_id=entity_id,
                    label_time=moment,
                    label=label,
                    features=features,
                )
            )

        return examples

    def assert_no_leakage(
        self, examples: list[TrainingExample], embargo: timedelta = timedelta(0)
    ) -> None:
        for example in examples:
            cutoff = example.label_time - embargo
            times = self._times.get(example.entity_id, [])
            rows = self._rows.get(example.entity_id, [])

            future = [
                row
                for row, stamp in zip(rows, times, strict=True)
                if stamp > cutoff.timestamp()
            ]
            if not future:
                continue

            for row in future:
                for name, value in row.values.items():
                    default = self._view.spec(name).default
                    if example.features.get(name) == value and value != default:
                        earlier = any(
                            other.event_time <= cutoff and other.values.get(name) == value
                            for other in rows
                        )
                        if not earlier:
                            raise LeakageError(
                                f"feature {name!r} for {example.entity_id!r} came from "
                                f"{row.event_time.isoformat()}, which is after the label at "
                                f"{example.label_time.isoformat()}"
                            )

    def size(self) -> int:
        return sum(len(rows) for rows in self._rows.values())
