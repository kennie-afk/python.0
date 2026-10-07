from __future__ import annotations

import threading
from collections.abc import Callable
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

# Edges the registry itself may take but an operator may not request. Withdrawing the live model
# puts the newest archived version back in its place; ARCHIVED stays terminal for everything else.
SYSTEM_EDGES: frozenset[tuple[Stage, Stage]] = frozenset({(Stage.ARCHIVED, Stage.LIVE)})

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
    # Who caused each history entry, parallel to `history` ("system" for automatic transitions).
    actors: list[str] = field(default_factory=list)
    # Reproducibility record: data fingerprint, config, seeds, library versions, git SHA, ...
    card: dict[str, Any] = field(default_factory=dict)

    def log(self, stage: Stage, reason: str, actor: str = "system") -> None:
        self.history.append((now(), stage, reason))
        self.actors.append(actor)

    @property
    def label(self) -> str:
        return f"{self.name}:v{self.version}"

class ModelRegistry:
    def __init__(self, canary_traffic: float = 0.1) -> None:
        if not 0.0 < canary_traffic < 1.0:
            raise RegistryError("canary traffic must sit between 0 and 1")
        self._canary_traffic = canary_traffic
        self._versions: dict[str, list[ModelVersion]] = {}
        self._lock = threading.RLock()
        # Called, under the lock, with every version a mutation touched. Persistence hangs here.
        self.on_change: Callable[[ModelVersion], None] | None = None

    def _changed(self, *versions: ModelVersion) -> None:
        if self.on_change is not None:
            for version in versions:
                self.on_change(version)

    def restore(self, version: ModelVersion) -> None:
        """Re-insert a version loaded from durable storage without logging a transition."""
        with self._lock:
            self._versions.setdefault(version.name, []).append(version)

    def register(
        self,
        name: str,
        payload: Any,
        metrics: dict[str, float] | None = None,
        card: dict[str, Any] | None = None,
        actor: str = "system",
    ) -> ModelVersion:
        with self._lock:
            versions = self._versions.setdefault(name, [])
            version = ModelVersion(
                name=name,
                version=len(versions) + 1,
                payload=payload,
                metrics=dict(metrics or {}),
                card=dict(card or {}),
            )
            version.log(Stage.DRAFT, "registered", actor)
            versions.append(version)
            self._changed(version)
            return version

    def get(self, name: str, version: int) -> ModelVersion:
        with self._lock:
            for candidate in self._versions.get(name, []):
                if candidate.version == version:
                    return candidate
            raise RegistryError(f"no version {version} of {name!r}")

    def versions(self, name: str) -> list[ModelVersion]:
        with self._lock:
            return list(self._versions.get(name, []))

    def live(self, name: str) -> ModelVersion | None:
        with self._lock:
            for candidate in self._versions.get(name, []):
                if candidate.stage is Stage.LIVE:
                    return candidate
            return None

    def shadow(self, name: str) -> ModelVersion | None:
        with self._lock:
            for candidate in self._versions.get(name, []):
                if candidate.stage is Stage.SHADOW:
                    return candidate
            return None

    def canary(self, name: str) -> ModelVersion | None:
        with self._lock:
            for candidate in self._versions.get(name, []):
                if candidate.stage is Stage.CANARY:
                    return candidate
            return None

    def transition(
        self, name: str, version: int, stage: Stage, reason: str = "", actor: str = "system"
    ) -> ModelVersion:
        with self._lock:
            target = self.get(name, version)

            if stage not in ALLOWED[target.stage]:
                raise RegistryError(
                    f"{target.label} cannot move from {target.stage} to {stage}"
                )

            if stage in (Stage.SHADOW, Stage.CANARY):
                holder = next(
                    (
                        other
                        for other in self._versions.get(name, [])
                        if other.stage is stage and other.version != version
                    ),
                    None,
                )
                if holder is not None:
                    raise RegistryError(
                        f"{holder.label} is already in {stage.value}; advance or roll it back first"
                    )

            replaced: ModelVersion | None = None
            if stage is Stage.LIVE:
                current = self.live(name)
                if current is not None and current.version != version:
                    current.stage = Stage.ARCHIVED
                    current.traffic = 0.0
                    current.log(Stage.ARCHIVED, f"replaced by v{version}", actor)
                    replaced = current

            target.stage = stage
            target.traffic = {
                Stage.LIVE: 1.0,
                Stage.CANARY: self._canary_traffic,
                Stage.SHADOW: 0.0,
            }.get(stage, 0.0)
            target.log(stage, reason, actor)
            self._changed(*([replaced] if replaced else []), target)
            return target

    def rollback(self, name: str, reason: str, actor: str = "system") -> ModelVersion:
        with self._lock:
            current = self.canary(name) or self.live(name)
            if current is None:
                raise RegistryError(f"{name!r} has nothing serving to roll back")

            # Withdrawing a canary leaves the live model exactly where it was. Only withdrawing the
            # live model itself restores an older one; doing it for a canary would put two models
            # live at once whenever an older version was archived.
            withdrew_live = current.stage is Stage.LIVE

            current.stage = Stage.ROLLED_BACK
            current.traffic = 0.0
            current.log(Stage.ROLLED_BACK, reason, actor)

            if not withdrew_live:
                self._changed(current)
                return current

            previous = [
                candidate
                for candidate in self._versions.get(name, [])
                if candidate.version < current.version and candidate.stage is Stage.ARCHIVED
            ]
            if previous:
                restored = max(previous, key=lambda candidate: candidate.version)
                if (restored.stage, Stage.LIVE) not in SYSTEM_EDGES:
                    raise RegistryError(
                        f"{restored.label} cannot be restored from {restored.stage}"
                    )
                restored.stage = Stage.LIVE
                restored.traffic = 1.0
                restored.log(
                    Stage.LIVE, f"restored after rolling back v{current.version}", actor
                )
                self._changed(current, restored)
                return current

            self._changed(current)
            return current
