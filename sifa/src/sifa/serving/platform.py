from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from sifa.bandits.thompson import ThompsonSampler
from sifa.core.errors import SifaError
from sifa.evaluation.metrics import ndcg, recall_at_k
from sifa.experiments.assignment import Experiment, Variant
from sifa.experiments.sequential import MixtureSprt, SequentialResult
from sifa.index.hnsw import HnswConfig
from sifa.monitoring.drift import DriftReport, detect_drift
from sifa.monitoring.guard import GuardVerdict, RolloutGuard, ServingWindow
from sifa.ranking.ranker import LearningToRank, RankerConfig, TrainingReport
from sifa.registry.models import ModelRegistry, Stage
from sifa.retrieval.two_tower import Retriever, TwoTowerConfig, TwoTowerModel
from sifa.serving.pipeline import FeedPipeline, ItemCatalogue, ServingConfig
from sifa.simulation.world import World, build_world

BASELINE_SEED = 4242


@dataclass(slots=True)
class ExperimentCounters:
    control_trials: int = 0
    control_successes: int = 0
    treatment_trials: int = 0
    treatment_successes: int = 0


@dataclass(slots=True)
class Platform:
    world: World = field(default_factory=build_world)
    tower: TwoTowerModel = field(init=False)
    ranker: LearningToRank = field(init=False)
    retriever: Retriever = field(init=False)
    pipeline: FeedPipeline = field(init=False)
    registry: ModelRegistry = field(init=False)
    experiment: Experiment = field(init=False)
    sprt: MixtureSprt = field(init=False)
    counters: ExperimentCounters = field(init=False)
    guard: RolloutGuard = field(init=False)
    live_window: ServingWindow = field(init=False)
    canary_window: ServingWindow = field(init=False)
    training: TrainingReport = field(init=False)
    tower_report: dict[str, float] = field(init=False)
    reference_features: dict[str, list[float]] = field(init=False)
    built_at: datetime = field(init=False)

    def __post_init__(self) -> None:
        self.built_at = datetime.now(UTC)
        self.experiment = Experiment(
            key="diversity_v1",
            variants=(Variant("control", 1.0), Variant("treatment", 1.0)),
            holdout=0.05,
        )
        self.sprt = MixtureSprt(alpha=0.05, tau=0.01, minimum_samples=200)
        self.counters = ExperimentCounters()
        self.guard = RolloutGuard()
        self.live_window = ServingWindow()
        self.canary_window = ServingWindow()

        self.tower = TwoTowerModel(TwoTowerConfig(dimension=48, epochs=10, seed=5))
        self.tower_report = self.tower.fit(self.world.interactions)
        index = self.tower.build_index(HnswConfig(m=16, ef_construction=100, ef_search=64))
        self.retriever = Retriever(self.tower, index)

        rows, labels = self._training_rows()
        self.ranker = LearningToRank(
            RankerConfig(feature_order=tuple(rows[0].keys()), seed=13)
        )
        self.training = self.ranker.fit(rows, labels)

        self.reference_features = {
            name: [row[name] for row in rows] for name in rows[0]
        }

        catalogue = ItemCatalogue(
            vectors={item: self.tower.item_vector(item) for item in self.world.items},
            published_at=self.world.catalogue.published_at,
            author=self.world.catalogue.author,
            topic=self.world.catalogue.topic,
        )
        self.pipeline = FeedPipeline(
            self.retriever,
            self.ranker,
            self.world.item_features,
            self.world.user_features,
            catalogue,
            self.experiment,
            ServingConfig(retrieve_k=120, return_k=15),
            sampler=ThompsonSampler(seed=BASELINE_SEED),
        )

        self.registry = ModelRegistry(canary_traffic=0.1)
        self.registry.register("ranker", self.ranker, {"auc": self.training.holdout_auc})
        self.registry.transition("ranker", 1, Stage.SHADOW, "initial build")
        self.registry.transition("ranker", 1, Stage.CANARY, "passed shadow")
        self.registry.transition("ranker", 1, Stage.LIVE, "promoted")

    def _training_rows(self) -> tuple[list[dict[str, float]], list[int]]:
        as_of = datetime(2026, 4, 1, tzinfo=UTC)
        rows: list[dict[str, float]] = []
        labels: list[int] = []

        for user, item, clicked, extra in self.world.labels:
            user_row = self.world.user_features.latest(user, as_of)
            item_row = self.world.item_features.latest(item, as_of)
            score = float(
                np.dot(self.tower.user_vector(user), self.tower.item_vector(item))
            )
            rows.append({**user_row, **item_row, **extra, "retrieval_score": score})
            labels.append(clicked)

        return rows, labels

    def users(self, limit: int = 60) -> list[dict[str, Any]]:
        return [
            {
                "user_id": user,
                "topic": self.world.user_topic[user],
                "clicks": sum(1 for u, _ in self.world.interactions if u == user),
            }
            for user in self.world.users[:limit]
        ]

    def recommend(self, user_id: str) -> dict[str, Any]:
        if user_id not in set(self.world.users):
            raise SifaError(f"no user {user_id!r} in this catalogue")

        feed = self.pipeline.recommend(user_id)
        topic = self.world.user_topic[user_id]
        relevance = [
            1.0 if self.world.catalogue.topic[item.item_id] == topic else 0.0
            for item in feed.items
        ]
        relevant = {
            item for item in self.world.items if self.world.catalogue.topic[item] == topic
        }

        self._record_serving(feed.variant, relevance)

        return {
            "request_id": feed.request_id,
            "user_id": feed.user_id,
            "user_topic": topic,
            "variant": feed.variant,
            "retrieved": feed.retrieved,
            "latency_ms": round(feed.latency_ms, 2),
            "ndcg_at_10": round(ndcg(relevance, 10), 4),
            "recall_at_15": round(
                recall_at_k([item.item_id for item in feed.items], relevant, 15), 4
            ),
            "diagnostics": feed.diagnostics,
            "items": [
                {
                    "item_id": item.item_id,
                    "score": round(item.score, 4),
                    "retrieval_score": round(item.retrieval_score, 4),
                    "topic": self.world.catalogue.topic[item.item_id],
                    "author": self.world.catalogue.author[item.item_id],
                    "on_topic": self.world.catalogue.topic[item.item_id] == topic,
                    "source": item.source,
                    "reasons": list(item.reasons),
                    "published_at": self.world.catalogue.published_at[
                        item.item_id
                    ].isoformat(),
                }
                for item in feed.items
            ],
        }

    def _record_serving(self, variant: str, relevance: list[float]) -> None:
        clicked = bool(relevance and relevance[0] > 0)
        probability = float(np.mean(relevance)) if relevance else 0.0

        if variant == "treatment":
            self.counters.treatment_trials += 1
            self.counters.treatment_successes += int(clicked)
            self.canary_window.record(clicked, probability, 8.0)
        elif variant == "control":
            self.counters.control_trials += 1
            self.counters.control_successes += int(clicked)
            self.live_window.record(clicked, probability, 8.0)

    def experiment_state(self) -> SequentialResult:
        return self.sprt.evaluate(
            self.counters.control_successes,
            self.counters.control_trials,
            self.counters.treatment_successes,
            self.counters.treatment_trials,
        )

    def drift(self, live_shift: float = 0.0) -> list[DriftReport]:
        rng = np.random.default_rng(9)
        reports: list[DriftReport] = []

        for name, reference in self.reference_features.items():
            sample = np.asarray(reference, dtype=np.float64)
            live = sample + live_shift * (sample.std() or 1.0) + rng.normal(
                0, 1e-6, size=len(sample)
            )
            reports.append(detect_drift(name, sample.tolist(), live.tolist()))

        reports.sort(key=lambda report: report.psi, reverse=True)
        return reports

    def guard_verdict(self) -> GuardVerdict:
        return self.guard.assess(self.live_window, self.canary_window)

    def health(self) -> dict[str, Any]:
        return {
            "built_at": self.built_at.isoformat(),
            "users": len(self.world.users),
            "items": len(self.world.items),
            "interactions": len(self.world.interactions),
            "index_size": len(self.world.items),
            "embedding_dimension": self.tower.dimension,
            "ranker_auc": round(self.training.holdout_auc, 4),
            "ranker_calibrated": self.training.calibrated,
            "tower_final_loss": round(self.tower_report["final_loss"], 4),
        }
