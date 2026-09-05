"use client";

import { useActionState, useState } from "react";
import { SubmitButton } from "@/components/submit-button";
import { Badge, Card, Notice, Select, Table, inputClass, rowClass } from "@/components/ui";
import { checkAdverseImpact } from "@/lib/actions";
import type { ImpactState } from "@/lib/actions";

const initial: ImpactState = { error: null, message: null, result: null };

const COMPARISONS: Record<string, { label: string; groups: string[] }> = {
  gender: { label: "Gender", groups: ["Women", "Men", "Non-binary"] },
  ethnicity: {
    label: "Ethnicity",
    groups: ["Group A", "Group B", "Group C", "Group D"]
  },
  age: { label: "Age band", groups: ["Under 40", "40 and over"] },
  disability: { label: "Disability status", groups: ["Disabled", "Not disabled"] },
  custom: { label: "Something else", groups: ["", "", "", ""] }
};

const STAGES = [
  { value: "application screening", label: "Application screening" },
  { value: "interview", label: "Interview" },
  { value: "offer", label: "Offer" },
  { value: "promotion", label: "Promotion" }
];

const MINIMUMS = [
  { value: "10", label: "10 people" },
  { value: "30", label: "30 people (recommended)" },
  { value: "50", label: "50 people" },
  { value: "100", label: "100 people" }
];

export function ImpactForm() {
  const [state, action] = useActionState(checkAdverseImpact, initial);
  const [comparison, setComparison] = useState("gender");
  const groups = COMPARISONS[comparison]?.groups ?? [];

  return (
    <div className="space-y-6">
      <Card
        title="Adverse impact"
        description="The four-fifths rule: if a group is selected at less than 80 percent of the strongest group's rate, that is a flag."
      >
        <form action={action} key={comparison} className="space-y-5">
          <div className="grid gap-4 sm:grid-cols-3">
            <Select
              label="Compare by"
              placeholder="Choose a characteristic"
              options={Object.entries(COMPARISONS).map(([value, entry]) => ({
                value,
                label: entry.label
              }))}
              value={comparison}
              onChange={(event) => setComparison(event.target.value)}
            />
            <Select
              label="Stage"
              name="stage"
              placeholder="Which decision"
              options={STAGES}
              defaultValue="application screening"
            />
            <Select
              label="Minimum group size"
              name="minimum_group_size"
              placeholder="30 people (recommended)"
              options={MINIMUMS}
              defaultValue="30"
            />
          </div>

          <Table head={["Group", "Selected", "Considered"]}>
            {groups.map((group, index) => (
              <tr key={index} className="border-b border-[var(--color-line)] last:border-0">
                <td className="px-3 py-2">
                  <input
                    name={`group.${index}.name`}
                    defaultValue={group}
                    placeholder={index < 2 ? "Name this group" : "Optional"}
                    className={`${inputClass} mt-0`}
                  />
                </td>
                <td className="px-3 py-2">
                  <input
                    name={`group.${index}.selected`}
                    type="number"
                    min="0"
                    placeholder="0"
                    className={`${inputClass} mt-0`}
                  />
                </td>
                <td className="px-3 py-2">
                  <input
                    name={`group.${index}.total`}
                    type="number"
                    min="1"
                    placeholder="0"
                    className={`${inputClass} mt-0`}
                  />
                </td>
              </tr>
            ))}
          </Table>

          <p className="text-xs text-[var(--color-faint)]">
            Enter how many people in each group were put forward and how many were considered.
            Groups below the minimum size are reported but not used to draw a conclusion.
          </p>

          {state.error ? <Notice tone="danger">{state.error}</Notice> : null}
          <SubmitButton label="Check for adverse impact" pendingLabel="Checking…" />
        </form>
      </Card>

      {state.result ? (
        <Card title="Finding">
          <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-3">
              <Badge value={state.result.verdict} />
              <span className="text-sm text-[var(--color-muted)]">
                measured against {state.result.reference_group}
              </span>
            </div>
            <p className="text-sm">{state.result.summary}</p>
            <Table head={["Group", "Selected", "Rate", "Ratio", ""]}>
              {state.result.groups.map((group) => (
                <tr key={group.group} className={rowClass}>
                  <td className="px-3 py-2.5 font-medium">{group.group}</td>
                  <td className="px-3 py-2.5 tabular-nums text-[var(--color-muted)]">
                    {group.selected} / {group.total}
                  </td>
                  <td className="px-3 py-2.5 tabular-nums">
                    {(group.selection_rate * 100).toFixed(1)}%
                  </td>
                  <td className="px-3 py-2.5 tabular-nums">{group.impact_ratio.toFixed(2)}</td>
                  <td className="px-3 py-2.5">
                    {group.adversely_impacted ? <Badge value="ADVERSE_IMPACT" /> : null}
                  </td>
                </tr>
              ))}
            </Table>
            {state.result.p_value !== null ? (
              <p className="text-xs text-[var(--color-faint)]">
                Fisher exact p = {state.result.p_value.toFixed(4)}. A small p means the gap is
                unlikely to be chance alone.
              </p>
            ) : null}
          </div>
        </Card>
      ) : null}
    </div>
  );
}
