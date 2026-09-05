from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from sifa.core.errors import SifaError
from sifa.registry.models import Stage
from sifa.serving.platform import Platform

_platform: Platform | None = None


def get_platform() -> Iterator[Platform]:
    global _platform
    if _platform is None:
        _platform = Platform()
    yield _platform


PlatformDep = Annotated[Platform, Depends(get_platform)]


@asynccontextmanager
async def lifespan(_: FastAPI) -> Any:
    get_platform().__next__()
    yield


app = FastAPI(
    title="Sifa",
    version="0.1.0",
    description="Retrieval, ranking and experimentation for personalised feeds",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(SifaError)
async def sifa_error_handler(_: object, error: SifaError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"code": "sifa-error", "detail": str(error)},
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/overview")
def overview(platform: PlatformDep) -> dict[str, Any]:
    verdict = platform.guard_verdict()
    sequential = platform.experiment_state()
    live = platform.registry.live("ranker")

    return {
        **platform.health(),
        "live_model": live.label if live else None,
        "live_stage": live.stage.value if live else None,
        "served": platform.live_window.impressions + platform.canary_window.impressions,
        "guard_healthy": verdict.healthy,
        "guard_reasons": list(verdict.reasons),
        "experiment": {
            "key": platform.experiment.key,
            "decision": sequential.decision.value,
            "likelihood_ratio": round(sequential.likelihood_ratio, 4),
            "threshold": round(sequential.threshold, 2),
            "control_rate": round(sequential.control_rate, 4),
            "treatment_rate": round(sequential.treatment_rate, 4),
            "lift": round(sequential.lift, 4),
            "samples": sequential.samples,
        },
    }


@app.get("/v1/users")
def users(platform: PlatformDep, limit: int = Query(60, ge=1, le=240)) -> list[dict[str, Any]]:
    return platform.users(limit)


@app.get("/v1/feed/{user_id}")
def feed(user_id: str, platform: PlatformDep) -> dict[str, Any]:
    return platform.recommend(user_id)


@app.get("/v1/retrieval/{user_id}")
def retrieval(
    user_id: str, platform: PlatformDep, k: int = Query(20, ge=1, le=100)
) -> dict[str, Any]:
    if user_id not in set(platform.world.users):
        raise HTTPException(status_code=404, detail=f"no user {user_id}")

    vector = platform.tower.user_vector(user_id)
    index = platform.retriever.index

    started = time.perf_counter()
    approximate = index.search(vector, k)
    approximate_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    exact = index.brute_force(vector, k)
    exact_ms = (time.perf_counter() - started) * 1000

    approximate_keys = {key for key, _ in approximate}
    exact_keys = {key for key, _ in exact}
    overlap = len(approximate_keys & exact_keys)

    topic = platform.world.user_topic[user_id]

    return {
        "user_id": user_id,
        "user_topic": topic,
        "k": k,
        "recall_vs_brute_force": round(overlap / max(len(exact_keys), 1), 4),
        "approximate_ms": round(approximate_ms, 3),
        "exact_ms": round(exact_ms, 3),
        "speedup": round(exact_ms / approximate_ms, 2) if approximate_ms > 0 else 0.0,
        "results": [
            {
                "item_id": key,
                "similarity": round(score, 4),
                "topic": platform.world.catalogue.topic[key],
                "on_topic": platform.world.catalogue.topic[key] == topic,
                "in_exact_set": key in exact_keys,
            }
            for key, score in approximate
        ],
    }


@app.get("/v1/model")
def model(platform: PlatformDep) -> dict[str, Any]:
    report = platform.training
    importance = sorted(report.importance.items(), key=lambda pair: pair[1], reverse=True)

    return {
        "rows": report.rows,
        "positives": report.positives,
        "holdout_auc": round(report.holdout_auc, 4),
        "calibrated": report.calibrated,
        "features": [
            {"name": name, "importance": round(value, 4)} for name, value in importance
        ],
        "tower": {
            "dimension": platform.tower.dimension,
            "first_loss": round(platform.tower_report["first_loss"], 4),
            "final_loss": round(platform.tower_report["final_loss"], 4),
            "users": int(platform.tower_report["users"]),
            "items": int(platform.tower_report["items"]),
        },
    }


@app.get("/v1/registry")
def registry(platform: PlatformDep) -> list[dict[str, Any]]:
    return [
        {
            "label": version.label,
            "version": version.version,
            "stage": version.stage.value,
            "traffic": version.traffic,
            "metrics": version.metrics,
            "created_at": version.created_at.isoformat(),
            "history": [
                {"at": at.isoformat(), "stage": stage.value, "reason": reason}
                for at, stage, reason in version.history
            ],
        }
        for version in platform.registry.versions("ranker")
    ]


@app.post("/v1/registry/promote")
def promote(platform: PlatformDep) -> dict[str, Any]:
    versions = platform.registry.versions("ranker")
    candidate = platform.registry.register(
        "ranker", platform.ranker, {"auc": platform.training.holdout_auc}
    )
    platform.registry.transition(
        "ranker", candidate.version, Stage.SHADOW, "queued behind live traffic"
    )
    platform.registry.transition(
        "ranker", candidate.version, Stage.CANARY, "shadow metrics acceptable"
    )
    return {
        "promoted": candidate.label,
        "stage": candidate.stage.value,
        "traffic": candidate.traffic,
        "previous_versions": len(versions),
    }


@app.post("/v1/registry/rollback")
def rollback(platform: PlatformDep) -> dict[str, Any]:
    rolled = platform.registry.rollback("ranker", "operator asked for a rollback")
    live = platform.registry.live("ranker")
    return {
        "rolled_back": rolled.label,
        "now_live": live.label if live else None,
    }


@app.get("/v1/drift")
def drift(
    platform: PlatformDep, shift: float = Query(0.0, ge=0.0, le=3.0)
) -> list[dict[str, Any]]:
    return [
        {
            "feature": report.feature,
            "psi": round(report.psi, 4),
            "ks_statistic": round(report.ks_statistic, 4),
            "p_value": round(report.p_value, 6),
            "severity": report.severity,
            "drifted": report.drifted,
        }
        for report in platform.drift(shift)
    ]


@app.get("/v1/experiment")
def experiment(platform: PlatformDep) -> dict[str, Any]:
    result = platform.experiment_state()
    return {
        "key": platform.experiment.key,
        "variants": [
            {"name": variant.name, "weight": variant.weight}
            for variant in platform.experiment.variants
        ],
        "holdout": platform.experiment.holdout,
        "decision": result.decision.value,
        "likelihood_ratio": round(result.likelihood_ratio, 4),
        "threshold": round(result.threshold, 2),
        "control": {
            "trials": platform.counters.control_trials,
            "successes": platform.counters.control_successes,
            "rate": round(result.control_rate, 4),
        },
        "treatment": {
            "trials": platform.counters.treatment_trials,
            "successes": platform.counters.treatment_successes,
            "rate": round(result.treatment_rate, 4),
        },
        "lift": round(result.lift, 4),
        "samples": result.samples,
    }


@app.post("/v1/simulate")
def simulate(
    platform: PlatformDep, requests: int = Query(200, ge=1, le=2000)
) -> dict[str, Any]:
    rng = np.random.default_rng()
    chosen = rng.choice(platform.world.users, size=requests, replace=True)

    started = time.perf_counter()
    latencies = []
    for user in chosen:
        result = platform.recommend(str(user))
        latencies.append(result["latency_ms"])
    elapsed = (time.perf_counter() - started) * 1000

    return {
        "requests": requests,
        "wall_ms": round(elapsed, 1),
        "throughput_per_second": round(requests / (elapsed / 1000), 1),
        "latency_p50_ms": round(float(np.percentile(latencies, 50)), 2),
        "latency_p95_ms": round(float(np.percentile(latencies, 95)), 2),
        "latency_p99_ms": round(float(np.percentile(latencies, 99)), 2),
        "experiment": platform.experiment_state().decision.value,
    }


@app.get("/v1/retrieval/benchmark")
def benchmark(
    dimension: int = Query(48, ge=8, le=128),
    k: int = Query(10, ge=1, le=50),
    corpus: int = Query(16000, ge=1000, le=40000),
) -> dict[str, Any]:
    from sifa.index.hnsw import HnswConfig, HnswIndex

    rng = np.random.default_rng(17)
    vectors = rng.normal(size=(corpus, dimension)).astype(np.float32)
    queries = rng.normal(size=(25, dimension)).astype(np.float32)

    index = HnswIndex(dimension, HnswConfig(m=24, ef_construction=200, ef_search=64))
    started = time.perf_counter()
    for position, vector in enumerate(vectors):
        index.add(f"n{position}", vector)
    build_seconds = time.perf_counter() - started

    started = time.perf_counter()
    exact = [index.brute_force(query, k) for query in queries]
    exact_ms = (time.perf_counter() - started) / len(queries) * 1000

    curve: list[dict[str, Any]] = []
    for ef in (32, 64, 128, 256):
        started = time.perf_counter()
        approximate = [index.search(query, k, ef=ef) for query in queries]
        approximate_ms = (time.perf_counter() - started) / len(queries) * 1000

        overlap = sum(
            len({key for key, _ in a} & {key for key, _ in b})
            for a, b in zip(approximate, exact, strict=True)
        )

        curve.append(
            {
                "ef_search": ef,
                "recall": round(overlap / (len(queries) * k), 4),
                "approximate_ms": round(approximate_ms, 3),
                "speedup": round(exact_ms / approximate_ms, 2) if approximate_ms else 0.0,
            }
        )

    return {
        "corpus": corpus,
        "dimension": dimension,
        "k": k,
        "build_seconds": round(build_seconds, 2),
        "exhaustive_ms": round(exact_ms, 3),
        "curve": curve,
    }
