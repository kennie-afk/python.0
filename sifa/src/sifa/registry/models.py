from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from sifa.core.clock import now
from sifa.core.errors import RegistryError


class Stage(StrEnum):
    DRAFT = "draft"
    SHADOW = "shadow"
    CANARY = "canary"
    LIVE = "live"
    ROLLED_BACK = "rolled_back"
    ARCHIVED = "archived"


ALLOWED: dict[Stage, set[Stage]] = {
    Stage.DRAFT: {Stage.SHADOW, Stage.ARCHIVED},
    Stage.SHADOW: {Stage.CANARY, Stage.ARCHIVED, Stage.ROLLED_BACK},
    Stage.CANARY: {Stage.LIVE, Stage.ROLLED_BACK},
    Stage.LIVE: {Stage.ROLLED_BACK, Stage.ARCHIVED},
    Stage.ROLLED_BACK: {Stage.ARCHIVED},
    Stage.ARCHIVED: set(),
}


@dataclass(slots=True)
class ModelVersion:
    name: str
    version: int
    stage: Stage = Stage.DRAFT
    traffic: float = 0.0
    metrics: dict[str, float] = field(default_factory=dict)
    payload: Any = None
    created_at: datetime = field(default_factory=now)
    history: list[tuple[datetime, Stage, str]] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.name}:v{self.version}"


class ModelRegistry:
    def __init__(self, canary_traffic: float = 0.1) -> None:
        if not 0.0 < canary_traffic < 1.0:
            raise RegistryError("canary traffic must sit between 0 and 1")
        self._canary_traffic = canary_traffic
        self._versions: dict[str, list[ModelVersion]] = {}

    def register(
        self, name: str, payload: Any, metrics: dict[str, float] | None = None
    ) -> ModelVersion:
        versions = self._versions.setdefault(name, [])
        version = ModelVersion(
            name=name,
            version=len(versions) + 1,
            payload=payload,
            metrics=dict(metrics or {}),
        )
        version.history.append((now(), Stage.DRAFT, "registered"))
        versions.append(version)
        return version

    def get(self, name: str, version: int) -> ModelVersion:
        for candidate in self._versions.get(name, []):
            if candidate.version == version:
                return candidate
        raise RegistryError(f"no version {version} of {name!r}")

    def versions(self, name: str) -> list[ModelVersion]:
        return list(self._versions.get(name, []))

    def live(self, name: str) -> ModelVersion | None:
        for candidate in self._versions.get(name, []):
            if candidate.stage is Stage.LIVE:
                return candidate
        return None

    def shadow(self, name: str) -> ModelVersion | None:
        for candidate in self._versions.get(name, []):
            if candidate.stage is Stage.SHADOW:
                return candidate
        return None

    def canary(self, name: str) -> ModelVersion | None:
        for candidate in self._versions.get(name, []):
            if candidate.stage is Stage.CANARY:
                return candidate
        return None

    def transition(self, name: str, version: int, stage: Stage, reason: str = "") -> ModelVersion:
        target = self.get(name, version)

        if stage not in ALLOWED[target.stage]:
            raise RegistryError(
                f"{target.label} cannot move from {target.stage} to {stage}"
            )

        if stage is Stage.LIVE:
            current = self.live(name)
            if current is not None and current.version != version:
                current.stage = Stage.ARCHIVED
                current.traffic = 0.0
                current.history.append((now(), Stage.ARCHIVED, f"replaced by v{version}"))

        target.stage = stage
        target.traffic = {
            Stage.LIVE: 1.0,
            Stage.CANARY: self._canary_traffic,
            Stage.SHADOW: 0.0,
        }.get(stage, 0.0)
        target.history.append((now(), stage, reason))
        return target

    def rollback(self, name: str, reason: str) -> ModelVersion:
        current = self.canary(name) or self.live(name)
        if current is None:
            raise RegistryError(f"{name!r} has nothing serving to roll back")

        current.stage = Stage.ROLLED_BACK
        current.traffic = 0.0
        current.history.append((now(), Stage.ROLLED_BACK, reason))

        previous = [
            candidate
            for candidate in self._versions.get(name, [])
            if candidate.version < current.version and candidate.stage is Stage.ARCHIVED
        ]
        if previous:
            restored = max(previous, key=lambda candidate: candidate.version)
            restored.stage = Stage.LIVE
            restored.traffic = 1.0
            restored.history.append(
                (now(), Stage.LIVE, f"restored after rolling back v{current.version}")
            )

        return current
