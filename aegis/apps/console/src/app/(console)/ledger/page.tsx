import Link from "next/link";
import { api, describeError } from "@/lib/api";
import { requireSession } from "@/lib/session";
import { FilterBar } from "@/components/filter-bar";
import { PAGE_SIZE, Pager, first, pageOf } from "@/components/pager";
import { Badge, Card, EmptyState, Notice, PageHeader, Table, rowClass, secondaryButtonClass } from "@/components/ui";
import type { IntegrityView, LedgerEntryView } from "@/lib/types";
import { humanizeReason } from "@/lib/humanize";

export default async function LedgerPage({
  searchParams
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const session = await requireSession();
  const raw = await searchParams;
  const page = pageOf(raw.page);
  const filters = {
    q: first(raw.q),
    outcome: first(raw.outcome),
    actor: first(raw.actor),
    workflow: first(raw.workflow)
  };

  const query = new URLSearchParams({
    limit: String(PAGE_SIZE),
    offset: String((page - 1) * PAGE_SIZE)
  });
  for (const [key, value] of Object.entries(filters)) {
    if (value) {
      query.set(key, value);
    }
  }

  let entries: LedgerEntryView[] = [];
  let total = 0;
  let integrity: IntegrityView | null = null;
  let error: string | null = null;
  const checkedAt = new Date();

  try {
    const [result, verdict] = await Promise.all([
      api.page<LedgerEntryView>(`/v1/ledger/search?${query}`, session.token),
      api.get<IntegrityView>("/v1/ledger/verify", session.token)
    ]);
    entries = result.items;
    total = result.total;
    integrity = verdict;
  } catch (caught) {
    error = describeError(caught);
  }

  return (
    <>
      <PageHeader
        title="Audit trail"
        subtitle="Every decision an agent or a person made, hashed onto the one before it. Editing history breaks the chain and this page will say so."
        actions={
          <>
            <Link href="/ledger" className={secondaryButtonClass} prefetch={false}>
              Verify again
            </Link>
            <a href="/ledger/export" className={secondaryButtonClass} download>
              Export CSV
            </a>
          </>
        }
      />

      {error ? <Notice tone="danger">{error}</Notice> : null}

      {integrity ? (
        <div className="mb-4">
          <Notice tone={integrity.intact ? "good" : "danger"}>
            {integrity.intact
              ? `Chain intact across ${integrity.entries_checked} entries. Every hash was recomputed at ${checkedAt.toLocaleTimeString()}.`
              : `Chain broken at entry ${integrity.broken_at}. ${integrity.reason ?? ""}`}
          </Notice>
        </div>
      ) : null}

      {!error ? (
        <FilterBar
          action="/ledger"
          fields={[
            { name: "q", label: "Search", value: filters.q, kind: "search", placeholder: "Subject, step or approver" },
            {
              name: "outcome",
              label: "Outcome",
              value: filters.outcome,
              kind: "select",
              placeholder: "Any outcome",
              options: [
                "COMPLETED",
                "AWAITING_APPROVAL",
                "AWAITING_EXTERNAL",
                "REJECTED",
                "FAILED",
                "DENIED",
                "RETRIED",
                "FLAGGED",
                "PASSED",
                "INSUFFICIENT_DATA"
              ].map((value) => ({ value, label: value.replaceAll("_", " ").toLowerCase() }))
            },
            {
              name: "actor",
              label: "Who acted",
              value: filters.actor,
              kind: "select",
              placeholder: "Anyone",
              options: [
                { value: "people", label: "People only" },
                { value: "agents", label: "Agents only" }
              ]
            },
            {
              name: "workflow",
              label: "Workflow",
              value: filters.workflow,
              kind: "select",
              placeholder: "All workflows",
              options: ["talent_acquisition", "onboarding", "retention_intervention", "offboarding", "compliance"].map(
                (value) => ({ value, label: value.replaceAll("_", " ") })
              )
            }
          ]}
        />
      ) : null}

      {!error && total === 0 ? (
        <EmptyState message={Object.values(filters).some(Boolean) ? "Nothing matches those filters." : "Nothing has been recorded yet. Entries appear as soon as a run takes its first step."} />
      ) : null}

      {entries.length > 0 ? (
        <>
          <Card>
            <Table head={["#", "Subject", "Workflow", "Step", "Outcome", "By", "Why", "When"]}>
              {entries.map((entry) => (
                <tr key={entry.sequence} className={rowClass}>
                  <td className="px-3 py-2.5 tabular-nums text-[var(--color-faint)]">{entry.sequence}</td>
                  <td className="px-3 py-2.5">{entry.subject_id}</td>
                  <td className="px-3 py-2.5 text-[var(--color-muted)]">{entry.workflow.replaceAll("_", " ")}</td>
                  <td className="px-3 py-2.5 text-[var(--color-muted)]">{entry.step.replaceAll("_", " ")}</td>
                  <td className="px-3 py-2.5">
                    <Badge value={entry.outcome} />
                  </td>
                  <td className="px-3 py-2.5 text-[var(--color-muted)]">{entry.approver ?? "agent"}</td>
                  <td className="max-w-[22rem] px-3 py-2.5 text-xs text-[var(--color-faint)]" title={entry.reasons.map(humanizeReason).join("; ")}>
                    <span className="line-clamp-2">{entry.reasons.map(humanizeReason).join("; ") || "—"}</span>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-xs text-[var(--color-faint)]">
                    {new Date(entry.recorded_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </Table>
          </Card>
          <Pager base="/ledger" params={filters} page={page} total={total} noun="entries" />
        </>
      ) : null}
    </>
  );
}
