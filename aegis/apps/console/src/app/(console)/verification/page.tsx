import { api, describeError } from "@/lib/api";
import { requireSession } from "@/lib/session";
import { DeterminismPanel, DriftPanel } from "@/components/analytics-panels";
import { PAGE_SIZE, Pager, first, pageOf } from "@/components/pager";
import { Badge, Card, EmptyState, Notice, PageHeader, Table, rowClass } from "@/components/ui";
import type { VerificationReport } from "@/lib/types";

export default async function VerificationPage({
  searchParams
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const session = await requireSession();
  const raw = await searchParams;
  const page = pageOf(raw.page);
  const kind = first(raw.kind);

  let reports: VerificationReport[] = [];
  let total = 0;
  let error: string | null = null;
  try {
    const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String((page - 1) * PAGE_SIZE) });
    if (kind) query.set("kind", kind);
    const result = await api.page<VerificationReport>(`/v1/verification/reports?${query}`, session.token);
    reports = result.items;
    total = result.total;
  } catch (caught) {
    error = describeError(caught);
  }

  return (
    <>
      <PageHeader
        title="Verification"
        subtitle="Is the automation behaving the same way twice, and has the population it sees moved? Each check is recorded in the audit trail."
      />
      {error ? <Notice tone="danger">{error}</Notice> : null}
      <div className="space-y-6">
        <DeterminismPanel />
        <DriftPanel />
        <section aria-labelledby="past">
          <h2 id="past" className="mb-3 text-[0.875rem] font-semibold">Past checks</h2>
          {total === 0 ? (
            <div className="rounded-md border border-[var(--color-line)] bg-[var(--color-surface)]">
              <EmptyState message="No verification has been run." detail="Run one above and it will be listed here." />
            </div>
          ) : (
            <>
              <Card>
                <Table head={["Check", "What", "Verdict", "By", "Audit entry", "When"]}>
                  {reports.map((report) => (
                    <tr key={report.id} className={rowClass}>
                      <td className="px-3 py-2.5 capitalize">{report.kind}</td>
                      <td className="px-3 py-2.5 text-[var(--color-muted)]">{report.label}</td>
                      <td className="px-3 py-2.5"><Badge value={report.verdict} /></td>
                      <td className="px-3 py-2.5 text-xs text-[var(--color-muted)]">{report.created_by}</td>
                      <td className="px-3 py-2.5 tabular-nums text-[var(--color-faint)]">{report.ledger_sequence ?? "—"}</td>
                      <td className="whitespace-nowrap px-3 py-2.5 text-xs text-[var(--color-faint)]">{new Date(report.created_at).toLocaleString()}</td>
                    </tr>
                  ))}
                </Table>
              </Card>
              <Pager base="/verification" params={{ kind }} page={page} total={total} noun="checks" />
            </>
          )}
        </section>
      </div>
    </>
  );
}
