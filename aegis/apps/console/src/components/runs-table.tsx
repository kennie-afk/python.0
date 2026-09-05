"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Badge, Card, EmptyState, Select, Table, inputClass, rowClass } from "@/components/ui";
import type { RunView } from "@/lib/types";

function progress(run: RunView): string {
  const done = run.steps.filter((step) => ["COMPLETED", "SKIPPED"].includes(step.status)).length;
  return `${done}/${run.steps.length}`;
}

export function RunsTable({ runs }: { runs: RunView[] }) {
  const [status, setStatus] = useState("");
  const [workflow, setWorkflow] = useState("");
  const [query, setQuery] = useState("");

  const workflows = useMemo(
    () => Array.from(new Set(runs.map((run) => run.workflow))).sort(),
    [runs]
  );
  const statuses = useMemo(
    () => Array.from(new Set(runs.map((run) => run.status))).sort(),
    [runs]
  );

  const visible = runs.filter(
    (run) =>
      (!status || run.status === status) &&
      (!workflow || run.workflow === workflow) &&
      (!query || run.subject_id.toLowerCase().includes(query.trim().toLowerCase()))
  );

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="block">
          <span className="sr-only">Search by subject</span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search by subject"
            className={`${inputClass} mt-0`}
          />
        </label>
        <Select
          aria-label="Filter by workflow"
          placeholder="All workflows"
          options={workflows.map((name) => ({ value: name, label: name.replaceAll("_", " ") }))}
          value={workflow}
          onChange={(event) => setWorkflow(event.target.value)}
        />
        <Select
          aria-label="Filter by status"
          placeholder="Any status"
          options={statuses.map((name) => ({ value: name, label: name.toLowerCase() }))}
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        />
      </div>

      {visible.length === 0 ? (
        <EmptyState message="No runs match those filters." />
      ) : (
        <Card>
          <Table head={["Subject", "Workflow", "Progress", "Status", "Waiting on", ""]}>
            {visible.map((run) => (
              <tr key={run.run_id} className={`${rowClass} group`}>
                <td className="px-3 py-3 font-medium">
                  <Link
                    href={`/runs/${run.run_id}`}
                    className="transition-colors group-hover:text-[var(--color-brand)]"
                  >
                    {run.subject_id}
                  </Link>
                </td>
                <td className="px-3 py-3 text-[var(--color-muted)]">
                  {run.workflow.replaceAll("_", " ")}
                </td>
                <td className="px-3 py-3 tabular-nums text-[var(--color-muted)]">
                  {progress(run)}
                </td>
                <td className="px-3 py-3">
                  <Badge value={run.status} />
                </td>
                <td className="px-3 py-3 text-[var(--color-muted)]">
                  {run.pending_approvals.length > 0
                    ? run.pending_approvals.join(", ").replaceAll("_", " ")
                    : "—"}
                </td>
                <td className="px-3 py-3 text-right">
                  <Link
                    href={`/runs/${run.run_id}`}
                    className="text-sm font-medium text-[var(--color-brand)] opacity-0 transition-opacity hover:underline focus-visible:opacity-100 group-hover:opacity-100"
                  >
                    Open
                  </Link>
                </td>
              </tr>
            ))}
          </Table>
        </Card>
      )}
    </div>
  );
}
