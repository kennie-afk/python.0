"use client";

import { useActionState, useState } from "react";
import { DateTimeField, EmailListField } from "@/components/controls";
import { SubmitButton } from "@/components/submit-button";
import { Field, Notice, Select, inputClass, secondaryButtonClass } from "@/components/ui";
import { startRun } from "@/lib/actions";
import { idleForm } from "@/lib/form-state";
import type { WorkflowCatalogue } from "@/lib/types";

const LABELS: Record<string, string> = {
  recipient_email: "Recipient email",
  subject: "Message subject",
  body: "Message",
  attendees: "Attendees",
  starts_at: "Interview time",
  role: "Role"
};

const SAMPLE: Record<string, string> = {
  recipient_email: "amina@example.com",
  subject: "Interview invitation",
  body: "We were impressed by your application and would like to meet you this week."
};

function ContextField({ field }: { field: string }) {
  const label = LABELS[field] ?? field.replaceAll("_", " ");
  const name = `context.${field}`;

  if (field === "starts_at") {
    return <DateTimeField label={label} name={name} />;
  }

  if (field === "attendees") {
    return (
      <EmailListField
        label={label}
        name={name}
        hint="Everyone who should receive the invitation."
      />
    );
  }

  if (field === "body") {
    return (
      <Field label={label}>
        <textarea name={name} rows={4} defaultValue={SAMPLE.body} className={inputClass} />
      </Field>
    );
  }

  if (field === "recipient_email") {
    return (
      <Field label={label}>
        <input name={name} type="email" placeholder={SAMPLE.recipient_email} className={inputClass} />
      </Field>
    );
  }

  return (
    <Field label={label}>
      <input name={name} placeholder={SAMPLE[field]} className={inputClass} />
    </Field>
  );
}

export function StartRunForm({
  catalogue,
  initial
}: {
  catalogue: WorkflowCatalogue;
  initial?: string;
}) {
  const names = Object.keys(catalogue);
  const [workflow, setWorkflow] = useState(initial && catalogue[initial] ? initial : names[0]);
  const [state, action] = useActionState(startRun, idleForm);
  const definition = catalogue[workflow];
  const required = definition?.required_context ?? [];
  const wide = new Set(["body", "attendees"]);

  return (
    <form action={action} key={workflow} className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <Select
          label="Workflow"
          hint={`${definition?.steps.length ?? 0} steps`}
          name="workflow"
          placeholder="Choose a workflow"
          options={names.map((name) => ({ value: name, label: name.replaceAll("_", " ") }))}
          value={workflow}
          onChange={(event) => setWorkflow(event.target.value)}
        />
        <Field label="Subject" hint="The candidate or employee this run is about.">
          <input name="subject_id" placeholder="candidate-1042" className={inputClass} />
        </Field>
      </div>

      {required.length > 0 ? (
        <div className="rounded-md border border-[var(--color-line)] bg-[var(--color-raised)] p-4">
          <p className="mb-4 text-xs text-[var(--color-muted)]">
            This workflow will not start without the details its steps need, so it cannot act on
            someone and then stall halfway.
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            {required.map((field) => (
              <div key={field} className={wide.has(field) ? "sm:col-span-2" : undefined}>
                <ContextField field={field} />
              </div>
            ))}
          </div>
        </div>
      ) : (
        <Notice>This workflow needs nothing up front. It can start straight away.</Notice>
      )}

      {state.error ? <Notice tone="danger">{state.error}</Notice> : null}

      <div className="flex items-center gap-3">
        <SubmitButton label="Start run" pendingLabel="Starting…" />
        <button type="reset" className={secondaryButtonClass}>
          Clear
        </button>
      </div>
    </form>
  );
}
