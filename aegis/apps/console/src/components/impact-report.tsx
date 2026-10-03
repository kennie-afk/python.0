import { Badge, Meter, Table, rowClass } from "@/components/ui";
import type { AdverseImpactResponse } from "@/lib/types";

/** One stored analysis: the verdict, the evidence behind it, and where it sits in the audit chain. */
export function ImpactReport({ report }: { report: AdverseImpactResponse }) {
  const flagged = report.verdict === "ADVERSE_IMPACT";
  return (
    <article
      className={`rounded-md border bg-[var(--color-surface)] ${
        flagged ? "border-[#f5cdcb]" : "border-[var(--color-line)]"
      }`}
    >
      <header className="flex flex-wrap items-start justify-between gap-3 px-5 pt-4 pb-3">
        <div className="min-w-0">
          <h3 className="text-[0.875rem] font-semibold tracking-[-0.01em]">{report.label}</h3>
          <p className="mt-1 text-xs text-[var(--color-muted)]">
            {report.recorded_at ? new Date(report.recorded_at).toLocaleString() : ""}
            {report.ledger_sequence !== null && report.ledger_sequence !== undefined
              ? ` · audit entry ${report.ledger_sequence}`
              : ""}
            {report.minimum_group_size ? ` · groups under ${report.minimum_group_size} excluded` : ""}
          </p>
        </div>
        <Badge value={report.verdict} />
      </header>
      <div className="space-y-4 px-5 pb-5">
        <p className="text-[0.8125rem] leading-relaxed">{report.summary}</p>
        {report.groups.length === 0 ? (
          <p className="rounded-md bg-[var(--color-raised)] px-3 py-2.5 text-xs text-[var(--color-muted)]">
            No group was large enough to draw a conclusion from, so no rates are shown. This is recorded as
            insufficient data, not as a pass.
          </p>
        ) : (
        <Table head={["Group", "Selected", "Rate", "Ratio to best", ""]}>
          {report.groups.map((group) => (
            <tr key={group.group} className={rowClass}>
              <td className="px-3 py-2.5 font-medium">{group.group}</td>
              <td className="px-3 py-2.5 tabular-nums text-[var(--color-muted)]">
                {group.selected} / {group.total}
              </td>
              <td className="px-3 py-2.5 tabular-nums">{(group.selection_rate * 100).toFixed(1)}%</td>
              <td className="w-48 px-3 py-2.5">
                <div className="flex items-center gap-3">
                  <span className="w-9 tabular-nums">{group.impact_ratio.toFixed(2)}</span>
                  <Meter value={Math.min(group.impact_ratio, 1)} tone={group.adversely_impacted ? "danger" : "good"} />
                </div>
              </td>
              <td className="px-3 py-2.5">{group.adversely_impacted ? <Badge value="ADVERSE_IMPACT" /> : null}</td>
            </tr>
          ))}
        </Table>
        )}
        {report.p_value !== null ? (
          <p className="text-xs text-[var(--color-faint)]">
            Chi-squared p = {report.p_value.toFixed(4)}. Below 0.05 the gap is unlikely to be chance alone. The
            four-fifths line is a ratio of 0.80.
          </p>
        ) : null}
      </div>
    </article>
  );
}
