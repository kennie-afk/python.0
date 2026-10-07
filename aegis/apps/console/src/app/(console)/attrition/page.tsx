import Link from "next/link";
import { api, describeError } from "@/lib/api";
import { requireSession } from "@/lib/session";
import { ScoreEmployee, TrainOnSample } from "@/components/attrition-panels";
import { FilterBar } from "@/components/filter-bar";
import { PAGE_SIZE, Pager, first, pageOf } from "@/components/pager";
import { Badge, Card, EmptyState, Meter, Notice, PageHeader, Stat, Table, rowClass, secondaryButtonClass } from "@/components/ui";
import { startRetentionConversation } from "@/lib/actions";
import { ModelVersions } from "@/components/model-versions";
import type { ModelStatusView, ModelVersionView, StoredScoreView } from "@/lib/types";

export default async function AttritionPage({
  searchParams
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const session = await requireSession();
  const raw = await searchParams;
  const page = pageOf(raw.page);
  const band = first(raw.band);
  const flash = first(raw.error);

  let status: ModelStatusView | null = null;
  let roster: StoredScoreView[] = [];
  let rosterTotal = 0;
  let versions: ModelVersionView[] = [];
  let error: string | null = null;
  try {
    const query = new URLSearchParams({
      limit: String(PAGE_SIZE),
      offset: String((page - 1) * PAGE_SIZE)
    });
    if (band) {
      query.set("band", band);
    }
    const [model, scores, models] = await Promise.all([
      api.get<ModelStatusView>("/v1/attrition/model", session.token),
      api.page<StoredScoreView>(`/v1/attrition/scores?${query}`, session.token),
      api.get<ModelVersionView[]>("/v1/attrition/models", session.token)
    ]);
    versions = models;
    status = model;
    roster = scores.items;
    rosterTotal = scores.total;
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
      {flash ? <div className="mb-4"><Notice tone="danger">{flash}</Notice></div> : null}

      {!error && status && !status.trained ? (
        <Card title="No model yet">
          <TrainOnSample />
        </Card>
      ) : null}
      {!error && status && !status.trained && versions.length > 0 ? <div className="mt-6"><ModelVersions versions={versions} /></div> : null}

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

          {versions.length > 0 ? <ModelVersions versions={versions} /> : null}

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

          <section aria-labelledby="roster">
            <h2 id="roster" className="mb-3 text-[0.875rem] font-semibold">
              Who is at risk
            </h2>
            <FilterBar
              action="/attrition"
              fields={[
                {
                  name: "band",
                  label: "Risk band",
                  value: band,
                  kind: "select",
                  placeholder: "Any band",
                  options: [
                    { value: "HIGH", label: "High" },
                    { value: "MEDIUM", label: "Medium" },
                    { value: "LOW", label: "Low" }
                  ]
                }
              ]}
            />
            {rosterTotal === 0 ? (
              <div className="rounded-md border border-[var(--color-line)] bg-[var(--color-surface)]">
                <EmptyState
                  message={band ? "Nobody is in that band." : "Nobody has been scored yet."}
                  detail="Score an employee below and they will appear here, highest risk first."
                />
              </div>
            ) : (
              <>
                <Card>
                  <Table head={["Employee", "Risk", "Band", "Main drivers", "", ""]}>
                    {roster.map((row) => (
                      <tr key={row.subject_key} className={rowClass}>
                        <td className="px-3 py-2.5 font-mono text-xs">{row.subject_key}</td>
                        <td className="w-44 px-3 py-2.5">
                          <div className="flex items-center gap-3">
                            <span className="w-10 tabular-nums">{(row.probability * 100).toFixed(0)}%</span>
                            <Meter value={row.probability} tone={row.band === "HIGH" ? "danger" : row.band === "LOW" ? "good" : "accent"} />
                          </div>
                        </td>
                        <td className="px-3 py-2.5">
                          <Badge value={row.band} />
                        </td>
                        <td className="max-w-[18rem] px-3 py-2.5 text-xs text-[var(--color-muted)]">
                          {row.drivers
                            .slice(0, 3)
                            .map((driver) => driver.feature.replaceAll("_", " "))
                            .join(", ") || "—"}
                        </td>
                        <td className="px-3 py-2.5 text-right">
                          <Link href={`/runs?q=${encodeURIComponent(row.subject_key)}`} className="text-xs font-medium text-[var(--color-accent)] hover:underline">
                            Runs
                          </Link>
                        </td>
                        <td className="px-3 py-2.5 text-right">
                          {row.needs_intervention ? (
                            <form action={startRetentionConversation}>
                              <input type="hidden" name="subject_key" value={row.subject_key} />
                              <button type="submit" className={secondaryButtonClass}>
                                Start conversation
                              </button>
                            </form>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </Table>
                </Card>
                <Pager base="/attrition" params={{ band }} page={page} total={rosterTotal} noun="employees" />
              </>
            )}
          </section>

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
