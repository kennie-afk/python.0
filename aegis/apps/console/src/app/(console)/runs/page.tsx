import Link from "next/link";
import { api, describeError } from "@/lib/api";
import { requireSession } from "@/lib/session";
import { FilterBar } from "@/components/filter-bar";
import { PAGE_SIZE, Pager, first, pageOf } from "@/components/pager";
import { Badge, Card, EmptyState, Notice, PageHeader, Table, rowClass, secondaryButtonClass } from "@/components/ui";
import type { RunView, WorkflowCatalogue } from "@/lib/types";

function progress(run: RunView): string {
  const done = run.steps.filter((step) => ["COMPLETED", "SKIPPED"].includes(step.status)).length;
  return `${done}/${run.steps.length}`;
}

export default async function RunsPage({
  searchParams
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const session = await requireSession();
  const raw = await searchParams;
  const page = pageOf(raw.page);
  const filters = { q: first(raw.q), workflow: first(raw.workflow), needs: first(raw.needs) };

  const query = new URLSearchParams({
    limit: String(PAGE_SIZE),
    offset: String((page - 1) * PAGE_SIZE)
  });
  for (const [key, value] of Object.entries(filters)) {
    if (value) {
      query.set(key, value);
    }
  }

  let runs: RunView[] = [];
  let total = 0;
  let catalogue: WorkflowCatalogue = {};
  let error: string | null = null;
  try {
    const [result, workflows] = await Promise.all([
      api.page<RunView>(`/v1/runs?${query}`, session.token),
      api.get<WorkflowCatalogue>("/v1/workflows")
    ]);
    runs = result.items;
    total = result.total;
    catalogue = workflows;
  } catch (caught) {
    error = describeError(caught);
  }

  return (
    <>
      <PageHeader
        title="Runs"
        subtitle="Every workflow this tenant has started, newest first."
        actions={
          <Link href="/workflows" className={secondaryButtonClass}>
            Start a run
          </Link>
        }
      />

      {error ? <Notice tone="danger">{error}</Notice> : null}

      {!error ? (
        <FilterBar
          action="/runs"
          fields={[
            { name: "q", label: "Search by subject", value: filters.q, kind: "search", placeholder: "Search by subject" },
            {
              name: "workflow",
              label: "Workflow",
              value: filters.workflow,
              kind: "select",
              placeholder: "All workflows",
              options: Object.keys(catalogue).map((name) => ({ value: name, label: name.replaceAll("_", " ") }))
            },
            {
              name: "needs",
              label: "Needs attention",
              value: filters.needs,
              kind: "select",
              placeholder: "Any state",
              options: [
                { value: "approval", label: "Waiting for a person" },
                { value: "external", label: "Waiting on an outside system" },
                { value: "failed", label: "Failed" }
              ]
            }
          ]}
        />
      ) : null}

      {!error && total === 0 ? (
        <EmptyState
          message={Object.values(filters).some(Boolean) ? "No runs match those filters." : "No runs yet. A run is one workflow carried out for one person."}
          action={
            <Link href="/workflows" className={secondaryButtonClass}>
              Start one
            </Link>
          }
        />
      ) : null}

      {!error && runs.length > 0 ? (
        <>
          <Card>
            <Table head={["Subject", "Workflow", "Progress", "Status", "Waiting on", ""]}>
              {runs.map((run) => (
                <tr key={run.run_id} className={`${rowClass} group`}>
                  <td className="px-3 py-3 font-medium">
                    <Link href={`/runs/${run.run_id}`} className="transition-colors group-hover:text-[var(--color-accent)]">
                      {run.subject_id}
                    </Link>
                  </td>
                  <td className="px-3 py-3 text-[var(--color-muted)]">{run.workflow.replaceAll("_", " ")}</td>
                  <td className="px-3 py-3 tabular-nums text-[var(--color-muted)]">{progress(run)}</td>
                  <td className="px-3 py-3">
                    <Badge value={run.status} />
                  </td>
                  <td className="px-3 py-3 text-[var(--color-muted)]">
                    {run.pending_approvals.length > 0 ? run.pending_approvals.join(", ").replaceAll("_", " ") : "—"}
                  </td>
                  <td className="px-3 py-3 text-right">
                    <Link href={`/runs/${run.run_id}`} className="text-sm font-medium text-[var(--color-accent)] hover:underline">
                      Open
                    </Link>
                  </td>
                </tr>
              ))}
            </Table>
          </Card>
          <Pager base="/runs" params={filters} page={page} total={total} noun="runs" />
        </>
      ) : null}
    </>
  );
}
