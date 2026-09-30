"""Optional demo warm-up, off unless SIFA_DEMO_WARMUP is a positive number of requests.

A freshly built platform has served nothing, so the experiment screen reads "0 served", the
registry shows one version and the rollout guard has no window to judge. Warming up serves real
traffic through the real pipeline, and records a believable release history through the real
registry transitions, so a demo opens on screens that have something to say. Every history reason
is tagged "(demo warm-up)" so nothing here can be mistaken for a production record.
"""
from __future__ import annotations

import numpy as np

from sifa.registry.models import Stage
from sifa.serving.platform import Platform

TAG = "(demo warm-up)"


def seed_release_history(platform: Platform) -> None:
    """v1 is live from the build; add a promoted v2 and a canary that was rolled back."""
    registry = platform.registry
    auc = platform.training.holdout_auc

    second = registry.register("ranker", platform.ranker, {"auc": round(auc + 0.004, 4)})
    v2 = second.version
    registry.transition("ranker", v2, Stage.SHADOW, f"offline AUC beat v1 {TAG}")
    registry.transition("ranker", v2, Stage.CANARY, f"shadow disagreement under 2% {TAG}")
    registry.transition("ranker", v2, Stage.LIVE, f"canary held for 48 h inside the guard {TAG}")

    third = registry.register("ranker", platform.ranker, {"auc": round(auc - 0.011, 4)})
    v3 = third.version
    registry.transition("ranker", v3, Stage.SHADOW, f"retrained on the newest week {TAG}")
    registry.transition("ranker", v3, Stage.CANARY, f"shadow acceptable {TAG}")
    registry.rollback("ranker", f"calibration error on the canary beat the guard {TAG}")


def warm_up(platform: Platform, requests: int, seed: int = 29) -> int:
    """Serve `requests` feeds for random users, then write the release history; returns how many."""
    requests = max(0, min(requests, 20_000))
    rng = np.random.default_rng(seed)
    users = platform.world.users
    for user in rng.choice(users, size=requests, replace=True):
        platform.recommend(str(user))
    if len(platform.registry.versions("ranker")) == 1:
        seed_release_history(platform)
    return requests
