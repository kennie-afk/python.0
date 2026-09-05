import { api, describeError } from "@/lib/api";
import type { ExperimentState } from "@/lib/types";
import { Badge, Card, Meter, Notice, PageHeader, Stat } from "@/components/ui";

const EXPLANATION: Record<string, string> = {
  continue: "Neither boundary has been crossed. Keep serving and read it again later.",
  treatment_wins: "The treatment crossed the upper boundary. It can be promoted.",
  control_wins: "The control crossed the upper boundary. The treatment is worse.",
  no_difference: "The ratio fell below the futility boundary. There is no effect worth chasing."
};

export default async function ExperimentPage() {
  let state: ExperimentState | null = null;
  let error: string | null = null;

  try {
    state = await api.get<ExperimentState>("/v1/experiment");
  } catch (caught) {
    error = describeError(caught);
  }

  if (error || !state) {
    return (
      <>
        <PageHeader title="Experiment" />
        <Notice tone="danger">{error}</Notice>
      </>
    );
  }

  const progress = Math.min(state.likelihood_ratio / state.threshold, 1);

  return (
    <>
      <PageHeader
        title="Experiment"
        subtitle="A mixture sequential probability ratio test. Unlike a fixed horizon test, this can be looked at whenever you like without inflating the false positive rate."
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Control" value={`${(state.control.rate * 100).toFixed(2)}%`} hint={`${state.control.trials.toLocaleString()} served`} />
        <Stat label="Treatment" value={`${(state.treatment.rate * 100).toFixed(2)}%`} hint={`${state.treatment.trials.toLocaleString()} served`} />
        <Stat
          label="Lift"
          value={`${(state.lift * 100).toFixed(2)}%`}
          tone={state.lift > 0 ? "good" : state.lift < 0 ? "danger" : undefined}
        />
        <Stat label="Observations" value={state.samples.toLocaleString()} hint="across both arms" />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card title="Decision" description="What the test says right now.">
          <div className="space-y-5">
            <Badge value={state.decision} />
            <p className="text-sm text-[var(--color-muted)]">
              {EXPLANATION[state.decision] ?? ""}
            </p>

            <div>
              <div className="mb-1.5 flex items-baseline justify-between">
                <span className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
                  Progress toward the boundary
                </span>
                <span className="text-sm tabular-nums">
                  {state.likelihood_ratio.toExponential(2)} / {state.threshold}
                </span>
              </div>
              <Meter value={progress} />
            </div>

            <p className="text-xs leading-relaxed text-[var(--color-faint)]">
              The boundary is the reciprocal of the false positive rate, so a five percent alpha
              stops at a likelihood ratio of twenty. Below one twentieth the test declares
              futility instead of running forever.
            </p>
          </div>
        </Card>

        <Card title="Assignment" description="Consistent hashing, so a person keeps their arm across sessions.">
          <div className="space-y-4">
            {state.variants.map((variant) => (
              <div key={variant.name}>
                <div className="mb-1.5 flex items-baseline justify-between">
                  <span className="text-sm">{variant.name}</span>
                  <span className="text-sm tabular-nums text-[var(--color-muted)]">
                    {((variant.weight / state.variants.reduce((sum, v) => sum + v.weight, 0)) * (1 - state.holdout) * 100).toFixed(0)}%
                  </span>
                </div>
                <Meter value={variant.weight / state.variants.reduce((sum, v) => sum + v.weight, 0)} />
              </div>
            ))}
            <div>
              <div className="mb-1.5 flex items-baseline justify-between">
                <span className="text-sm">holdout</span>
                <span className="text-sm tabular-nums text-[var(--color-muted)]">
                  {(state.holdout * 100).toFixed(0)}%
                </span>
              </div>
              <Meter value={state.holdout} />
            </div>
            <p className="text-xs leading-relaxed text-[var(--color-faint)]">
              The holdout never sees any treatment. It is how you measure what the whole system is
              worth, not just the latest change to it.
            </p>
          </div>
        </Card>
      </div>
    </>
  );
}
