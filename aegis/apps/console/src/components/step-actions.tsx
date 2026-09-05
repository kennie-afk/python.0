"use client";

import { useActionState, useState } from "react";
import { DateTimeField, EmailListField } from "@/components/controls";
import { SubmitButton } from "@/components/submit-button";
import { Notice, inputClass, secondaryButtonClass } from "@/components/ui";
import { approveStep, rejectStep, retryStep } from "@/lib/actions";
import { idleForm } from "@/lib/form-state";
import type { StepView } from "@/lib/types";

export function ApprovalActions({
  runId,
  step,
  approver
}: {
  runId: string;
  step: StepView;
  approver: string;
}) {
  const [approveState, approve] = useActionState(approveStep, idleForm);
  const [rejectState, reject] = useActionState(rejectStep, idleForm);
  const [rejecting, setRejecting] = useState(false);

  return (
    <div className="mt-3 space-y-3">
      {step.irreversible ? (
        <p className="text-xs text-[var(--color-danger)]">
          This action cannot be undone once taken. Your signature is recorded against it.
        </p>
      ) : null}

      {rejecting ? (
        <form action={reject} className="space-y-2">
          <input type="hidden" name="run_id" value={runId} />
          <input type="hidden" name="step_key" value={step.key} />
          <input type="hidden" name="approver" value={approver} />
          <input
            name="reason"
            placeholder="Why are you rejecting this?"
            className={inputClass}
          />
          {rejectState.error ? <Notice tone="danger">{rejectState.error}</Notice> : null}
          <div className="flex gap-2">
            <SubmitButton label="Confirm rejection" pendingLabel="Recording…" variant="danger" />
            <button
              type="button"
              onClick={() => setRejecting(false)}
              className={secondaryButtonClass}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap gap-2">
          <form action={approve}>
            <input type="hidden" name="run_id" value={runId} />
            <input type="hidden" name="step_key" value={step.key} />
            <input type="hidden" name="approver" value={approver} />
            <SubmitButton label="Approve" pendingLabel="Approving…" />
          </form>
          <button type="button" onClick={() => setRejecting(true)} className={secondaryButtonClass}>
            Reject
          </button>
        </div>
      )}

      {approveState.error ? <Notice tone="danger">{approveState.error}</Notice> : null}
    </div>
  );
}

const AMENDABLE: Record<string, string> = {
  recipient_email: "Recipient email",
  subject: "Message subject",
  body: "Message",
  attendees: "Attendees",
  starts_at: "Interview time"
};

export function RetryAction({
  runId,
  step,
  actor,
  amendable
}: {
  runId: string;
  step: StepView;
  actor: string;
  amendable: string[];
}) {
  const [state, action] = useActionState(retryStep, idleForm);
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <div className="mt-3">
        <button type="button" onClick={() => setOpen(true)} className={secondaryButtonClass}>
          Fix and retry
        </button>
        <p className="mt-1.5 text-xs text-[var(--color-faint)]">
          Attempt {step.attempts} of 3.
        </p>
      </div>
    );
  }

  return (
    <form action={action} className="mt-3 space-y-3 rounded-md border border-[var(--color-line)] bg-[var(--color-raised)] p-4">
      <input type="hidden" name="run_id" value={runId} />
      <input type="hidden" name="step_key" value={step.key} />
      <input type="hidden" name="actor" value={actor} />
      <p className="text-xs text-[var(--color-muted)]">
        Change only what this step needs. A retry cannot introduce anything else, and it cannot
        undo a decision someone made.
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        {amendable.map((key) => {
          const label = AMENDABLE[key] ?? key.replaceAll("_", " ");
          const name = `amend.${key}`;
          if (key === "starts_at") {
            return <DateTimeField key={key} label={label} name={name} />;
          }
          if (key === "attendees") {
            return (
              <EmailListField
                key={key}
                label={label}
                name={name}
                hint="Leave empty to keep the current list."
              />
            );
          }
          return (
            <label key={key} className="block">
              <span className="block text-xs font-medium uppercase tracking-wide text-[var(--color-muted)]">
                {label}
              </span>
              <input name={name} className={inputClass} />
            </label>
          );
        })}
      </div>
      {state.error ? <Notice tone="danger">{state.error}</Notice> : null}
      <div className="flex gap-2">
        <SubmitButton label="Retry step" pendingLabel="Retrying…" />
        <button type="button" onClick={() => setOpen(false)} className={secondaryButtonClass}>
          Cancel
        </button>
      </div>
    </form>
  );
}
