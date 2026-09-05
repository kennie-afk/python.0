from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

Vector = np.ndarray


@dataclass(frozen=True, slots=True)
class Candidate:
    item_id: str
    retrieval_score: float
    source: str

    def __post_init__(self) -> None:
        if not self.item_id:
            raise ValueError("a candidate must have an item id")


@dataclass(frozen=True, slots=True)
class ScoredItem:
    item_id: str
    score: float
    retrieval_score: float
    source: str
    features: dict[str, float] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Impression:
    request_id: str
    user_id: str
    item_id: str
    position: int
    score: float
    variant: str
    served_at: datetime


@dataclass(frozen=True, slots=True)
class Interaction:
    user_id: str
    item_id: str
    kind: str
    occurred_at: datetime
    dwell_seconds: float = 0.0
    value: float = 1.0


@dataclass(frozen=True, slots=True)
class RankedFeed:
    request_id: str
    user_id: str
    variant: str
    items: tuple[ScoredItem, ...]
    retrieved: int
    latency_ms: float
    diagnostics: dict[str, Any] = field(default_factory=dict)
