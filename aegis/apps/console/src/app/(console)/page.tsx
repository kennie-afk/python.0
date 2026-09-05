import Link from "next/link";
import { api, describeError } from "@/lib/api";
import { requireSession } from "@/lib/session";
import type { IntegrityView, LedgerEntryView, RunView } from "@/lib/types";
import { Badge, Card, EmptyState, Notice, PageHeader, Stat, Table, rowClass } from "@/components/ui";

export default async function OverviewPage() {
  const session = await requireSession();

  let runs: RunView[] = [];
  let entries: LedgerEntryView[] = [];
  let integrity: IntegrityView | null = null;
  let error: string | null = null;

  try {
    [runs, entries, integrity] = await Promise.all([
      api.get<RunView[]>("/v1/runs", session.token),
      api.get<LedgerEntryView[]>("/v1/ledger", session.token),
      api.get<IntegrityView>("/v1/ledger/verify", session.token)
    ]);
  } catch (caught) {
    error = describeError(caught);
  }

  if (error) {
    return (
      <>
        <PageHeader title="Overview" />
        <Notice tone="danger">{error}</Notice>
      </>
    );
  }

  const awaiting = runs.filter((run) => run.pending_approvals.length > 0);
  const failed = runs.filter((run) => run.status === "FAILED");
  const approvals = entries.filter((entry) => entry.approver).length;

  return (
    <>
      <PageHeader
        title="Overview"
        subtitle="What the agents have done, what they are waiting on you for, and whether the record of it still adds up."
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Runs" value={String(runs.length)} hint="in this tenant" />
        <Stat
          label="Awaiting you"
          value={String(awaiting.length)}
          hint="held for human approval"
          tone={awaiting.length > 0 ? "warn" : undefined}
        />
        <Stat
          label="Failed"
          value={String(failed.length)}
          hint="recoverable by retrying a step"
          tone={failed.length > 0 ? "danger" : undefined}
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
        >
          {awaiting.length === 0 ? (
            <EmptyState message="Nothing is waiting on you." />
          ) : (
            <ul className="space-y-3">
              {awaiting.slice(0, 6).map((run) => (
                <li key={run.run_id}>
                  <Link
                    href={`/runs/${run.run_id}`}
                    className="-mx-2 flex items-center justify-between gap-3 rounded-md px-2 py-2 transition-colors duration-150 hover:bg-[var(--color-raised)]"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium">{run.subject_id}</span>
                      <span className="block truncate text-xs text-[var(--color-muted)]">
                        {run.workflow.replaceAll("_", " ")} · waiting on{" "}
                        {run.pending_approvals.join(", ").replaceAll("_", " ")}
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
            <div>
              <div className="flex items-baseline justify-between">
                <span className="text-sm text-[var(--color-muted)]">Approvals recorded</span>
                <span className="text-2xl font-semibold tabular-nums">{approvals}</span>
              </div>
            </div>
            <div>
              <div className="flex items-baseline justify-between">
                <span className="text-sm text-[var(--color-muted)]">Agent actions recorded</span>
                <span className="text-2xl font-semibold tabular-nums">
                  {entries.length - approvals}
                </span>
              </div>
            </div>
            <p className="text-xs leading-relaxed text-[var(--color-faint)]">
              Every entry is hashed onto the one before it. Changing a past decision breaks the
              chain, and the overview says so.
            </p>
          </div>
        </Card>
      </div>

      <div className="mt-6">
        <Card title="Latest activity" description="The most recent entries in the audit trail.">
          {entries.length === 0 ? (
            <EmptyState message="No activity yet. Start a run to see it here." />
          ) : (
            <Table head={["#", "Subject", "Step", "Outcome", "By"]}>
              {entries
                .slice(-8)
                .reverse()
                .map((entry) => (
                  <tr key={entry.sequence} className={rowClass}>
                    <td className="px-3 py-2.5 tabular-nums text-[var(--color-faint)]">
                      {entry.sequence}
                    </td>
                    <td className="px-3 py-2.5">{entry.subject_id}</td>
                    <td className="px-3 py-2.5 text-[var(--color-muted)]">
                      {entry.step.replaceAll("_", " ")}
                    </td>
                    <td className="px-3 py-2.5">
                      <Badge value={entry.outcome} />
                    </td>
                    <td className="px-3 py-2.5 text-[var(--color-muted)]">
                      {entry.approver ?? "agent"}
                    </td>
                  </tr>
                ))}
            </Table>
          )}
        </Card>
      </div>
    </>
  );
}
