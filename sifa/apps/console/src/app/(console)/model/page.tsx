import { api, describeError } from "@/lib/api";
import type { ModelReport } from "@/lib/types";
import { Card, Meter, Notice, PageHeader, Stat } from "@/components/ui";

export default async function ModelPage() {
  let report: ModelReport | null = null;
  let error: string | null = null;

  try {
    report = await api.get<ModelReport>("/v1/model");
  } catch (caught) {
    error = describeError(caught);
  }

  if (error || !report) {
    return (
      <>
        <PageHeader title="Ranker" />
        <Notice tone="danger">{error}</Notice>
      </>
    );
  }

  const strongest = report.features[0]?.importance ?? 1;

  return (
    <>
      <PageHeader
        title="Ranker"
        subtitle="A gradient boosted model over point-in-time correct features, calibrated so its output can be read as a probability."
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Holdout AUC" value={report.holdout_auc.toFixed(3)} hint="never seen in training" tone="good" />
        <Stat label="Training rows" value={report.rows.toLocaleString()} />
        <Stat
          label="Positives"
          value={report.positives.toLocaleString()}
          hint={`${((report.positives / report.rows) * 100).toFixed(1)}% of the set`}
        />
        <Stat
          label="Calibration"
          value={report.calibrated ? "Platt" : "raw"}
          hint={report.calibrated ? "scores are probabilities" : "scores are not probabilities"}
          tone={report.calibrated ? "good" : "warn"}
        />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card title="What the ranker leans on" description="Learned from the data, not declared.">
          <ul className="space-y-4">
            {report.features.map((feature) => (
              <li key={feature.name}>
                <div className="mb-1.5 flex items-baseline justify-between gap-4">
                  <span className="text-sm">{feature.name.replaceAll("_", " ")}</span>
                  <span className="text-sm tabular-nums text-[var(--color-muted)]">
                    {feature.importance.toFixed(3)}
                  </span>
                </div>
                <Meter value={feature.importance / strongest} />
              </li>
            ))}
          </ul>
        </Card>

        <Card title="Retrieval tower" description="The dual encoder that produces the candidate set.">
          <dl className="space-y-4">
            <div className="flex items-center justify-between">
              <dt className="text-sm text-[var(--color-muted)]">Embedding dimension</dt>
              <dd className="text-sm tabular-nums">{report.tower.dimension}</dd>
            </div>
            <div className="flex items-center justify-between">
              <dt className="text-sm text-[var(--color-muted)]">Users learned</dt>
              <dd className="text-sm tabular-nums">{report.tower.users.toLocaleString()}</dd>
            </div>
            <div className="flex items-center justify-between">
              <dt className="text-sm text-[var(--color-muted)]">Items learned</dt>
              <dd className="text-sm tabular-nums">{report.tower.items.toLocaleString()}</dd>
            </div>
            <div>
              <div className="mb-1.5 flex items-baseline justify-between">
                <span className="text-sm text-[var(--color-muted)]">Sampled softmax loss</span>
                <span className="text-sm tabular-nums">
                  {report.tower.first_loss.toFixed(3)} → {report.tower.final_loss.toFixed(3)}
                </span>
              </div>
              <Meter value={1 - report.tower.final_loss / report.tower.first_loss} />
            </div>
          </dl>
        </Card>
      </div>

      <p className="mt-4 text-xs leading-relaxed text-[var(--color-faint)]">
        Every training row was assembled with a point-in-time join, so no feature carries a value
        that did not exist when the label was written. That is the difference between a model that
        looks excellent offline and one that survives contact with production.
      </p>
    </>
  );
}
