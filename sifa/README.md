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
pip install -e ".[api,dev]"
uvicorn sifa.serving.api:app --port 4700
cd apps/console && npm install && npm run dev
```

Or `docker compose up --build`, which serves the API on 8000 and the console on 3200.

There is no database and no seed step. The service builds a simulated world on
first request — users, items with topics and authors, timestamped interactions —
then trains the tower and the ranker against it. Cold start is about 9 seconds.

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

204 tests. `mypy` runs in strict mode.

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
