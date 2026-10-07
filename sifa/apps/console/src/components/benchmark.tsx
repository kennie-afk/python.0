"use client";

import { useEffect, useState, type FormEvent } from "react";
import { pollBenchmark, startBenchmark } from "@/lib/actions";
import { idleBenchmark, type BenchmarkState } from "@/lib/action-state";
import { Card, Notice, Select, Stat, Table, buttonClass } from "@/components/ui";

const ESTIMATE: Record<string, number> = { "1000": 4, "2000": 10, "4000": 25 };

function Progress({ corpus, running, seconds }: { corpus: string; running: boolean; seconds: number }) {
  const estimate = ESTIMATE[corpus] ?? 10;
  return (
    <div className="space-y-3">
      <button type="submit" className={buttonClass} disabled={running}>
        {running ? `Building… ${seconds}s` : "Run the benchmark"}
      </button>
      {running ? (
        <div role="status" aria-live="polite" className="space-y-1.5">
          <div className="h-1.5 w-full max-w-sm overflow-hidden rounded-sm bg-[var(--color-line)]">
            <div
              className="h-full bg-[var(--color-accent)]"
              style={{ width: `${Math.min(95, (seconds / estimate) * 100)}%` }}
            />
          </div>
          <p className="text-xs text-[var(--color-muted)]">
            Running as a background job on the API and polled every second. Inserting vectors one at a
            time into a graph that is still being built, so the cost grows faster than the corpus. About{" "}
            {estimate} seconds for this size. The API runs one benchmark at a time.
          </p>
        </div>
      ) : null}
    </div>
  );
}

export function Benchmark() {
  const [state, setState] = useState<BenchmarkState>(idleBenchmark);
  const [jobId, setJobId] = useState<string | null>(null);
  const [seconds, setSeconds] = useState(0);
  const [corpus, setCorpus] = useState("2000");

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setState(idleBenchmark);
    const started = await startBenchmark(Number(corpus));
    if (started.error || !started.jobId) {
      setState({ ...idleBenchmark, error: started.error });
      return;
    }
    setSeconds(0);
    setJobId(started.jobId);
  }

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    const tick = window.setInterval(async () => {
      setSeconds((value) => value + 1);
      const job = await pollBenchmark(jobId);
      if (cancelled) return;
      if ("error" in job && !("status" in job)) {
        setJobId(null);
        setState({ ...idleBenchmark, error: job.error });
      } else if ("status" in job && job.status !== "running") {
        setJobId(null);
        setState({ error: job.error, message: null, result: job.result });
      }
    }, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(tick);
    };
  }, [jobId]);

  return (
    <div className="space-y-4">
      <Card
        title="Scale test"
        description="Builds a fresh index of random vectors and compares graph search with exhaustive search at four search widths. This is the honest cost of the index: building it is expensive, querying it is not."
      >
        <form onSubmit={start} className="space-y-4">
          <div className="w-64">
            <Select
              label="Corpus size"
              name="corpus"
              placeholder="Corpus size"
              value={corpus}
              onChange={(event) => setCorpus(event.target.value)}
              options={[
                { value: "1000", label: "1,000 vectors (~4 s)" },
                { value: "2000", label: "2,000 vectors (~10 s)" },
                { value: "4000", label: "4,000 vectors (~25 s)" }
              ]}
            />
          </div>
          {state.error ? <Notice tone="danger">{state.error}</Notice> : null}
          <Progress corpus={corpus} running={jobId !== null} seconds={seconds} />
        </form>
      </Card>

      {state.result ? (
        <Card title={`${state.result.corpus.toLocaleString()} vectors, ${state.result.dimension} dimensions`}>
          <div className="grid gap-3 sm:grid-cols-3">
            <Stat label="Index build" value={`${state.result.build_seconds.toFixed(1)} s`} hint="one insert at a time" tone="warn" />
            <Stat label="Exhaustive query" value={`${state.result.exhaustive_ms.toFixed(2)} ms`} hint="every vector" />
            <Stat
              label="Best speed-up"
              value={`${Math.max(...state.result.curve.map((p) => p.speedup)).toFixed(1)}x`}
              hint="at the narrowest search width"
              tone="good"
            />
          </div>
          <div className="mt-4">
            <Table head={["Search width (ef)", "Recall@" + state.result.k, "Query", "Speed-up"]}>
              {state.result.curve.map((point) => (
                <tr key={point.ef_search} className="border-b border-[var(--color-line)] last:border-0">
                  <td className="px-3 py-2.5 tabular-nums">{point.ef_search}</td>
                  <td className="px-3 py-2.5 tabular-nums">{point.recall.toFixed(3)}</td>
                  <td className="px-3 py-2.5 tabular-nums">{point.approximate_ms.toFixed(3)} ms</td>
                  <td className="px-3 py-2.5 tabular-nums">{point.speedup.toFixed(1)}x</td>
                </tr>
              ))}
            </Table>
          </div>
        </Card>
      ) : null}
    </div>
  );
}
