# Sifa

Retrieval, ranking and experimentation for a personalised feed, built end to end
without a vector database, a feature platform or a managed experiment service.
The point of the project is the parts that are usually bought: the ANN index,
the point-in-time feature store, the calibrated ranker, the sequential test and
the rollout guard are all implemented here and measured.

`sifa` is Swahili for *reputation, what is said about you* — which is what a
ranking system is really estimating.

## What is in it

| Layer | What it does | Where |
| --- | --- | --- |
| Vector index | HNSW built from scratch: layered graph, greedy descent, neighbour heuristic | `src/sifa/index/hnsw.py` |
| Retrieval | Two-tower model trained with sampled softmax over in-batch negatives | `src/sifa/retrieval/two_tower.py` |
| Feature store | Point-in-time correct lookups that refuse to read a value recorded after the request | `src/sifa/features/store.py` |
| Ranking | Gradient-boosted ranker with Platt scaling on a held-out slice | `src/sifa/ranking/ranker.py` |
| Policy | Freshness decay, MMR diversification, per-author caps | `src/sifa/policy/rules.py` |
| Exploration | Thompson sampling over topics with optional decay | `src/sifa/bandits/thompson.py` |
| Experiments | Hash-bucketed assignment and a mixture SPRT that can stop early without inflating α | `src/sifa/experiments/` |
| Monitoring | PSI and KS drift on live features, plus a rollout guard on CTR, calibration and latency | `src/sifa/monitoring/` |
| Registry | Draft → shadow → canary → live with enforced transitions and one-call rollback | `src/sifa/registry/models.py` |

## Running it

```bash
cp .env.example .env
export SIFA_API_KEYS=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

pip install -e ".[api,dev]"
uvicorn sifa.serving.api:app --port 4700
cd apps/console && npm install && npm run dev
```

Or `docker compose up --build`, which serves the API on 8000 and the console on 3200.

Every `/v1` route requires an `X-Api-Key` header matching one of the comma-separated
entries in `SIFA_API_KEYS`, and the service refuses to start if that variable is unset
or holds a key shorter than 24 characters. An entry may carry a role, `key:viewer`,
`key:operator` or `key:admin`; a bare key is an admin, as before. Viewers read; operators
also record feedback, run `/simulate` and start benchmarks and read the export; admins also
promote, advance, roll back and switch the outcome mode. Listing an old and a new key
together is how a key is rotated. The acting key (a short hash, never the key) is recorded
against every registry transition, and `/simulate`, `/retrieval/benchmark` (POST) and
`/registry/promote` are limited to `SIFA_EXPENSIVE_PER_MINUTE` (12) per key; rate limits live
in process memory. `/healthz` is liveness and stays open; `/readyz` is readiness and answers
503 until the models are built or restored (the platform builds on a background thread, so
`/v1` answers 503 with `Retry-After` meanwhile). This is not decoration: `/v1/registry/promote`
and `/v1/registry/rollback` change which model is serving live traffic, and a benchmark builds
a real HNSW index. `SIFA_CORS_ORIGINS` is empty by
default, because the console calls the API from the server, never from the browser.

**Build time, measured, not previously claimed:** the benchmark table below reports
query latency and recall, never how long *building* the index took — that number is
real and severe. Measured on this repository's own `HnswIndex.add`, single-threaded:
500 vectors 1.1s, 1,000 vectors 3.4s, 2,000 vectors 10.2s, 4,000 vectors 24.1s — worse
than quadratic, because `ef_construction=200` makes every insert search the graph it
is still building. The default `corpus` for `/v1/retrieval/benchmark` is therefore
**2,000**, not 40,000: the old default took several minutes and pegged a CPU core for
the whole call with no progress feedback. The benchmark is now a background job:
`POST /v1/retrieval/benchmark?corpus=` returns 202 and a `job_id` at once, and
`GET /v1/retrieval/benchmark/{job_id}` reports `running`, `done` (with the result) or
`failed`; the console's Scale test polls it every second. Only one job runs at a time (409
otherwise) and the last 20 are kept in memory. The 1,000-40,000 range is selectable, with
single-digit minutes expected at the top.

There is no seed step. The service builds a simulated world, then trains the tower and the
ranker against it; a cold start is about 9 seconds.

## State, feedback and what is still simulated

**Persisted** (stdlib `sqlite3`, WAL mode, schema version 1, under `SIFA_STATE_DIR`, default
`./state`, a volume in compose): every registry version with its stage, traffic, metrics, model
card and full history including who did it; experiment counters; Thompson-sampling arms; the
serving windows the rollout guard reads; impressions (30-day retention), feedback and the alert
outbox. Model artifacts are `.npz` files of plain arrays loaded with `allow_pickle=False`, each
checked against a SHA-256 recorded in the database; a file that does not match is refused. The
tower is stored as arrays and the HNSW graph is rebuilt from those vectors on boot. The gradient
boosted ranker is stored as its tree arrays and served by a numpy traversal that reproduces
scikit-learn's scores to 1e-9 (asserted). On boot nothing is retrained unless no artifact exists
or the *data fingerprint* (data, features, model definitions) changed; then the old database is
set aside as `*.stale-<time>` and a fresh one is built. **Not persisted:** the buffer of served
feature rows used for live drift (the last 5,000 rows), rate-limit counters, benchmark jobs.
State is one SQLite file, so **run a single replica** until the state moves to a shared store.

**Feedback.** `GET /v1/feed/{user}` stores an impression (request id, model version, stage,
variant, the items served). `POST /v1/feedback {request_id, item_id, clicked}` records what the
person did; a repeat of the same pair is stored and counted once, an unknown request id is 404,
an item that was not in that feed is 400. Feedback always trains the Thompson-sampling arm for
the item's topic. `SIFA_OUTCOME_MODE` (or `POST /v1/outcome-mode`, admin) chooses where the
experiment counters and canary/live windows get outcomes: **`simulated`** (default) computes the
outcome from the simulated world's topics and says so in every response (`outcome_source`);
**`feedback`** counts each feed as a trial and its first click as the success. Switching modes
resets the counters and windows because the two are not comparable. The world itself, its users
and its clicks remain simulated; nothing here is production traffic.

**Model card.** `GET /v1/registry/{version}` and the console's Registry page show, per version:
data fingerprint, ranker config, seeds, library versions, git SHA (if it can be told),
metrics, feature-schema hash, training time and artifact hash. `GET /v1/registry/export` returns
the full history, cards and alert log as one JSON document (operator).

**Drift on served traffic.** `GET /v1/drift/live` compares the feature rows the ranker actually
received while serving with the training reference (needs 100 rows). `/v1/drift?shift=` keeps
the injected shift and labels it as such. The live comparison exposed a real train/serve
mismatch: `topic_match` and `item_quality` are the simulator's own click-model inputs, which the
ranker was trained on but serving never supplied, so it read them as 0 live. They are no longer
training features (held-out AUC 0.9726 against 0.9729 with them, so the oracle was not doing the
work); the endpoint still reports any such skew as `trained_but_not_served`, now empty.

**Alerts and scheduled retraining.** A background thread (every `SIFA_SCHEDULER_INTERVAL`, 30
s) delivers the alert outbox to `SIFA_ALERT_WEBHOOK` (guard rollbacks, newly drifted features,
scheduled retrains), retrying five times and keeping every alert in the export; and, only if
`SIFA_RETRAIN_EVERY_SECONDS` is above 0, trains a candidate that enters as a canary and must
still pass the guard.

## Demo in five minutes

```bash
cp .env.example .env
# in .env: set SIFA_API_KEYS to 24+ random characters and SIFA_DEMO_WARMUP=2500
docker compose up -d --build        # api on 8000, console on 3200; the API needs about a minute
```

`SIFA_DEMO_WARMUP=2500` makes the API serve 2,500 real feeds through the real pipeline before it reports
healthy (about 30 seconds) and record a release history, so the Experiment, Drift and Registry screens
open with something on them instead of at zero. The release history (a promoted v2 and a canary that was
rolled back) is written through the real registry transitions and every reason is tagged
`(demo warm-up)`. Leave it at 0 for a cold start. Host ports are `SIFA_API_PORT` and `SIFA_CONSOLE_PORT`.
The console has no login: it calls the API server-side with the key.

What to show, in order:

1. **Overview**: what is live (`ranker:v2`), ranker AUC, index size, the rollout guard, and the experiment's
   verdict after 2,500 served feeds.
2. **Feed**: pick a person. Every row shows why it is there: retrieved, re-ranked, diversified, or an
   exploration slot from the bandit. The Feedback panel records a click or skip against the request.
3. **Search**: graph search against exhaustive search for the same query. At 574 items exhaustive search
   wins (the screen says so); open **Scale test** and run 2,000 vectors. The button disables, counts
   seconds and shows an estimate, because building the index is the expensive part (about 10 s at 2,000,
   about 25 s at 4,000). It runs as a polled background job; the API runs one at a time and answers 409
   to a second.
4. **Ranker**: what the model leans on and its calibration.
5. **Registry**: each version's model card; promote a candidate (shadow, then canary at ten percent), then roll it back. Rolling back
   a canary leaves the live model alone; only with no canary does a rollback withdraw the live model and
   restore the previous one. Open a version to read its history.
6. **Experiment**: a sequential test you can look at whenever you like. After 2,500 feeds it reads
   "continue": both arms convert the same, which is the honest answer here.
7. **Drift**: live serving traffic against the training reference, then the injected demonstration; with no shift every feature reads stable; choose 1σ or 2σ and watch the PSI and
   KS columns catch it.
8. **Load**: serve 500 or 1,000 feeds and read throughput and p50/p95/p99.

`node tools/ui_flow_check.mjs http://localhost:3200` drives these screens in headless Chrome and asserts
the results; `node tools/shot.mjs` takes screenshots.

## Measured, not claimed

Every number below comes from the code in this repository on a 4-core laptop.

**Serving.** 240 users, 600 items, 574 of them retrievable, 400 requests:

```
p50  7.5 ms      p95  10.7 ms      p99  12.6 ms
```

A request retrieves 200 candidates, joins point-in-time features, scores them,
applies freshness and author caps, diversifies with MMR and returns 20.

**Ranker.** Held-out AUC 0.973, Platt-calibrated on a 25% slice. The two-tower
loss falls from 2.199 to 0.336 over 12 epochs.

**The index, including where it loses.** 16,000 vectors in 48 dimensions,
recall measured against exhaustive search over the same index:

| ef_search | recall@10 | query | vs exhaustive |
| --- | --- | --- | --- |
| 32 | 0.820 | 3.11 ms | 0.6× |
| 64 | 0.936 | 4.23 ms | 0.4× |
| 128 | 0.984 | 6.71 ms | 0.3× |
| 256 | 1.000 | 12.78 ms | 0.1× |

The recall curve behaves exactly as it should. The speed-up does not: at this
corpus size the graph index is *slower* than brute force. That is not a defect
in the graph, it is arithmetic. Exhaustive search here is a single 16,000 × 48
matmul that NumPy hands to BLAS, while the traversal pays Python overhead on
every hop. Measuring brute force as the corpus grows shows where the two meet:

```
     16,000 vectors   0.74 ms
     50,000 vectors   4.33 ms
    150,000 vectors   5.27 ms
    400,000 vectors  11.67 ms
```

Brute force stays memory-bandwidth bound and cheap. Graph traversal cost grows
only logarithmically, so it overtakes somewhere above 50,000 vectors and the
margin widens from there — but a pure-Python HNSW does not earn its place in a
16,000-item catalogue, and the honest thing is to say so rather than quote a
recall number and leave the latency out.

**On a public dataset.** Everything above runs on a simulated world. To check the retrieval
stack against real behaviour, `tools/movielens_eval.py` evaluates it on MovieLens
`ml-latest-small` (GroupLens): ratings of 4.0 or more are positives, each user's last
two positives are held out by timestamp (validation, then test), and every method ranks
the whole catalogue minus the user's history, not 100 sampled negatives. Hyper-parameters
were chosen on validation only; the model was then refit and scored once on test.
542 users, 6,227 items, 38,548 training interactions (61 users dropped because their
held-out item never appears in training).

| Method | Hit rate @10 | 95% interval | NDCG @10 |
| --- | --- | --- | --- |
| Random | 0.00% | 0.00 to 0.00% | 0.0000 |
| Most popular | 4.06% | 2.58 to 5.72% | 0.0220 |
| Sifa two-tower, exact scoring | 5.35% | 3.51 to 7.20% | 0.0263 |
| Sifa two-tower, served through its HNSW | 5.35% | 3.51 to 7.20% | 0.0263 |
| Item-based nearest neighbours (cosine) | 6.09% | 4.24 to 8.30% | 0.0288 |

The two-tower beats popularity on point estimates and loses to a plain item-based
neighbourhood model, and with 542 test users every interval overlaps its neighbour's, so
none of these gaps is established. Serving through the index changes nothing in ranking
quality. This is a small dataset and a small model trained with uniform negatives in a
pure-Python loop; it is evidence that the pipeline works on real data, not that it beats a
tuned production recommender.

Against FAISS (`IndexHNSWFlat`, same M 16 and efConstruction 200) on the same 6,227
trained item vectors, 542 queries:

| ef_search | Sifa recall@10 | Sifa p50 | FAISS recall@10 | FAISS p50 |
| --- | --- | --- | --- | --- |
| 16 | 0.923 | 0.94 ms | 0.953 | 0.04 ms |
| 64 | 0.989 | 2.91 ms | 0.998 | 0.08 ms |
| 128 | 0.996 | 5.51 ms | 0.999 | 0.17 ms |
| 256 | 0.999 | 10.38 ms | 1.000 | 0.35 ms |

Recall is close; speed is not. Sifa's graph is 25 to 35 times slower per query and takes
45 s to build against FAISS's 0.64 s, and at this catalogue size exact NumPy scoring
(0.175 ms) is faster than Sifa's index and level with FAISS at ef 128. Use the from-scratch index to
understand the algorithm, and FAISS or hnswlib in production. Reproduce with
`pip install -e ".[bench]"` then `python tools/movielens_eval.py` (tens of minutes: the two-tower trains in a pure-Python loop);
results are stored in `src/sifa/evaluation/movielens_results.json` and served at
`/v1/evaluation/movielens` and in the console's Evaluate page.

**Sequential testing.** The mixture SPRT holds its false-positive rate near α
when both arms are identical — asserted in `tests/test_experiments.py` over 300
simulated A/A runs with repeated peeking, the exact situation where a fixed-horizon
t-test would leak far past 5%.

## Correctness

```bash
ruff check src tests && mypy src && pytest -q
```

265 tests (`pytest --collect-only`). `mypy` runs in strict mode.

The tests are written to catch real failures rather than to raise coverage, and
they have: the SPRT's mixture variance was wrong until the A/A test caught it,
PSI read pure noise as drift on binary features until the low-cardinality test
caught it, MMR had its λ inverted until a pure-relevance test caught it, and
`/v1/retrieval/benchmark` was shadowed by `/v1/retrieval/{user_id}` — unreachable
in production — until an API test hit it.

## Layout

```
src/sifa/
  index/        HNSW
  retrieval/    two-tower model and retriever
  features/     point-in-time feature store and schema
  ranking/      LTR model and Platt calibration
  policy/       freshness, MMR, caps
  bandits/      Thompson sampling
  experiments/  assignment and mixture SPRT
  monitoring/   drift detection and rollout guard
  registry/     model versions and stage transitions
  persistence/  sqlite state store and npz artifacts
  evaluation/   nDCG, recall, MRR, MAP, ECE
  serving/      pipeline, platform, HTTP API
  simulation/   the world the service trains on
apps/console/   Next.js operator console
```
