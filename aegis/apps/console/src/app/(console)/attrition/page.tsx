import { api, describeError } from "@/lib/api";
import { requireSession } from "@/lib/session";
import { ScoreEmployee, TrainOnSample } from "@/components/attrition-panels";
import { Card, EmptyState, Meter, Notice, PageHeader, Stat } from "@/components/ui";
import type { ModelStatusView } from "@/lib/types";

export default async function AttritionPage() {
  const session = await requireSession();

  let status: ModelStatusView | null = null;
  let error: string | null = null;
  try {
    status = await api.get<ModelStatusView>("/v1/attrition/model", session.token);
  } catch (caught) {
    error = describeError(caught);
  }

  const importance = Object.entries(status?.feature_importance ?? {}).sort(
    (left, right) => right[1] - left[1]
  );
  const strongest = importance[0]?.[1] ?? 1;

  return (
    <>
      <PageHeader
        title="Retention"
        subtitle="A model trained on your own leavers, not a borrowed one. It stays inside your tenant and is never shared."
      />

      {error ? <Notice tone="danger">{error}</Notice> : null}

      {!error && status && !status.trained ? (
        <Card title="No model yet">
          <TrainOnSample />
        </Card>
      ) : null}

      {!error && status?.trained ? (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3">
            <Stat label="Trained on" value={String(status.rows ?? 0)} hint="historical records" />
            <Stat
              label="Leavers"
              value={String(status.positives ?? 0)}
              hint={`${(((status.positives ?? 0) / (status.rows || 1)) * 100).toFixed(0)}% of the cohort`}
            />
            <Stat label="Algorithm" value={(status.algorithm ?? "—").replaceAll("_", " ")} />
          </div>

          <Card
            title="What drives the prediction"
            description="The signals this model leans on hardest, learned from your data."
          >
            {importance.length === 0 ? (
              <EmptyState message="No feature importance was recorded." />
            ) : (
              <ul className="space-y-4">
                {importance.slice(0, 8).map(([feature, weight]) => (
                  <li key={feature}>
                    <div className="mb-1.5 flex items-baseline justify-between gap-4">
                      <span className="text-sm">{feature.replaceAll("_", " ")}</span>
                      <span className="text-sm tabular-nums text-[var(--color-muted)]">
                        {weight.toFixed(3)}
                      </span>
                    </div>
                    <Meter value={weight / strongest} />
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <ScoreEmployee />

          <p className="text-xs leading-relaxed text-[var(--color-faint)]">
            Trained {status.trained_at ? new Date(status.trained_at).toLocaleString() : "recently"}.
            The model and the data behind it stay inside this tenant.
          </p>
        </div>
      ) : null}
    </>
  );
}
