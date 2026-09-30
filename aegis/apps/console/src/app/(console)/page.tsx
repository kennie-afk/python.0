import Link from "next/link";
import { api, describeError } from "@/lib/api";
import { requireSession } from "@/lib/session";
import type { IntegrityView, LedgerEntryView, OverviewView, RunView } from "@/lib/types";
import { Badge, Card, EmptyState, Meter, Notice, PageHeader, Stat, Table, rowClass } from "@/components/ui";

function total(counts: Record<string, number>): number {
  return Object.values(counts).reduce((sum, value) => sum + value, 0);
}

export default async function OverviewPage() {
  const session = await requireSession();

  let overview: OverviewView | null = null;
  let waiting: RunView[] = [];
  let recent: LedgerEntryView[] = [];
  let integrity: IntegrityView | null = null;
  let error: string | null = null;

  try {
    const [counts, held, latest, verdict] = await Promise.all([
      api.get<OverviewView>("/v1/overview", session.token),
      api.get<RunView[]>("/v1/runs?needs=approval&limit=6", session.token),
      api.get<LedgerEntryView[]>("/v1/ledger/search?limit=8", session.token),
      api.get<IntegrityView>("/v1/ledger/verify", session.token)
    ]);
    overview = counts;
    waiting = held;
    recent = latest;
    integrity = verdict;
  } catch (caught) {
    error = describeError(caught);
  }

  if (error || !overview) {
    return (
      <>
        <PageHeader title="Overview" />
        <Notice tone="danger">{error ?? "The overview could not be loaded."}</Notice>
      </>
    );
  }

  const screened = total(overview.screenings);
  const advanced = overview.screenings.ADVANCE ?? 0;
  const flagged = overview.impact_reports.ADVERSE_IMPACT ?? 0;
  const bands = overview.retention_bands;
  const employees = total(bands);

  return (
    <>
      <PageHeader
        title="Overview"
        subtitle="What the agents have done, what they are waiting on you for, and whether the record of it still adds up."
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Runs" value={String(overview.runs)} hint="in this tenant" />
        <Stat
          label="Awaiting you"
          value={String(overview.awaiting_approval)}
          hint="held for human approval"
          tone={overview.awaiting_approval > 0 ? "warn" : undefined}
        />
        <Stat
          label="Failed"
          value={String(overview.failed)}
          hint="recoverable by retrying a step"
          tone={overview.failed > 0 ? "danger" : undefined}
        />
        <Stat
          label="Audit chain"
          value={integrity?.intact ? "Intact" : "Broken"}
          hint={`${integrity?.entries_checked ?? 0} entries verified`}
          tone={integrity?.intact ? "good" : "danger"}
        />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card
          title="Waiting for a decision"
          description="An agent stopped here because your policy says a person decides."
          actions={
            overview.awaiting_approval > 6 ? (
              <Link href="/runs?needs=approval" className="text-[0.75rem] font-medium text-[var(--color-accent)] hover:underline">
                All {overview.awaiting_approval}
              </Link>
            ) : undefined
          }
        >
          {waiting.length === 0 ? (
            <EmptyState message="Nothing is waiting on you." />
          ) : (
            <ul className="space-y-1">
              {waiting.map((run) => (
                <li key={run.run_id}>
                  <Link
                    href={`/runs/${run.run_id}`}
                    className="-mx-2 flex items-center justify-between gap-3 rounded-md px-2 py-2 transition-colors duration-150 hover:bg-[var(--color-raised)]"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium">{run.subject_id}</span>
                      <span className="block truncate text-xs text-[var(--color-muted)]">
                        {run.workflow.replaceAll("_", " ")} · waiting on {run.pending_approvals.join(", ").replaceAll("_", " ")}
                      </span>
                    </span>
                    <Badge value={run.status} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Human involvement" description="Decisions people made rather than agents.">
          <div className="space-y-4">
            <div className="flex items-baseline justify-between">
              <span className="text-[0.8125rem] text-[var(--color-muted)]">Decisions by people</span>
              <span className="text-[1.125rem] font-semibold tabular-nums">{overview.human_decisions}</span>
            </div>
            <div className="flex items-baseline justify-between">
              <span className="text-[0.8125rem] text-[var(--color-muted)]">Actions by agents</span>
              <span className="text-[1.125rem] font-semibold tabular-nums">
                {overview.ledger_entries - overview.human_decisions}
              </span>
            </div>
            <p className="text-xs leading-relaxed text-[var(--color-faint)]">
              Every entry is hashed onto the one before it. Changing a past decision breaks the chain, and the overview says so.
            </p>
          </div>
        </Card>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Card title="Screening" description="Applicants scored without their identity." actions={<Link href="/screening" className="text-[0.75rem] font-medium text-[var(--color-accent)] hover:underline">Open</Link>}>
          {screened === 0 ? (
            <EmptyState message="Nobody has been screened yet." />
          ) : (
            <ul className="space-y-3">
              {["ADVANCE", "REVIEW", "HOLD"].map((key) => (
                <li key={key}>
                  <div className="mb-1 flex items-center justify-between">
                    <Badge value={key} />
                    <span className="text-sm tabular-nums text-[var(--color-muted)]">{overview?.screenings[key] ?? 0}</span>
                  </div>
                  <Meter value={(overview?.screenings[key] ?? 0) / screened} tone={key === "ADVANCE" ? "good" : "accent"} />
                </li>
              ))}
              <li className="pt-1 text-xs text-[var(--color-faint)]">{advanced} of {screened} advanced</li>
            </ul>
          )}
        </Card>

        <Card title="Compliance" description="Adverse impact analyses on record." actions={<Link href="/compliance" className="text-[0.75rem] font-medium text-[var(--color-accent)] hover:underline">Open</Link>}>
          {total(overview.impact_reports) === 0 ? (
            <EmptyState message="No analyses yet." />
          ) : (
            <div className="space-y-3">
              {flagged > 0 ? (
                <Notice tone="danger">
                  {flagged} {flagged === 1 ? "analysis shows" : "analyses show"} adverse impact.
                </Notice>
              ) : (
                <Notice tone="good">No analysis shows adverse impact.</Notice>
              )}
              <ul className="space-y-1.5 text-sm">
                {Object.entries(overview.impact_reports).map(([verdict, count]) => (
                  <li key={verdict} className="flex items-center justify-between">
                    <Badge value={verdict} />
                    <span className="tabular-nums text-[var(--color-muted)]">{count}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>

        <Card title="Retention" description="Flight risk across current staff." actions={<Link href="/attrition" className="text-[0.75rem] font-medium text-[var(--color-accent)] hover:underline">Open</Link>}>
          {!overview.model_trained || employees === 0 ? (
            <EmptyState message={overview.model_trained ? "Nobody has been scored yet." : "No model trained yet."} />
          ) : (
            <ul className="space-y-3">
              {["HIGH", "MEDIUM", "LOW"].map((band) => (
                <li key={band}>
                  <div className="mb-1 flex items-center justify-between">
                    <Badge value={band} />
                    <span className="text-sm tabular-nums text-[var(--color-muted)]">{bands[band] ?? 0}</span>
                  </div>
                  <Meter value={(bands[band] ?? 0) / employees} tone={band === "HIGH" ? "danger" : band === "LOW" ? "good" : "accent"} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="mt-6">
        <Card title="Latest activity" description="The most recent entries in the audit trail." actions={<Link href="/ledger" className="text-[0.75rem] font-medium text-[var(--color-accent)] hover:underline">Full trail</Link>}>
          {recent.length === 0 ? (
            <EmptyState message="No activity yet. Start a run to see it here." />
          ) : (
            <Table head={["#", "Subject", "Step", "Outcome", "By"]}>
              {recent.map((entry) => (
                <tr key={entry.sequence} className={rowClass}>
                  <td className="px-3 py-2.5 tabular-nums text-[var(--color-faint)]">{entry.sequence}</td>
                  <td className="px-3 py-2.5">{entry.subject_id}</td>
                  <td className="px-3 py-2.5 text-[var(--color-muted)]">{entry.step.replaceAll("_", " ")}</td>
                  <td className="px-3 py-2.5">
                    <Badge value={entry.outcome} />
                  </td>
                  <td className="px-3 py-2.5 text-[var(--color-muted)]">{entry.approver ?? "agent"}</td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      </div>
    </>
  );
}
