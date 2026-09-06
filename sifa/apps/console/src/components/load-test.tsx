"use client";

import { useActionState } from "react";
import { useFormStatus } from "react-dom";
import { runLoadTest } from "@/lib/actions";
import { idleSimulation } from "@/lib/action-state";
import { Card, Notice, Select, Stat, buttonClass } from "@/components/ui";

function RunButton() {
  const { pending } = useFormStatus();
  return (
    <button type="submit" className={buttonClass} disabled={pending}>
      {pending ? "Serving…" : "Run the load test"}
    </button>
  );
}

export function LoadTest() {
  const [state, action] = useActionState(runLoadTest, idleSimulation);

  return (
    <div className="space-y-6">
      <Card
        title="Serve a burst of feeds"
        description="Each request runs the whole pipeline: retrieve, assemble features, rank, diversify and explore."
      >
        <form action={action} className="space-y-4">
          <div className="w-64">
            <Select
              label="Requests"
              name="requests"
              placeholder="How many"
              defaultValue="500"
              options={[
                { value: "100", label: "100 requests" },
                { value: "500", label: "500 requests" },
                { value: "1000", label: "1,000 requests" },
                { value: "2000", label: "2,000 requests" }
              ]}
            />
          </div>
          {state.error ? <Notice tone="danger">{state.error}</Notice> : null}
          <RunButton />
        </form>
      </Card>

      {state.result ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="Throughput"
              value={`${state.result.throughput_per_second.toFixed(0)}/s`}
              hint="single process, no cache"
              tone="good"
            />
            <Stat label="p50" value={`${state.result.latency_p50_ms.toFixed(1)} ms`} />
            <Stat label="p95" value={`${state.result.latency_p95_ms.toFixed(1)} ms`} />
            <Stat
              label="p99"
              value={`${state.result.latency_p99_ms.toFixed(1)} ms`}
              tone={state.result.latency_p99_ms > 50 ? "warn" : "good"}
            />
          </div>

          <Notice>
            {state.result.requests.toLocaleString()} feeds in{" "}
            {(state.result.wall_ms / 1000).toFixed(2)} seconds. The experiment now reads{" "}
            {state.result.experiment.replaceAll("_", " ")}.
          </Notice>
        </>
      ) : null}
    </div>
  );
}
