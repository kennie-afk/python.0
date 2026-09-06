from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from sifa.bandits.thompson import ThompsonSampler
from sifa.core.clock import now
from sifa.core.types import Candidate, RankedFeed, ScoredItem
from sifa.experiments.assignment import Experiment, assign
from sifa.features.store import FeatureStore
from sifa.policy.rules import (
    PolicyConfig,
    apply_freshness,
    cap_per_attribute,
    maximal_marginal_relevance,
    similarity_matrix,
)
from sifa.ranking.ranker import LearningToRank
from sifa.retrieval.two_tower import Retriever


@dataclass(frozen=True, slots=True)
class ServingConfig:
    retrieve_k: int = 200
    return_k: int = 20
    explore_fraction: float = 0.1
    latency_budget_ms: float = 250.0

    def __post_init__(self) -> None:
        if self.return_k > self.retrieve_k:
            raise ValueError("cannot return more items than are retrieved")
        if not 0.0 <= self.explore_fraction < 0.5:
            raise ValueError("explore_fraction must sit between 0 and 0.5")


@dataclass(frozen=True, slots=True)
class ItemCatalogue:
    vectors: dict[str, np.ndarray]
    published_at: dict[str, datetime]
    author: dict[str, str]
    topic: dict[str, str]


class FeedPipeline:
    def __init__(
        self,
        retriever: Retriever,
        ranker: LearningToRank,
        item_features: FeatureStore,
        user_features: FeatureStore,
        catalogue: ItemCatalogue,
        experiment: Experiment,
        config: ServingConfig | None = None,
        policy: PolicyConfig | None = None,
        sampler: ThompsonSampler | None = None,
    ) -> None:
        self._retriever = retriever
        self._ranker = ranker
        self._item_features = item_features
        self._user_features = user_features
        self._catalogue = catalogue
        self._experiment = experiment
        self._config = config or ServingConfig()
        self._policy = policy or PolicyConfig()
        self._sampler = sampler or ThompsonSampler()

    def _assemble_features(
        self, user_id: str, candidates: list[Candidate], as_of: datetime
    ) -> dict[str, dict[str, float]]:
        user_row = self._user_features.latest(user_id, as_of)
        assembled: dict[str, dict[str, float]] = {}

        for candidate in candidates:
            item_row = self._item_features.latest(candidate.item_id, as_of)
            assembled[candidate.item_id] = {
                **user_row,
                **item_row,
                "retrieval_score": candidate.retrieval_score,
            }

        return assembled

    def recommend(
        self,
        user_id: str,
        seen: set[str] | None = None,
        as_of: datetime | None = None,
    ) -> RankedFeed:
        started = time.perf_counter()
        moment = as_of or now()
        request_id = str(uuid.uuid4())
        variant = assign(self._experiment, user_id)

        candidates = self._retriever.retrieve(
            user_id, self._config.retrieve_k, exclude=seen or set()
        )

        if not candidates:
            return RankedFeed(
                request_id=request_id,
                user_id=user_id,
                variant=variant,
                items=(),
                retrieved=0,
                latency_ms=(time.perf_counter() - started) * 1000,
                diagnostics={"reason": "no candidates for this user"},
            )

        features = self._assemble_features(user_id, candidates, moment)
        ranked = self._ranker.rank(candidates, features)

        ranked = apply_freshness(ranked, self._catalogue.published_at, moment, self._policy)

        if self._policy.max_per_author is not None:
            ranked = cap_per_attribute(
                ranked, self._catalogue.author, self._policy.max_per_author
            )

        lambda_ = 1.0 if variant == "control" else 1.0 - self._policy.diversity_lambda
        pool = ranked[: max(self._config.return_k * 4, self._config.return_k)]
        diversified = maximal_marginal_relevance(
            pool,
            similarity_matrix(pool, self._catalogue.vectors),
            self._config.return_k,
            lambda_,
        )

        final = self._explore(diversified, ranked)

        elapsed = (time.perf_counter() - started) * 1000

        return RankedFeed(
            request_id=request_id,
            user_id=user_id,
            variant=variant,
            items=tuple(final),
            retrieved=len(candidates),
            latency_ms=elapsed,
            diagnostics={
                "diversity_lambda": 1.0 - lambda_,
                "over_budget": elapsed > self._config.latency_budget_ms,
                "distinct_topics": len(
                    {self._catalogue.topic.get(item.item_id, "") for item in final}
                ),
            },
        )

    def _explore(
        self, selected: list[ScoredItem], pool: list[ScoredItem]
    ) -> list[ScoredItem]:
        if self._config.explore_fraction <= 0.0 or len(selected) < 4:
            return selected

        slots = max(1, int(len(selected) * self._config.explore_fraction))
        chosen = {item.item_id for item in selected}
        reserve = [item for item in pool if item.item_id not in chosen]
        if not reserve:
            return selected

        topics = [self._catalogue.topic.get(item.item_id, "unknown") for item in reserve]
        arms = sorted(set(topics))
        if not arms:
            return selected

        result = list(selected)
        for _ in range(min(slots, len(reserve))):
            arm = self._sampler.select(arms)
            pick = next(
                (
                    item
                    for item in reserve
                    if self._catalogue.topic.get(item.item_id, "unknown") == arm
                    and item.item_id not in chosen
                ),
                None,
            )
            if pick is None:
                continue
            chosen.add(pick.item_id)
            result[-1] = ScoredItem(
                item_id=pick.item_id,
                score=pick.score,
                retrieval_score=pick.retrieval_score,
                source="exploration",
                features=pick.features,
                reasons=(*pick.reasons, f"explored topic {arm}"),
            )

        return result

    def reward(self, topic: str, clicked: bool) -> None:
        self._sampler.update(topic, 1.0 if clicked else 0.0)
