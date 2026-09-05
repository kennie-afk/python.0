"use client";

import { useMemo, useState } from "react";
import { Badge, Card, EmptyState, Select, Table, inputClass, rowClass } from "@/components/ui";
import type { LedgerEntryView } from "@/lib/types";

export function LedgerTable({ entries }: { entries: LedgerEntryView[] }) {
  const [outcome, setOutcome] = useState("");
  const [actor, setActor] = useState("");
  const [query, setQuery] = useState("");

  const outcomes = useMemo(
    () => Array.from(new Set(entries.map((entry) => entry.outcome))).sort(),
    [entries]
  );

  const visible = entries
    .filter((entry) => !outcome || entry.outcome === outcome)
    .filter((entry) =>
      actor === "people"
        ? entry.approver !== null
        : actor === "agents"
          ? entry.approver === null
          : true
    )
    .filter((entry) => {
      const needle = query.trim().toLowerCase();
      return (
        !needle ||
        entry.subject_id.toLowerCase().includes(needle) ||
        entry.step.toLowerCase().includes(needle) ||
        (entry.approver ?? "").toLowerCase().includes(needle)
      );
    })
    .reverse();

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="block">
          <span className="sr-only">Search the trail</span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search subject, step or approver"
            className={`${inputClass} mt-0`}
          />
        </label>
        <Select
          aria-label="Filter by outcome"
          placeholder="Any outcome"
          options={outcomes.map((name) => ({
            value: name,
            label: name.replaceAll("_", " ").toLowerCase()
          }))}
          value={outcome}
          onChange={(event) => setOutcome(event.target.value)}
        />
        <Select
          aria-label="Filter by who acted"
          placeholder="Anyone"
          options={[
            { value: "people", label: "People only" },
            { value: "agents", label: "Agents only" }
          ]}
          value={actor}
          onChange={(event) => setActor(event.target.value)}
        />
      </div>

      {visible.length === 0 ? (
        <EmptyState message="Nothing matches those filters." />
      ) : (
        <Card>
          <Table head={["#", "Subject", "Workflow", "Step", "Action", "Outcome", "By", "When"]}>
            {visible.map((entry) => (
              <tr key={entry.sequence} className={rowClass}>
                <td className="px-3 py-2.5 tabular-nums text-[var(--color-faint)]">
                  {entry.sequence}
                </td>
                <td className="px-3 py-2.5">{entry.subject_id}</td>
                <td className="px-3 py-2.5 text-[var(--color-muted)]">
                  {entry.workflow.replaceAll("_", " ")}
                </td>
                <td className="px-3 py-2.5 text-[var(--color-muted)]">
                  {entry.step.replaceAll("_", " ")}
                </td>
                <td className="px-3 py-2.5 font-mono text-xs text-[var(--color-faint)]">
                  {entry.action_type}
                </td>
                <td className="px-3 py-2.5">
                  <Badge value={entry.outcome} />
                </td>
                <td className="px-3 py-2.5 text-[var(--color-muted)]">
                  {entry.approver ?? "agent"}
                </td>
                <td className="px-3 py-2.5 whitespace-nowrap text-xs text-[var(--color-faint)]">
                  {new Date(entry.recorded_at).toLocaleString()}
                </td>
              </tr>
            ))}
          </Table>
        </Card>
      )}
    </div>
  );
}
