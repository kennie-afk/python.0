"use client";

import { useActionState, useEffect, useState } from "react";
import { useFormStatus } from "react-dom";
import { runBenchmark } from "@/lib/actions";
import { idleBenchmark } from "@/lib/action-state";
import { Card, Notice, Select, Stat, Table, buttonClass } from "@/components/ui";

const ESTIMATE: Record<string, number> = { "1000": 4, "2000": 10, "4000": 25 };

function Progress({ corpus }: { corpus: string }) {
  const { pending } = useFormStatus();
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!pending) {
      setSeconds(0);
      return;
    }
    const timer = window.setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => window.clearInterval(timer);
  }, [pending]);

  const estimate = ESTIMATE[corpus] ?? 10;
  return (
    <div className="space-y-3">
      <button type="submit" className={buttonClass} disabled={pending}>
        {pending ? `Building… ${seconds}s` : "Run the benchmark"}
      </button>
      {pending ? (
        <div role="status" aria-live="polite" className="space-y-1.5">
          <div className="h-1.5 w-full max-w-sm overflow-hidden rounded-sm bg-[var(--color-line)]">
            <div
              className="h-full bg-[var(--color-accent)] transition-all duration-1000"
              style={{ width: `${Math.min(95, (seconds / estimate) * 100)}%` }}
            />
          </div>
          <p className="text-xs text-[var(--color-muted)]">
            Inserting vectors one at a time into a graph that is still being built, so the cost grows faster
            than the corpus. About {estimate} seconds for this size. The API runs one benchmark at a time.
          </p>
        </div>
      ) : null}
    </div>
  );
}

export function Benchmark() {
  const [state, action] = useActionState(runBenchmark, idleBenchmark);
  const [corpus, setCorpus] = useState("2000");

  return (
    <div className="space-y-4">
      <Card
        title="Scale test"
        description="Builds a fresh index of random vectors and compares graph search with exhaustive search at four search widths. This is the honest cost of the index: building it is expensive, querying it is not."
      >
        <form action={action} className="space-y-4">
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
          <Progress corpus={corpus} />
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
