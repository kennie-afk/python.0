import { ImpactForm } from "@/components/impact-form";
import { ImpactReport } from "@/components/impact-report";
import { FilterBar } from "@/components/filter-bar";
import { Pager, first, pageOf } from "@/components/pager";
import { EmptyState, Notice, PageHeader } from "@/components/ui";
import { api, describeError } from "@/lib/api";
import { requireSession } from "@/lib/session";
import type { AdverseImpactResponse, OverviewView } from "@/lib/types";

const SIZE = 6;

export default async function CompliancePage({
  searchParams
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const session = await requireSession();
  const raw = await searchParams;
  const page = pageOf(raw.page);
  const verdict = first(raw.verdict);

  const query = new URLSearchParams({ limit: String(SIZE), offset: String((page - 1) * SIZE) });
  if (verdict) {
    query.set("verdict", verdict);
  }

  let reports: AdverseImpactResponse[] = [];
  let total = 0;
  let flagged = 0;
  let error: string | null = null;
  try {
    flagged = (await api.get<OverviewView>("/v1/overview", session.token)).impact_reports.ADVERSE_IMPACT ?? 0;
    const result = await api.page<AdverseImpactResponse>(`/v1/bias/reports?${query}`, session.token);
    reports = result.items;
    total = result.total;
  } catch (caught) {
    error = describeError(caught);
  }

  return (
    <>
      <PageHeader
        title="Compliance"
        subtitle="Check a hiring process for adverse impact before a regulator or a claimant does it for you. Every analysis is kept, and its verdict is written into the audit trail."
      />

      <section aria-labelledby="findings" className="mb-10">
        <h2 id="findings" className="mb-3 text-[0.875rem] font-semibold">
          Findings on record
        </h2>
        {error ? <Notice tone="danger">{error}</Notice> : null}
        {!error && flagged > 0 && verdict !== "ADVERSE_IMPACT" ? (
          <div className="mb-4">
            <Notice tone="danger">
              {flagged} {flagged === 1 ? "analysis shows" : "analyses show"} adverse impact.{" "}
              <a href="/compliance?verdict=ADVERSE_IMPACT" className="font-medium underline">
                Show {flagged === 1 ? "it" : "them"}
              </a>
            </Notice>
          </div>
        ) : null}
        {!error ? (
          <FilterBar
            action="/compliance"
            fields={[
              {
                name: "verdict",
                label: "Verdict",
                value: verdict,
                kind: "select",
                placeholder: "Any verdict",
                options: [
                  { value: "ADVERSE_IMPACT", label: "Adverse impact" },
                  { value: "NO_ADVERSE_IMPACT", label: "No adverse impact" },
                  { value: "INSUFFICIENT_DATA", label: "Insufficient data" }
                ]
              }
            ]}
          />
        ) : null}
        {!error && reports.length === 0 ? (
          <div className="rounded-md border border-[var(--color-line)] bg-[var(--color-surface)]">
            <EmptyState message="No analyses on record yet." detail="Run one below and it will be kept here." />
          </div>
        ) : null}
        <div className="space-y-4">
          {reports.map((report) => (
            <ImpactReport key={report.report_id} report={report} />
          ))}
        </div>
        {!error && total > 0 ? (
          <Pager base="/compliance" params={{ verdict }} page={page} total={total} size={SIZE} noun="analyses" />
        ) : null}
      </section>

      <section aria-labelledby="new-analysis">
        <h2 id="new-analysis" className="mb-3 text-[0.875rem] font-semibold">
          Run a new analysis
        </h2>
        <ImpactForm />
      </section>
    </>
  );
}
