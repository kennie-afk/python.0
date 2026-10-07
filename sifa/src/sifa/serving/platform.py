from __future__ import annotations

import hashlib
import json
import platform as host
import subprocess
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import scipy
import sklearn

from sifa.bandits.thompson import ThompsonSampler
from sifa.core.errors import NotFoundError, RegistryError, SifaError
from sifa.evaluation.metrics import ndcg, recall_at_k
from sifa.experiments.assignment import Experiment, Variant
from sifa.experiments.sequential import MixtureSprt, SequentialResult
from sifa.index.hnsw import HnswConfig
from sifa.monitoring.drift import LOW_CARDINALITY, DriftReport, detect_drift
from sifa.monitoring.guard import GuardVerdict, RolloutGuard, ServingWindow
from sifa.persistence.store import Store
from sifa.ranking.ranker import LearningToRank, RankerConfig, TrainingReport
from sifa.registry.models import ModelRegistry, ModelVersion, Stage
from sifa.retrieval.two_tower import Retriever, TwoTowerConfig, TwoTowerModel
from sifa.serving.pipeline import FeedPipeline, ItemCatalogue, ServingConfig
from sifa.simulation.world import World, build_world

BASELINE_SEED = 4242
# The guard looks at the canary after this many of its impressions, not on every request.
GUARD_EVERY = 25

OUTCOME_MODES = ("simulated", "feedback")
DRIFT_MINIMUM_ROWS = 100
IMPRESSION_RETENTION_DAYS = 30
TOWER_ARTIFACT = "tower.npz"
MODEL_NAME = "ranker"


@lru_cache(maxsize=1)
def git_sha() -> str | None:
    """The commit the code was running from, if it can be told; never required."""
    import os

    configured = os.environ.get("SIFA_GIT_SHA")
    if configured:
        return configured
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2,
            cwd=Path(__file__).resolve().parent, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and len(sha) == 40 else None


@dataclass(slots=True)
class ExperimentCounters:
    control_trials: int = 0
    control_successes: int = 0
    treatment_trials: int = 0
    treatment_successes: int = 0

@dataclass(slots=True)
class Platform:
    world: World = field(default_factory=build_world)
    # Where durable state lives. None keeps everything in memory (tests, throwaway runs).
    state_dir: Path | None = None
    # "simulated": the outcome of each feed is computed from the simulated world's topics.
    # "feedback": the outcome is whatever POST /v1/feedback reports.
    outcome_mode: str = "simulated"
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
    store: Store = field(init=False, repr=False)
    restored: bool = field(init=False, default=False)
    fingerprint: str = field(init=False, default="")
    _served_rows: deque[dict[str, float]] = field(init=False, repr=False)
    _rows: list[dict[str, float]] = field(init=False, repr=False)
    _labels: list[int] = field(init=False, repr=False)
    _lock: threading.RLock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.outcome_mode not in OUTCOME_MODES:
            raise SifaError(f"outcome_mode must be one of {OUTCOME_MODES}")
        self.built_at = datetime.now(UTC)
        self._lock = threading.RLock()
        self._served_rows = deque(maxlen=5000)
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

        self.fingerprint = self._data_fingerprint()
        self.store = Store(self.state_dir)
        if self.store.get_meta("fingerprint") not in (None, self.fingerprint):
            # The data or the model definition changed: old artifacts describe another model.
            self.store.archive()
            self.store = Store(self.state_dir)
        has_state = (
            self.store.get_meta("fingerprint") == self.fingerprint
            and self.store.get_meta("tower_sha256") is not None
            and bool(self.store.load_versions(MODEL_NAME))
        )
        if has_state:
            self._restore()
        else:
            self._cold_build()
        self.registry.on_change = self._persist_version
        self.store.prune_impressions(
            (datetime.now(UTC) - timedelta(days=IMPRESSION_RETENTION_DAYS)).isoformat()
        )
        self.store.trim_windows(5000)

    # -- build, persist, restore -------------------------------------------------------------
    def _data_fingerprint(self) -> str:
        """Everything training depends on except the clock: data, features, model definitions."""
        digest = hashlib.sha256()
        digest.update(b"sifa-fingerprint-1")
        digest.update(json.dumps(
            [self.world.users, self.world.items, sorted(self.world.interactions)]
        ).encode())
        as_of = self.built_at
        for user, item, clicked, _extra in self.world.labels:
            row = {
                **self.world.user_features.latest(user, as_of),
                **self.world.item_features.latest(item, as_of),
            }
            digest.update(json.dumps([user, item, clicked, sorted(row.items())]).encode())
        digest.update(repr(self._tower_config()).encode())
        digest.update(repr(self._ranker_config(13)).encode())
        return digest.hexdigest()

    @staticmethod
    def _tower_config() -> TwoTowerConfig:
        return TwoTowerConfig(dimension=48, epochs=10, seed=5)

    def _ranker_config(self, seed: int) -> RankerConfig:
        feature_order = self._feature_order()
        return RankerConfig(feature_order=feature_order, seed=seed)

    def _feature_order(self) -> tuple[str, ...]:
        # Only what serving can also supply. The simulator's labels carry two more columns,
        # topic_match and item_quality, which are the very quantities its click model is built
        # from. Training on them gave the ranker an oracle it never has at serving time (it read
        # them as 0 live), so they are left out.
        user, item, _, _extra = self.world.labels[0]
        return (
            *self.world.user_features.latest(user, self.built_at),
            *self.world.item_features.latest(item, self.built_at),
            "retrieval_score",
        )

    def _build_serving_stack(self) -> None:
        index = self.tower.build_index(HnswConfig(m=16, ef_construction=100, ef_search=64))
        self.retriever = Retriever(self.tower, index)
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

    def _cold_build(self) -> None:
        self.restored = False
        started = time.perf_counter()
        self.tower = TwoTowerModel(self._tower_config())
        self.tower_report = self.tower.fit(self.world.interactions)
        saved = self.store.save_artifact(TOWER_ARTIFACT, self.tower.to_arrays())
        if saved:
            self.store.set_meta("tower_sha256", saved[1])
        else:
            self.store.set_meta("tower_sha256", "memory")
        self.store.put("tower_report", self.tower_report)

        rows, labels = self._training_rows()
        self._rows, self._labels = rows, labels
        self.ranker = LearningToRank(self._ranker_config(13))
        self.training = self.ranker.fit(rows, labels)
        self.reference_features = {name: [row[name] for row in rows] for name in rows[0]}
        self._build_serving_stack()

        self.registry = ModelRegistry(canary_traffic=0.1)
        self.registry.on_change = self._persist_version
        card = self.model_card(self.ranker, 13, time.perf_counter() - started)
        self.registry.register(
            MODEL_NAME, self.ranker, {"auc": self.training.holdout_auc, "seed": 13.0}, card=card
        )
        self.registry.transition(MODEL_NAME, 1, Stage.SHADOW, "initial build")
        self.registry.transition(MODEL_NAME, 1, Stage.CANARY, "passed shadow")
        self.registry.transition(MODEL_NAME, 1, Stage.LIVE, "promoted")
        self.store.set_meta("fingerprint", self.fingerprint)
        self.store.set_meta("outcome_mode", self.outcome_mode)

    def _restore(self) -> None:
        """Reload everything from durable state; nothing is retrained."""
        self.restored = True
        sha = self.store.get_meta("tower_sha256") or ""
        self.tower = TwoTowerModel.from_arrays(self.store.load_artifact(TOWER_ARTIFACT, sha))
        self.tower_report = self.store.get("tower_report", {})

        rows, labels = self._training_rows()
        self._rows, self._labels = rows, labels
        self.reference_features = {name: [row[name] for row in rows] for name in rows[0]}

        self.registry = ModelRegistry(canary_traffic=0.1)
        for record in self.store.load_versions(MODEL_NAME):
            card = json.loads(record["card"])
            payload = None
            if record["artifact"]:
                payload = LearningToRank.from_arrays(
                    self.store.load_artifact(record["artifact"], record["sha256"])
                )
            version = ModelVersion(
                name=record["name"],
                version=record["version"],
                stage=Stage(record["stage"]),
                traffic=record["traffic"],
                metrics=json.loads(record["metrics"]),
                payload=payload,
                created_at=datetime.fromisoformat(record["created_at"]),
                card=card,
            )
            for entry in record["history"]:
                version.history.append(
                    (datetime.fromisoformat(entry["at"]), Stage(entry["stage"]), entry["reason"])
                )
                version.actors.append(entry["actor"])
            self.registry.restore(version)

        live = self.registry.live(MODEL_NAME)
        first = self.registry.versions(MODEL_NAME)[0]
        self.ranker = (live or first).payload
        self.training = self.ranker.report
        self._build_serving_stack()
        self.pipeline.sampler.load(self.store.get("bandit", {}))

        self.outcome_mode = self.store.get_meta("outcome_mode") or self.outcome_mode
        saved = self.store.get("counters")
        if saved:
            self.counters = ExperimentCounters(**saved)
        for name, window in (("live", self.live_window), ("canary", self.canary_window)):
            for event in self.store.load_window(name, 5000):
                window.record(
                    bool(event["clicked"]), event["probability"], event["latency_ms"],
                    event["request_id"],
                )

    def _persist_version(self, version: ModelVersion) -> None:
        artifact = None
        payload = version.payload
        if isinstance(payload, LearningToRank) and not self.store.has_artifact(
            version.name, version.version
        ):
            artifact = self.store.save_artifact(
                f"{version.name}-v{version.version}.npz", payload.to_arrays()
            )
            if artifact:
                version.card["artifact"] = {"file": artifact[0], "sha256": artifact[1]}
        self.store.save_version(version, artifact)

    def model_card(
        self, ranker: LearningToRank, seed: int, training_seconds: float, note: str = ""
    ) -> dict[str, Any]:
        """What is needed to say how a version was made and to make it again."""
        report = ranker.report
        config = asdict(ranker.config)
        config["feature_order"] = list(config["feature_order"])
        schema = hashlib.sha256(
            json.dumps([list(ranker.feature_order), self.world.user_features.view.name,
                        self.world.item_features.view.name]).encode()
        ).hexdigest()
        return {
            "data_fingerprint": self.fingerprint,
            "feature_schema_hash": schema,
            "config": config,
            "seeds": {"ranker": seed, "tower": self._tower_config().seed, "bandit": BASELINE_SEED},
            "libraries": {
                "python": host.python_version(), "numpy": np.__version__,
                "scipy": scipy.__version__, "scikit-learn": sklearn.__version__,
            },
            "git_sha": git_sha(),
            "metrics": {"holdout_auc": report.holdout_auc, "calibrated": report.calibrated},
            "training_rows": report.rows,
            "positives": report.positives,
            "trained_at": datetime.now(UTC).isoformat(),
            "training_seconds": round(training_seconds, 3),
            "trained_on": "the simulated world (see README: nothing here is production data)",
            "note": note,
        }

    def _bucket(self, user_id: str) -> float:
        digest = hashlib.sha256(f"ranker-canary:{user_id}".encode()).digest()
        return int.from_bytes(digest[:8], "big") / 2**64

    def _serving_model(self, user_id: str) -> tuple[LearningToRank, int, Stage]:
        """The ranker that answers this user, chosen by the registry and not by the platform.

        A canary takes its traffic share by hashing the user, so one person always sees the same
        model while it is a canary. With no live version the build-time ranker answers.
        """
        canary = self.registry.canary("ranker")
        if canary is not None and self._bucket(user_id) < canary.traffic:
            return canary.payload, canary.version, Stage.CANARY
        live = self.registry.live("ranker")
        if live is not None:
            return live.payload, live.version, Stage.LIVE
        return self.ranker, 0, Stage.LIVE

    def promote_candidate(self, actor: str = "system") -> dict[str, Any]:
        """Train a new ranker and start its canary. It is not a copy of the live one."""
        with self._lock:
            next_version = len(self.registry.versions(MODEL_NAME)) + 1
            seed = 13 + next_version
            started = time.perf_counter()
            candidate = LearningToRank(self._ranker_config(seed))
            report = candidate.fit(self._rows, self._labels)
            card = self.model_card(candidate, seed, time.perf_counter() - started)
            version = self.registry.register(
                MODEL_NAME,
                candidate,
                {"auc": report.holdout_auc, "seed": float(seed)},
                card=card,
                actor=actor,
            )
            self.registry.transition(
                MODEL_NAME, version.version, Stage.SHADOW,
                "retrained, waiting behind live traffic", actor,
            )
            self.registry.transition(
                MODEL_NAME,
                version.version,
                Stage.CANARY,
                f"holdout AUC {report.holdout_auc:.4f}, entering canary",
                actor,
            )
            self._reset_canary_window()
            return {"version": version, "auc": report.holdout_auc}

    def _reset_canary_window(self) -> None:
        self.canary_window = ServingWindow()
        self.store.clear_window("canary")

    def rollback(self, reason: str, actor: str = "system") -> tuple[Any, Any]:
        with self._lock:
            rolled = self.registry.rollback(MODEL_NAME, reason, actor)
            self._reset_canary_window()
            return rolled, self.registry.live(MODEL_NAME)

    def advance_canary(self, actor: str = "system") -> Any:
        """Canary to live, only when the guard has seen enough traffic and finds it healthy."""
        with self._lock:
            canary = self.registry.canary(MODEL_NAME)
            if canary is None:
                raise RegistryError("there is no canary to advance")
            minimum = self.guard.thresholds.minimum_samples
            seen = self.canary_window.impressions
            if seen < minimum:
                raise RegistryError(
                    f"{canary.label} has served {seen} requests and the guard needs {minimum}"
                )
            verdict = self.guard_verdict()
            if not verdict.healthy:
                raise RegistryError(f"the guard objects: {'; '.join(verdict.reasons)}")
            promoted = self.registry.transition(
                MODEL_NAME, canary.version, Stage.LIVE,
                "guard healthy, advanced from canary", actor,
            )
            self.live_window = self.canary_window
            self.canary_window = ServingWindow()
            self.store.promote_window()
            return promoted

    def _enforce_guard(self) -> None:
        canary = self.registry.canary(MODEL_NAME)
        if canary is None:
            return
        verdict = self.guard.enforce(
            self.registry, MODEL_NAME, self.live_window, self.canary_window
        )
        if not verdict.healthy:
            self._reset_canary_window()
            self.enqueue_alert(
                "guard_rollback",
                f"{canary.label} was rolled back by the guard: {'; '.join(verdict.reasons)}",
                {"version": canary.version, "reasons": list(verdict.reasons)},
            )

    def enqueue_alert(self, kind: str, message: str, payload: dict[str, Any]) -> None:
        """Alerts go to an outbox; the scheduler delivers them, so a dead webhook loses nothing."""
        self.store.add_alert(datetime.now(UTC).isoformat(), kind, message, payload)

    def _training_rows(self) -> tuple[list[dict[str, float]], list[int]]:
        as_of = self.built_at
        rows: list[dict[str, float]] = []
        labels: list[int] = []

        for user, item, clicked, _extra in self.world.labels:
            user_row = self.world.user_features.latest(user, as_of)
            item_row = self.world.item_features.latest(item, as_of)
            score = float(
                np.dot(self.tower.user_vector(user), self.tower.item_vector(item))
            )
            rows.append({**user_row, **item_row, "retrieval_score": score})
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

        ranker, model_version, served_stage = self._serving_model(user_id)
        feed = self.pipeline.recommend(user_id, ranker=ranker)
        topic = self.world.user_topic[user_id]
        relevance = [
            1.0 if self.world.catalogue.topic[item.item_id] == topic else 0.0
            for item in feed.items
        ]
        relevant = {
            item for item in self.world.items if self.world.catalogue.topic[item] == topic
        }

        self._record_serving(
            feed.request_id, feed.variant, relevance, served_stage, feed.latency_ms,
            [item.score for item in feed.items],
        )
        self.store.add_impression(
            feed.request_id, user_id, datetime.now(UTC).isoformat(), model_version,
            served_stage.value, feed.variant, [item.item_id for item in feed.items],
            self.outcome_mode,
        )
        with self._lock:
            # What the ranker actually saw, for comparison with the training reference.
            self._served_rows.extend(dict(item.features) for item in feed.items)

        return {
            "request_id": feed.request_id,
            "user_id": feed.user_id,
            "user_topic": topic,
            "variant": feed.variant,
            "model_version": model_version,
            "model_stage": served_stage.value,
            "outcome_source": self.outcome_mode,
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

    def _record_serving(
        self,
        request_id: str,
        variant: str,
        relevance: list[float],
        stage: Stage,
        latency_ms: float,
        scores: list[float],
    ) -> None:
        if self.outcome_mode == "simulated":
            # SIMULATED outcome: is the top item on the user's topic. Not a recorded click.
            clicked = bool(relevance and relevance[0] > 0)
            probability = float(np.mean(relevance)) if relevance else 0.0
        else:
            # Real feedback: a feed is a trial; a click, if one ever arrives, credits it later.
            # The probability is the model's own chance that at least one served item is clicked.
            clicked = False
            probability = float(1.0 - np.prod(1.0 - np.clip(scores, 0.0, 1.0))) if scores else 0.0

        with self._lock:
            # Experiment arms and model stages are two separate comparisons: the experiment asks
            # whether diversification helps, the windows ask whether a canary model is safe.
            if variant == "treatment":
                self.counters.treatment_trials += 1
                self.counters.treatment_successes += int(clicked)
            elif variant == "control":
                self.counters.control_trials += 1
                self.counters.control_successes += int(clicked)

            window = self.canary_window if stage is Stage.CANARY else self.live_window
            window.record(clicked, probability, latency_ms, request_id)
            self.store.add_window_event(
                "canary" if stage is Stage.CANARY else "live",
                request_id, clicked, probability, latency_ms,
            )
            self.store.put("counters", asdict(self.counters))

            if stage is Stage.CANARY and self.canary_window.impressions % GUARD_EVERY == 0:
                self._enforce_guard()

    def record_feedback(
        self, request_id: str, item_id: str, clicked: bool, actor: str = "system"
    ) -> dict[str, Any]:
        """A real signal from a user: feeds the bandit always, and the experiment counters and
        guard windows when the platform is in feedback mode. Repeats of a pair change nothing."""
        with self._lock:
            impression = self.store.get_impression(request_id)
            if impression is None:
                raise NotFoundError(f"no impression {request_id!r}; never served, or aged out")
            if item_id not in impression.items:
                raise SifaError(f"item {item_id!r} was not part of request {request_id!r}")

            inserted = self.store.add_feedback(
                request_id, item_id, clicked, datetime.now(UTC).isoformat(), actor
            )
            if not inserted:
                return {"recorded": False, "duplicate": True, "credited": False}

            self.pipeline.reward(self.world.catalogue.topic[item_id], clicked)
            self.store.put("bandit", self.pipeline.sampler.export())

            credited = False
            if (
                clicked
                and impression.outcome_source == "feedback"
                and self.outcome_mode == "feedback"
                and not impression.credited
            ):
                credited = True
                self.store.mark_credited(request_id)
                if impression.variant == "treatment":
                    self.counters.treatment_successes += 1
                elif impression.variant == "control":
                    self.counters.control_successes += 1
                self.store.put("counters", asdict(self.counters))
                for window in (self.canary_window, self.live_window):
                    window.credit_click(request_id)
                self.store.credit_window_click(request_id)
            return {"recorded": True, "duplicate": False, "credited": credited}

    def set_outcome_mode(self, mode: str) -> None:
        """Switch between simulated and real outcomes. The two are not comparable, so the
        experiment counters and both guard windows start again."""
        if mode not in OUTCOME_MODES:
            raise SifaError(f"outcome mode must be one of {OUTCOME_MODES}")
        with self._lock:
            if mode == self.outcome_mode:
                return
            self.outcome_mode = mode
            self.counters = ExperimentCounters()
            self.live_window = ServingWindow()
            self.canary_window = ServingWindow()
            self.store.clear_window("live")
            self.store.clear_window("canary")
            self.store.put("counters", asdict(self.counters))
            self.store.set_meta("outcome_mode", mode)

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
            live = sample + live_shift * (sample.std() or 1.0)
            # Jitter breaks ties in continuous features. On a discrete feature (a 0/1 flag) it
            # splits every tie the wrong way and makes the KS test report a large, meaningless
            # difference between two identical samples.
            if len(np.unique(np.round(sample, 9))) > LOW_CARDINALITY:
                live = live + rng.normal(0, 1e-6, size=len(sample))
            reports.append(detect_drift(name, sample.tolist(), live.tolist()))

        reports.sort(key=lambda report: report.psi, reverse=True)
        return reports

    def live_drift(self) -> dict[str, Any]:
        """Drift of the feature rows the ranker really saw while serving against the training
        reference. Empty until enough traffic has been served; never invented."""
        with self._lock:
            rows = list(self._served_rows)
        served = set(rows[0]) if rows else set()
        not_served = sorted(name for name in self.reference_features if rows and name not in served)
        reports: list[DriftReport] = []
        if len(rows) >= DRIFT_MINIMUM_ROWS:
            for name, reference in self.reference_features.items():
                if name in served:
                    live = [row[name] for row in rows if name in row]
                    reports.append(detect_drift(name, reference, live))
            reports.sort(key=lambda report: report.psi, reverse=True)
        return {
            "source": "live serving traffic",
            "rows": len(rows),
            "minimum_rows": DRIFT_MINIMUM_ROWS,
            "ready": len(rows) >= DRIFT_MINIMUM_ROWS,
            "reports": reports,
            "trained_but_not_served": not_served,
        }

    def guard_verdict(self) -> GuardVerdict:
        return self.guard.assess(self.live_window, self.canary_window)

    def health(self) -> dict[str, Any]:
        return {
            "built_at": self.built_at.isoformat(),
            "persisted": self.state_dir is not None,
            "restored_from_state": self.restored,
            "outcome_mode": self.outcome_mode,
            "users": len(self.world.users),
            "items": len(self.world.items),
            "interactions": len(self.world.interactions),
            "index_size": len(self.retriever.index),
            "embedding_dimension": self.tower.dimension,
            "ranker_auc": round(self.training.holdout_auc, 4),
            "ranker_calibrated": self.training.calibrated,
            "tower_final_loss": round(self.tower_report["final_loss"], 4),
        }
