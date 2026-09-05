import { api, describeError } from "@/lib/api";
import type { Overview } from "@/lib/types";
import { Badge, Card, Meter, Notice, PageHeader, Stat } from "@/components/ui";

export default async function OverviewPage() {
  let data: Overview | null = null;
  let error: string | null = null;

  try {
    data = await api.get<Overview>("/v1/overview");
  } catch (caught) {
    error = describeError(caught);
  }

  if (error || !data) {
    return (
      <>
        <PageHeader title="Overview" />
        <Notice tone="danger">{error}</Notice>
      </>
    );
  }

  const experiment = data.experiment;

  return (
    <>
      <PageHeader
        title="Overview"
        subtitle="What is serving, how well it ranks, and whether the experiment has decided anything yet."
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Ranker AUC" value={data.ranker_auc.toFixed(3)} hint="on a held out slice" tone="good" />
        <Stat label="Retrieval loss" value={data.tower_final_loss.toFixed(3)} hint="two tower, final epoch" />
        <Stat label="Indexed items" value={data.index_size.toLocaleString()} hint={`${data.embedding_dimension}-dimensional`} />
        <Stat
          label="Rollout guard"
          value={data.guard_healthy ? "Healthy" : "Tripped"}
          hint={data.guard_healthy ? "canary within limits" : data.guard_reasons[0] ?? ""}
          tone={data.guard_healthy ? "good" : "danger"}
        />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card title="Live model" description="What the registry is currently serving.">
          <dl className="space-y-4">
            <div className="flex items-center justify-between">
              <dt className="text-sm text-[var(--color-muted)]">Version</dt>
              <dd className="font-mono text-sm">{data.live_model ?? "none"}</dd>
            </div>
            <div className="flex items-center justify-between">
              <dt className="text-sm text-[var(--color-muted)]">Stage</dt>
              <dd>{data.live_stage ? <Badge value={data.live_stage} /> : "—"}</dd>
            </div>
            <div className="flex items-center justify-between">
              <dt className="text-sm text-[var(--color-muted)]">Calibrated</dt>
              <dd className="text-sm">{data.ranker_calibrated ? "yes, Platt scaled" : "no"}</dd>
            </div>
            <div className="flex items-center justify-between">
              <dt className="text-sm text-[var(--color-muted)]">Feeds served</dt>
              <dd className="text-sm tabular-nums">{data.served.toLocaleString()}</dd>
            </div>
          </dl>
        </Card>

        <Card
          title="Sequential experiment"
          description="A mixture SPRT, so this can be read at any moment without inflating the false positive rate."
        >
          <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-3">
              <Badge value={experiment.decision} />
              <span className="text-sm text-[var(--color-muted)]">
                {experiment.samples.toLocaleString()} observations
              </span>
            </div>

            <div>
              <div className="mb-1.5 flex items-baseline justify-between">
                <span className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
                  Likelihood ratio
                </span>
                <span className="text-sm tabular-nums">
                  {experiment.likelihood_ratio.toExponential(2)} / {experiment.threshold}
                </span>
              </div>
              <Meter value={Math.min(experiment.likelihood_ratio / experiment.threshold, 1)} />
              <p className="mt-1.5 text-xs text-[var(--color-faint)]">
                Crossing the threshold stops the test. Below its reciprocal means no difference
                worth chasing.
              </p>
            </div>

            <div className="grid grid-cols-3 gap-3 text-sm">
              <div>
                <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">Control</p>
                <p className="mt-0.5 tabular-nums">{(experiment.control_rate * 100).toFixed(1)}%</p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">Treatment</p>
                <p className="mt-0.5 tabular-nums">{(experiment.treatment_rate * 100).toFixed(1)}%</p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-[var(--color-muted)]">Lift</p>
                <p className="mt-0.5 tabular-nums">{(experiment.lift * 100).toFixed(1)}%</p>
              </div>
            </div>
          </div>
        </Card>
      </div>

      <div className="mt-6">
        <Card title="Corpus" description="What the models were fitted on.">
          <div className="grid gap-4 sm:grid-cols-3">
            <Stat label="Users" value={data.users.toLocaleString()} />
            <Stat label="Items" value={data.items.toLocaleString()} />
            <Stat label="Interactions" value={data.interactions.toLocaleString()} hint="clicks in the log" />
          </div>
        </Card>
      </div>
    </>
  );
}
