from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from sifa.core.clock import now
from sifa.features.schema import FeatureKind, FeatureSpec, FeatureView
from sifa.features.store import FeatureStore
from sifa.serving.pipeline import ItemCatalogue

TOPICS = ("politics", "sport", "tech", "music", "business")

USER_VIEW = FeatureView(
    name="user_activity",
    entity="user",
    features=(
        FeatureSpec("user_ctr", FeatureKind.NUMERIC),
        FeatureSpec("user_sessions", FeatureKind.NUMERIC),
    ),
    ttl=timedelta(days=90),
)

ITEM_VIEW = FeatureView(
    name="item_quality",
    entity="item",
    features=(
        FeatureSpec("item_ctr", FeatureKind.NUMERIC),
        FeatureSpec("item_age_hours", FeatureKind.NUMERIC),
        FeatureSpec("item_length", FeatureKind.NUMERIC),
    ),
    ttl=timedelta(days=30),
)


@dataclass(frozen=True, slots=True)
class World:
    users: list[str]
    items: list[str]
    interactions: list[tuple[str, str]]
    user_topic: dict[str, str]
    catalogue: ItemCatalogue
    user_features: FeatureStore
    item_features: FeatureStore
    labels: list[tuple[str, str, int, dict[str, float]]]


def build_world(
    n_users: int = 240,
    n_items: int = 600,
    seed: int = 101,
    start: datetime | None = None,
) -> World:
    rng = np.random.default_rng(seed)
    origin = start or now()

    users = [f"u{i}" for i in range(n_users)]
    items = [f"i{i}" for i in range(n_items)]

    user_topic = {user: TOPICS[i % len(TOPICS)] for i, user in enumerate(users)}
    item_topic = {item: TOPICS[i % len(TOPICS)] for i, item in enumerate(items)}
    item_author = {item: f"author{i % 40}" for i, item in enumerate(items)}

    item_quality = {item: float(rng.beta(2, 5)) for item in items}
    published = {
        item: origin - timedelta(hours=float(rng.integers(0, 240))) for item in items
    }

    user_features = FeatureStore(USER_VIEW)
    item_features = FeatureStore(ITEM_VIEW)

    for user in users:
        user_features.write(
            user,
            origin - timedelta(days=1),
            {
                "user_ctr": float(rng.beta(3, 20)),
                "user_sessions": float(rng.integers(1, 60)),
            },
        )

    for item in items:
        age = (origin - published[item]).total_seconds() / 3600.0
        item_features.write(
            item,
            origin - timedelta(days=1),
            {
                "item_ctr": item_quality[item],
                "item_age_hours": age,
                "item_length": float(rng.integers(80, 2000)),
            },
        )

    interactions: list[tuple[str, str]] = []
    labels: list[tuple[str, str, int, dict[str, float]]] = []

    for user in users:
        topic = user_topic[user]
        aligned = [item for item in items if item_topic[item] == topic]
        other = [item for item in items if item_topic[item] != topic]

        for _ in range(30):
            if rng.random() < 0.85:
                item = aligned[int(rng.integers(0, len(aligned)))]
            else:
                item = other[int(rng.integers(0, len(other)))]

            match = 1.0 if item_topic[item] == topic else 0.0
            probability = 0.08 + 0.55 * match * item_quality[item] * 2.2
            clicked = int(rng.random() < min(probability, 0.95))

            if clicked:
                interactions.append((user, item))

            labels.append(
                (
                    user,
                    item,
                    clicked,
                    {
                        "topic_match": match,
                        "item_quality": item_quality[item],
                    },
                )
            )

    catalogue = ItemCatalogue(
        vectors={},
        published_at=published,
        author=item_author,
        topic=item_topic,
    )

    return World(
        users=users,
        items=items,
        interactions=interactions,
        user_topic=user_topic,
        catalogue=catalogue,
        user_features=user_features,
        item_features=item_features,
        labels=labels,
    )
