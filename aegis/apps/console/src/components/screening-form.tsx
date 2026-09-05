"use client";

import { useActionState, useState } from "react";
import { SelectField } from "@/components/controls";
import { SubmitButton } from "@/components/submit-button";
import {
  Badge,
  Card,
  Field,
  Meter,
  Notice,
  inputClass,
  secondaryButtonClass
} from "@/components/ui";
import { screenCandidate } from "@/lib/actions";
import type { ScreeningState } from "@/lib/actions";

const initial: ScreeningState = { error: null, message: null, result: null };

const GENDERS = [
  { value: "female", label: "Female" },
  { value: "male", label: "Male" },
  { value: "non-binary", label: "Non-binary" },
  { value: "undisclosed", label: "Prefer not to say" }
];

const ROLES = [
  { value: "senior backend engineer", label: "Senior backend engineer" },
  { value: "backend engineer", label: "Backend engineer" },
  { value: "frontend engineer", label: "Frontend engineer" },
  { value: "data engineer", label: "Data engineer" },
  { value: "product manager", label: "Product manager" },
  { value: "people operations lead", label: "People operations lead" }
];

const EXAMPLE = {
  full_name: "Amina Wanjiru",
  email: "amina@example.com",
  national_id: "31445902",
  date_of_birth: "1994-03-11",
  university: "University of Nairobi",
  years_experience: "8",
  skill_match: "0.91",
  gender: "female",
  requirement: "senior backend engineer"
};

export function ScreeningForm() {
  const [state, action] = useActionState(screenCandidate, initial);
  const [seed, setSeed] = useState(0);
  const [filled, setFilled] = useState(false);
  const values = filled ? EXAMPLE : null;

  return (
    <div className="space-y-6">
      <Card
        title="Screen an applicant"
        description="Identity is stripped before the model reads the record. It never sees a name, an ID or a date of birth."
      >
        <form action={action} key={seed} className="space-y-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Full name">
              <input name="full_name" defaultValue={values?.full_name} className={inputClass} />
            </Field>
            <Field label="Email">
              <input
                name="email"
                type="email"
                defaultValue={values?.email}
                className={inputClass}
              />
            </Field>
            <Field label="National ID">
              <input name="national_id" defaultValue={values?.national_id} className={inputClass} />
            </Field>
            <Field label="Date of birth">
              <input
                name="date_of_birth"
                type="date"
                defaultValue={values?.date_of_birth}
                className={inputClass}
              />
            </Field>
            <SelectField
              key={`gender-${seed}`}
              label="Gender"
              name="gender"
              options={GENDERS}
              defaultValue={values?.gender}
            />
            <Field label="University">
              <input
                name="university"
                list="universities"
                defaultValue={values?.university}
                className={inputClass}
              />
              <datalist id="universities">
                <option value="University of Nairobi" />
                <option value="Jomo Kenyatta University" />
                <option value="Strathmore University" />
                <option value="Moi University" />
              </datalist>
            </Field>
            <Field label="Years of experience">
              <input
                name="years_experience"
                type="number"
                step="0.5"
                min="0"
                max="50"
                defaultValue={values?.years_experience}
                className={inputClass}
              />
            </Field>
            <Field label="Skill match" hint="How closely the profile matches, from 0 to 1.">
              <input
                name="skill_match"
                type="number"
                step="0.01"
                min="0"
                max="1"
                defaultValue={values?.skill_match}
                className={inputClass}
              />
            </Field>
          </div>

          <div className="max-w-md">
            <SelectField
              key={`requirement-${seed}`}
              label="Requirement"
              name="requirement"
              options={ROLES}
              defaultValue={values?.requirement}
              allowCustom
              hint="What the role actually needs."
            />
          </div>

          {state.error ? <Notice tone="danger">{state.error}</Notice> : null}

          <div className="flex flex-wrap items-center gap-3">
            <SubmitButton label="Screen" pendingLabel="Screening…" />
            <button
              type="button"
              className={secondaryButtonClass}
              onClick={() => {
                setFilled(true);
                setSeed((current) => current + 1);
              }}
            >
              Use an example
            </button>
            <button
              type="button"
              className={secondaryButtonClass}
              onClick={() => {
                setFilled(false);
                setSeed((current) => current + 1);
              }}
            >
              Clear
            </button>
          </div>
        </form>
      </Card>

      {state.result ? (
        <Card title="Result" description="What the model returned, and what it was allowed to see.">
          <div className="space-y-5">
            <div className="flex flex-wrap items-baseline gap-3">
              <Badge value={state.result.recommendation} />
              <span className="text-3xl font-semibold tabular-nums">
                {state.result.score.toFixed(2)}
              </span>
            </div>
            <Meter value={state.result.score} />
            <p className="text-sm text-[var(--color-muted)]">{state.result.rationale}</p>
            <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
              <div>
                <dt className="text-xs font-medium uppercase tracking-wide text-[var(--color-muted)]">
                  Pseudonym
                </dt>
                <dd className="mt-0.5 break-all font-mono text-xs">{state.result.subject_key}</dd>
              </div>
              <div>
                <dt className="text-xs font-medium uppercase tracking-wide text-[var(--color-muted)]">
                  Model
                </dt>
                <dd className="mt-0.5 text-sm">{state.result.model}</dd>
              </div>
              <div>
                <dt className="text-xs font-medium uppercase tracking-wide text-[var(--color-muted)]">
                  Signals the model used
                </dt>
                <dd className="mt-0.5 text-sm">
                  {state.result.signals_considered.join(", ") || "none"}
                </dd>
              </div>
              <div>
                <dt className="text-xs font-medium uppercase tracking-wide text-[var(--color-muted)]">
                  Prompt fingerprint
                </dt>
                <dd className="mt-0.5 break-all font-mono text-xs">
                  {state.result.prompt_fingerprint}
                </dd>
              </div>
            </dl>
            <Notice tone="good">
              Name, gender, date of birth and university were withheld. Only the signals listed
              above reached the model, and the fingerprint lets this exact decision be reproduced.
            </Notice>
          </div>
        </Card>
      ) : null}
    </div>
  );
}
