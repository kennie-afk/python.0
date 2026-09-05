"use client";

import { useActionState } from "react";
import { SubmitButton } from "@/components/submit-button";
import { Badge, Card, Field, Meter, Notice, inputClass } from "@/components/ui";
import { scoreEmployee, trainOnSampleCohort } from "@/lib/actions";
import type { ScoringState, TrainingState } from "@/lib/actions";

const scoringIdle: ScoringState = { error: null, message: null, result: null };
const trainingIdle: TrainingState = { error: null, message: null, result: null };

export function TrainOnSample() {
  const [state, action] = useActionState(trainOnSampleCohort, trainingIdle);

  return (
    <form action={action} className="space-y-4">
      <p className="text-sm text-[var(--color-muted)]">
        No model has been trained for this tenant yet. Point the API at your own leaver history to
        train properly, or train on a synthetic cohort now to see how the page works.
      </p>
      {state.error ? <Notice tone="danger">{state.error}</Notice> : null}
      {state.message ? <Notice tone="good">{state.message}</Notice> : null}
      <SubmitButton label="Train on a sample cohort" pendingLabel="Training…" />
    </form>
  );
}

const NUMERIC: { name: string; label: string; step: string; hint?: string; sample: string }[] = [
  { name: "tenure_years", label: "Tenure (years)", step: "0.1", sample: "1.4" },
  { name: "months_since_promotion", label: "Months since promotion", step: "1", sample: "31" },
  { name: "salary", label: "Salary", step: "100", sample: "56000" },
  { name: "band_midpoint", label: "Band midpoint", step: "100", sample: "80000" },
  { name: "peer_median_salary", label: "Peer median salary", step: "100", sample: "82000" },
  { name: "manager_changes_24m", label: "Manager changes (24m)", step: "1", sample: "3" },
  { name: "commute_minutes", label: "Commute (minutes)", step: "1", sample: "74" },
  {
    name: "engagement_score",
    label: "Engagement",
    step: "0.1",
    hint: "0 to 5.",
    sample: "1.8"
  },
  { name: "training_hours_12m", label: "Training hours (12m)", step: "1", sample: "2" },
  { name: "overtime_hours_monthly", label: "Overtime (monthly)", step: "1", sample: "38" },
  { name: "internal_applications_12m", label: "Internal applications (12m)", step: "1", sample: "3" }
];

export function ScoreEmployee() {
  const [state, action] = useActionState(scoreEmployee, scoringIdle);
  const score = state.result?.[0];

  return (
    <div className="space-y-6">
      <Card
        title="Score an employee"
        description="Run one person through the trained model and see what drove the answer."
      >
        <form action={action} className="space-y-5">
          <div className="max-w-sm">
            <Field label="Employee reference" hint="A pseudonym, not a name.">
              <input name="subject_key" placeholder="emp-3391" className={inputClass} />
            </Field>
          </div>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {NUMERIC.map((field) => (
              <Field key={field.name} label={field.label} hint={field.hint}>
                <input
                  name={field.name}
                  type="number"
                  step={field.step}
                  min="0"
                  placeholder={field.sample}
                  className={inputClass}
                />
              </Field>
            ))}
          </div>
          {state.error ? <Notice tone="danger">{state.error}</Notice> : null}
          <SubmitButton label="Score" pendingLabel="Scoring…" />
        </form>
      </Card>

      {score ? (
        <Card title="Risk" description="What the model predicts, and why.">
          <div className="space-y-5">
            <div className="flex flex-wrap items-baseline gap-3">
              <Badge value={score.band} />
              <span className="text-3xl font-semibold tabular-nums">
                {(score.probability * 100).toFixed(0)}%
              </span>
              <span className="text-sm text-[var(--color-muted)]">
                chance of leaving within the horizon
              </span>
            </div>
            <Meter value={score.probability} tone={score.needs_intervention ? "danger" : "brand"} />

            {score.drivers.length > 0 ? (
              <ul className="space-y-2">
                {score.drivers.map((driver) => (
                  <li key={driver.feature} className="flex items-baseline justify-between gap-4">
                    <span className="text-sm">{driver.feature.replaceAll("_", " ")}</span>
                    <span className="text-xs text-[var(--color-muted)]">
                      {driver.direction} · {driver.contribution.toFixed(3)}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}

            <Notice tone={score.needs_intervention ? "warn" : "info"}>
              {score.needs_intervention
                ? "This is a prompt for a conversation with the employee, never grounds for an adverse decision about them."
                : "No intervention is indicated. The score is advisory either way."}
            </Notice>
          </div>
        </Card>
      ) : null}
    </div>
  );
}
