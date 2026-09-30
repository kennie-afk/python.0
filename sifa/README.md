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
values in `SIFA_API_KEYS`, and the service refuses to start if that variable is unset
or holds a key shorter than 24 characters. `/healthz` stays open so an orchestrator can
probe it. This is not decoration: `/v1/registry/promote` and `/v1/registry/rollback`
change which model is serving live traffic, and `/v1/retrieval/benchmark` builds a real
HNSW index synchronously in the request handler. `SIFA_CORS_ORIGINS` is empty by
default, because the console calls the API from the server, never from the browser.

**Build time, measured, not previously claimed:** the benchmark table below reports
query latency and recall, never how long *building* the index took — that number is
real and severe. Measured on this repository's own `HnswIndex.add`, single-threaded:
500 vectors 1.1s, 1,000 vectors 3.4s, 2,000 vectors 10.2s, 4,000 vectors 24.1s — worse
than quadratic, because `ef_construction=200` makes every insert search the graph it
is still building. The default `corpus` for `/v1/retrieval/benchmark` is therefore
**2,000**, not 40,000: the old default took several minutes and pegged a CPU core for
the whole call with no progress feedback, which is not a reasonable default for a GET
endpoint. The 1,000-40,000 range is still selectable via `?corpus=`, but a caller
asking for the top of that range should expect single-digit minutes, not seconds, and
the endpoint has no timeout of its own — the caller's connection will simply outlast
the server's willingness to keep computing if it gives up first.

There is no database and no seed step. The service builds a simulated world on
first request — users, items with topics and authors, timestamped interactions —
then trains the tower and the ranker against it. Cold start is about 9 seconds.

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
   exploration slot from the bandit.
3. **Search**: graph search against exhaustive search for the same query. At 574 items exhaustive search
   wins (the screen says so); open **Scale test** and run 2,000 vectors. The button disables, counts
   seconds and shows an estimate, because building the index is the expensive part (about 10 s at 2,000,
   about 25 s at 4,000). The API runs one benchmark at a time and answers 409 to a second.
4. **Ranker**: what the model leans on and its calibration.
5. **Registry**: promote a candidate (shadow, then canary at ten percent), then roll it back. Rolling back
   a canary leaves the live model alone; only with no canary does a rollback withdraw the live model and
   restore the previous one. Open a version to read its history.
6. **Experiment**: a sequential test you can look at whenever you like. After 2,500 feeds it reads
   "continue": both arms convert the same, which is the honest answer here.
7. **Drift**: with no injected shift every feature reads stable; choose 1σ or 2σ and watch the PSI and
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

**Sequential testing.** The mixture SPRT holds its false-positive rate near α
when both arms are identical — asserted in `tests/test_experiments.py` over 300
simulated A/A runs with repeated peeking, the exact situation where a fixed-horizon
t-test would leak far past 5%.

## Correctness

```bash
ruff check src tests && mypy src && pytest -q
```

217 tests (`pytest --collect-only`). `mypy` runs in strict mode.

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
  evaluation/   nDCG, recall, MRR, MAP, ECE
  serving/      pipeline, platform, HTTP API
  simulation/   the world the service trains on
apps/console/   Next.js operator console
```
