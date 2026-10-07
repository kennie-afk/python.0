"""Evaluate Sifa's retrieval stack on MovieLens, a public dataset, not the simulator.

Protocol (leave-last-out, the usual offline setup for implicit feedback):

* a rating of 4.0 or more counts as a positive interaction;
* each user's positives are ordered by timestamp, the last is the test item, the one
  before it is the validation item, everything earlier is training;
* hyper-parameters are chosen on validation only, then the model is refit on
  training plus validation and scored once on test;
* every model ranks the whole catalogue (minus the user's own history), not a sample of
  100 negatives, which flatters every method and hides the differences between them.

Run from the repository root:

    python tools/movielens_eval.py
"""

from __future__ import annotations

import argparse
import io
import json
import platform
import time
import urllib.request
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import sparse

from sifa.evaluation.metrics import recall_at_k
from sifa.index.hnsw import HnswConfig, HnswIndex
from sifa.retrieval.two_tower import TwoTowerConfig, TwoTowerModel

DATASET_URL = "https://files.grouplens.org/datasets/movielens/ml-latest-small.zip"
CACHE = Path(".cache/movielens")
K = 10


@dataclass(slots=True)
class Split:
    train: dict[int, list[int]]
    validation: dict[int, int]
    test: dict[int, int]
    item_count: int
    dropped_cold: int


def load_ratings() -> np.ndarray:
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / "ratings.csv"
    if not target.exists():
        with urllib.request.urlopen(DATASET_URL, timeout=120) as response:
            archive = zipfile.ZipFile(io.BytesIO(response.read()))
        target.write_bytes(archive.read("ml-latest-small/ratings.csv"))
    return np.loadtxt(target, delimiter=",", skiprows=1, dtype=np.float64)


def build_split(ratings: np.ndarray, threshold: float = 4.0, minimum: int = 5) -> Split:
    positives: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for user, item, rating, stamp in ratings:
        if rating >= threshold:
            positives[int(user)].append((stamp, int(item)))

    train: dict[int, list[int]] = {}
    validation: dict[int, int] = {}
    test: dict[int, int] = {}
    for user, events in positives.items():
        if len(events) < minimum:
            continue
        ordered = [item for _, item in sorted(events)]
        train[user] = ordered[:-2]
        validation[user] = ordered[-2]
        test[user] = ordered[-1]

    seen = {item for items in train.values() for item in items}
    dropped = 0
    for user in list(train):
        if validation[user] not in seen or test[user] not in seen:
            dropped += 1
            del train[user], validation[user], test[user]

    remap = {item: i for i, item in enumerate(sorted(seen))}
    users = {user: i for i, user in enumerate(sorted(train))}
    return Split(
        train={users[u]: [remap[i] for i in items] for u, items in train.items()},
        validation={users[u]: remap[i] for u, i in validation.items()},
        test={users[u]: remap[i] for u, i in test.items()},
        item_count=len(remap),
        dropped_cold=dropped,
    )


def interaction_matrix(history: dict[int, list[int]], users: int, items: int) -> sparse.csr_matrix:
    rows = [u for u, seen in history.items() for _ in seen]
    cols = [i for seen in history.values() for i in seen]
    data = np.ones(len(rows), dtype=np.float32)
    matrix = sparse.csr_matrix((data, (rows, cols)), shape=(users, items))
    matrix.data[:] = 1.0
    return matrix


def rank_of_target(
    scores: np.ndarray, history: dict[int, list[int]], target: dict[int, int]
) -> np.ndarray:
    """1-based rank of each user's target among items they have not already seen.

    Ties are resolved against the model, so a method cannot win by scoring everything
    the same.
    """
    ranks = np.empty(len(target), dtype=np.int64)
    for row, (user, item) in enumerate(sorted(target.items())):
        row_scores = scores[user].copy()
        row_scores[history[user]] = -np.inf
        wanted = row_scores[item]
        ranks[row] = int(np.sum(row_scores >= wanted))
    return ranks


def summarise(ranks: np.ndarray, seed: int = 7) -> dict[str, float]:
    hit = (ranks <= K).astype(np.float64)
    gain = np.where(ranks <= K, 1.0 / np.log2(ranks + 1.0), 0.0)
    rng = np.random.default_rng(seed)
    boot = [float(hit[rng.integers(0, len(hit), len(hit))].mean()) for _ in range(2000)]
    return {
        "hit_rate_at_10": round(float(hit.mean()), 4),
        "hit_rate_ci95_low": round(float(np.percentile(boot, 2.5)), 4),
        "hit_rate_ci95_high": round(float(np.percentile(boot, 97.5)), 4),
        "ndcg_at_10": round(float(gain.mean()), 4),
        "mrr_at_10": round(float(np.where(ranks <= K, 1.0 / ranks, 0.0).mean()), 4),
    }


def popularity_scores(matrix: sparse.csr_matrix, users: int) -> np.ndarray:
    counts = np.asarray(matrix.sum(axis=0)).reshape(-1)
    return np.tile(counts, (users, 1))


def item_knn_scores(matrix: sparse.csr_matrix) -> np.ndarray:
    counts = np.asarray(matrix.sum(axis=0)).reshape(-1)
    norm = sparse.diags(1.0 / np.sqrt(np.maximum(counts, 1.0)))
    scaled = matrix @ norm
    user_affinity = matrix @ scaled.T
    return np.asarray((user_affinity @ scaled).todense(), dtype=np.float32)


def train_two_tower(
    history: dict[int, list[int]], config: TwoTowerConfig
) -> tuple[TwoTowerModel, dict[str, float], float]:
    pairs = [(f"u{u}", f"i{i}") for u, items in history.items() for i in items]
    model = TwoTowerModel(config)
    started = time.perf_counter()
    stats = model.fit(pairs)
    return model, stats, time.perf_counter() - started


def two_tower_scores(model: TwoTowerModel, users: int, items: int) -> np.ndarray:
    user_matrix = np.vstack([model.user_vector(f"u{u}") for u in range(users)])
    item_matrix = np.vstack([model.item_vector(f"i{i}") for i in range(items)])
    return np.asarray(user_matrix @ item_matrix.T, dtype=np.float32)


def percentile_ms(samples: list[float], q: float) -> float:
    return round(float(np.percentile(samples, q)) * 1000.0, 3)


def ann_study(
    model: TwoTowerModel, split: Split, history: dict[int, list[int]], ef_values: list[int]
) -> dict[str, object]:
    import faiss

    users = len(split.test)
    items = split.item_count
    dim = model.dimension
    item_matrix = np.vstack([model.item_vector(f"i{i}") for i in range(items)]).astype(np.float32)
    queries = np.vstack([model.user_vector(f"u{u}") for u in range(users)]).astype(np.float32)

    truth = np.argsort(-(queries @ item_matrix.T), axis=1)[:, :K]

    started = time.perf_counter()
    sifa_index = HnswIndex(dim, HnswConfig(m=16, ef_construction=200, ef_search=64))
    for i in range(items):
        sifa_index.add(f"i{i}", item_matrix[i])
    sifa_build = time.perf_counter() - started

    started = time.perf_counter()
    faiss_index = faiss.IndexHNSWFlat(dim, 16, faiss.METRIC_INNER_PRODUCT)
    faiss_index.hnsw.efConstruction = 200
    faiss_index.add(item_matrix)
    faiss_build = time.perf_counter() - started

    rows: list[dict[str, object]] = []
    for ef in ef_values:
        sifa_hits: list[float] = []
        sifa_times: list[float] = []
        for u in range(users):
            began = time.perf_counter()
            found = sifa_index.search(queries[u], K, ef=ef)
            sifa_times.append(time.perf_counter() - began)
            sifa_hits.append(recall_at_k([k for k, _ in found], {f"i{i}" for i in truth[u]}, K))

        faiss_index.hnsw.efSearch = ef
        faiss_hits: list[float] = []
        faiss_times: list[float] = []
        for u in range(users):
            began = time.perf_counter()
            _, ids = faiss_index.search(queries[u : u + 1], K)
            faiss_times.append(time.perf_counter() - began)
            faiss_hits.append(len(set(ids[0].tolist()) & set(truth[u].tolist())) / K)

        rows.append(
            {
                "ef_search": ef,
                "sifa_recall_at_10": round(float(np.mean(sifa_hits)), 4),
                "sifa_p50_ms": percentile_ms(sifa_times, 50),
                "sifa_p95_ms": percentile_ms(sifa_times, 95),
                "faiss_recall_at_10": round(float(np.mean(faiss_hits)), 4),
                "faiss_p50_ms": percentile_ms(faiss_times, 50),
                "faiss_p95_ms": percentile_ms(faiss_times, 95),
            }
        )

    brute: list[float] = []
    for u in range(users):
        began = time.perf_counter()
        np.argpartition(-(item_matrix @ queries[u]), K)[:K]
        brute.append(time.perf_counter() - began)

    return {
        "vectors": items,
        "dimension": dim,
        "queries": users,
        "sifa_build_seconds": round(sifa_build, 2),
        "faiss_build_seconds": round(faiss_build, 3),
        "numpy_exact_p50_ms": percentile_ms(brute, 50),
        "numpy_exact_p95_ms": percentile_ms(brute, 95),
        "sweep": rows,
    }


def ann_end_to_end(
    model: TwoTowerModel, split: Split, history: dict[int, list[int]], ef: int
) -> dict[str, float]:
    """Hit rate and NDCG when Sifa's own HNSW serves the retrieval, not exact scoring."""
    index = model.build_index(HnswConfig(m=16, ef_construction=200, ef_search=ef))
    ranks: list[int] = []
    for user, target in sorted(split.test.items()):
        vector = model.user_vector(f"u{user}")
        blocked = {f"i{i}" for i in history[user]}
        found = [k for k, _ in index.search(vector, K + len(blocked), ef=max(ef, K + len(blocked)))]
        found = [k for k in found if k not in blocked][:K]
        ranks.append(found.index(f"i{target}") + 1 if f"i{target}" in found else K + 1)
    return summarise(np.asarray(ranks))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", type=Path, default=Path("src/sifa/evaluation/movielens_results.json")
    )
    parser.add_argument("--quick", action="store_true", help="one configuration, for a smoke run")
    args = parser.parse_args()

    ratings = load_ratings()
    split = build_split(ratings)
    users = len(split.test)
    items = split.item_count
    print(f"users {users}  items {items}  dropped(cold target) {split.dropped_cold}", flush=True)

    train_matrix = interaction_matrix(split.train, users, items)
    train_plus_val = {u: [*split.train[u], split.validation[u]] for u in split.train}
    full_matrix = interaction_matrix(train_plus_val, users, items)

    grid = [TwoTowerConfig(dimension=64, epochs=12, learning_rate=0.08)]
    if not args.quick:
        grid = [
            TwoTowerConfig(dimension=d, epochs=e, learning_rate=lr, negatives=n)
            for d, e, lr, n in [
                (32, 12, 0.08, 8),
                (64, 12, 0.08, 8),
                (64, 24, 0.08, 8),
                (64, 24, 0.04, 16),
            ]
        ]

    tuning: list[dict[str, object]] = []
    best_config = grid[0]
    best_score = -1.0
    for config in grid:
        model, stats, seconds = train_two_tower(split.train, config)
        scores = two_tower_scores(model, users, items)
        outcome = summarise(rank_of_target(scores, split.train, split.validation))
        tuning.append(
            {
                "dimension": config.dimension,
                "epochs": config.epochs,
                "learning_rate": config.learning_rate,
                "negatives": config.negatives,
                "final_loss": round(stats["final_loss"], 4),
                "train_seconds": round(seconds, 1),
                "validation_ndcg_at_10": outcome["ndcg_at_10"],
                "validation_hit_rate_at_10": outcome["hit_rate_at_10"],
            }
        )
        print("tune", tuning[-1], flush=True)
        if outcome["ndcg_at_10"] > best_score:
            best_score = outcome["ndcg_at_10"]
            best_config = config

    random_scores = np.random.default_rng(5).random((users, items)).astype(np.float32)
    results = {
        "random": summarise(rank_of_target(random_scores, train_plus_val, split.test)),
        "popularity": summarise(
            rank_of_target(popularity_scores(full_matrix, users), train_plus_val, split.test)
        ),
        "item_knn_cosine": summarise(
            rank_of_target(item_knn_scores(full_matrix), train_plus_val, split.test)
        ),
    }

    model, stats, seconds = train_two_tower(train_plus_val, best_config)
    exact = two_tower_scores(model, users, items)
    results["sifa_two_tower_exact"] = summarise(rank_of_target(exact, train_plus_val, split.test))
    for name, outcome in results.items():
        print(name, outcome, flush=True)

    ef_end = 128
    results["sifa_two_tower_hnsw"] = ann_end_to_end(model, split, train_plus_val, ef_end)
    print("sifa_two_tower_hnsw", results["sifa_two_tower_hnsw"], flush=True)

    ann = ann_study(model, split, train_plus_val, [16, 32, 64, 128, 256])

    payload = {
        "dataset": "MovieLens ml-latest-small (GroupLens), ratings >= 4.0 as positives",
        "users": users,
        "items": items,
        "train_interactions": int(train_matrix.nnz),
        "dropped_users_cold_target": split.dropped_cold,
        "protocol": "leave-last-out by timestamp, full-catalogue ranking, ties against the model",
        "tuning_on_validation": tuning,
        "chosen": {
            "dimension": best_config.dimension,
            "epochs": best_config.epochs,
            "learning_rate": best_config.learning_rate,
            "negatives": best_config.negatives,
            "final_train_seconds": round(seconds, 1),
        },
        "test": results,
        "ann_end_to_end_ef": ef_end,
        "ann_index": ann,
        "machine": f"{platform.machine()}, Python {platform.python_version()}",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
