import { api, describeError } from "@/lib/api";
import type { DriftRow, LiveDrift } from "@/lib/types";
import { Badge, Card, Meter, Notice, PageHeader, Table } from "@/components/ui";
import Link from "next/link";

const SHIFTS = [0, 0.25, 0.5, 1, 2];

export default async function DriftPage({
  searchParams
}: {
  searchParams: Promise<{ shift?: string }>;
}) {
  const { shift } = await searchParams;
  const applied = Number(shift ?? 0);

  let rows: DriftRow[] = [];
  let live: LiveDrift | null = null;
  let error: string | null = null;

  try {
    live = await api.get<LiveDrift>("/v1/drift/live");
    rows = await api.get<DriftRow[]>(`/v1/drift?shift=${applied}`);
  } catch (caught) {
    error = describeError(caught);
  }

  const alerts = rows.filter((row) => row.severity === "alert").length;

  return (
    <>
      <PageHeader
        title="Drift"
        subtitle="Population stability index and a two sample KS test per feature. Move the slider to inject a shift and watch it get caught."
      />

      {error ? <Notice tone="danger">{error}</Notice> : null}

      {live ? (
        <div className="mb-6">
          <Card
            title="Live serving traffic"
            description={`The feature rows the ranker actually received while serving, against its training reference. ${live.rows.toLocaleString()} rows buffered.`}
          >
            {!live.ready ? (
              <Notice tone="info">
                {live.rows} of the {live.minimum_rows} rows needed so far. Serve some feeds and this fills in;
                nothing is shown until there is real traffic to compare.
              </Notice>
            ) : (
              <>
                <Table head={["Feature", "PSI", "KS statistic", "Verdict"]}>
                  {live.reports.map((row) => (
                    <tr key={row.feature} className="border-b border-[var(--color-line)] last:border-0">
                      <td className="px-3 py-2.5 text-sm">{row.feature.replaceAll("_", " ")}</td>
                      <td className="px-3 py-2.5 text-xs tabular-nums">{row.psi.toFixed(3)}</td>
                      <td className="px-3 py-2.5 text-xs tabular-nums text-[var(--color-muted)]">{row.ks_statistic.toFixed(4)}</td>
                      <td className="px-3 py-2.5"><Badge value={row.severity} /></td>
                    </tr>
                  ))}
                </Table>
                {live.trained_but_not_served.length > 0 ? (
                  <div className="mt-3">
                    <Notice tone="warn">
                      Trained on but never supplied at serving: {live.trained_but_not_served.join(", ")}. The
                      ranker reads these as zero when serving, which is a train and serve mismatch, not drift.
                    </Notice>
                  </div>
                ) : null}
              </>
            )}
          </Card>
        </div>
      ) : null}

      <h2 className="mb-2 text-sm font-semibold">Injected shift (demonstration)</h2>
      <div className="mb-6 flex flex-wrap items-center gap-2">
        <span className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
          Injected shift
        </span>
        {SHIFTS.map((value) => (
          <Link
            key={value}
            href={`/drift?shift=${value}`}
            className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
              applied === value
                ? "bg-[var(--color-accent)] font-medium text-white"
                : "border border-[var(--color-line)] bg-[var(--color-surface)] text-[var(--color-muted)] hover:bg-[var(--color-raised)]"
            }`}
          >
            {value === 0 ? "none" : `${value}σ`}
          </Link>
        ))}
      </div>

      {applied === 0 ? (
        <div className="mb-6">
          <Notice tone="good">
            With no shift applied every feature should read stable. Anything else would be a false
            alarm, and a monitor that cries wolf gets switched off.
          </Notice>
        </div>
      ) : (
        <div className="mb-6">
          <Notice tone={alerts > 0 ? "warn" : "info"}>
            {alerts} of {rows.length} features are alerting at a {applied}σ shift.
          </Notice>
        </div>
      )}

      <Card title="Per feature">
        <Table head={["Feature", "PSI", "KS statistic", "p value", "Verdict"]}>
          {rows.map((row) => (
            <tr
              key={row.feature}
              className="border-b border-[var(--color-line)] transition-colors last:border-0 hover:bg-[var(--color-raised)]"
            >
              <td className="px-3 py-2.5 text-sm">{row.feature.replaceAll("_", " ")}</td>
              <td className="px-3 py-2.5 w-48">
                <div className="flex items-center gap-2">
                  <span className="w-14 shrink-0 text-xs tabular-nums">{row.psi.toFixed(3)}</span>
                  <Meter
                    value={Math.min(row.psi / 0.5, 1)}
                    tone={row.severity === "alert" ? "danger" : "accent"}
                  />
                </div>
              </td>
              <td className="px-3 py-2.5 text-xs tabular-nums text-[var(--color-muted)]">
                {row.ks_statistic.toFixed(4)}
              </td>
              <td className="px-3 py-2.5 text-xs tabular-nums text-[var(--color-muted)]">
                {row.p_value < 1e-6 ? "< 1e-6" : row.p_value.toFixed(6)}
              </td>
              <td className="px-3 py-2.5">
                <Badge value={row.severity} />
              </td>
            </tr>
          ))}
        </Table>
      </Card>

      <p className="mt-4 text-xs leading-relaxed text-[var(--color-faint)]">
        A PSI above 0.1 is worth a look and above 0.25 is worth acting on. Low cardinality
        features are compared by category rather than by quantile bin, because quantile edges
        collapse on a binary feature and read microscopic float noise as catastrophic drift.
      </p>
    </>
  );
}
