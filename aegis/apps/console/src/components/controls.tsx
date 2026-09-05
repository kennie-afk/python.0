"use client";

import { useId, useState } from "react";
import { Field, Select, inputClass, secondaryButtonClass } from "@/components/ui";

export function SelectField({
  label,
  name,
  options,
  hint,
  value,
  defaultValue,
  allowCustom
}: {
  label: string;
  name: string;
  options: { value: string; label: string }[];
  hint?: string;
  value?: string;
  defaultValue?: string;
  allowCustom?: boolean;
}) {
  const [selected, setSelected] = useState(value ?? defaultValue ?? "");
  const custom = allowCustom && selected === "__custom__";
  const choices = allowCustom
    ? [...options, { value: "__custom__", label: "Something else…" }]
    : options;

  return (
    <div>
      <Select
        label={label}
        hint={custom ? undefined : hint}
        name={custom ? undefined : name}
        options={choices}
        value={selected}
        onChange={(event) => setSelected(event.target.value)}
      />
      {custom ? (
        <input name={name} autoFocus placeholder="Type it in" className={`${inputClass} mt-2`} />
      ) : null}
    </div>
  );
}

function toIso(local: string): string {
  if (!local) {
    return "";
  }
  const parsed = new Date(local);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  return parsed.toISOString().replace(/\.\d{3}Z$/, "+00:00");
}

export function DateTimeField({
  label,
  name,
  hint,
  defaultValue
}: {
  label: string;
  name: string;
  hint?: string;
  defaultValue?: string;
}) {
  const [local, setLocal] = useState(defaultValue ?? "");
  const iso = toIso(local);

  return (
    <div>
      <Field label={label} hint={iso ? `Sent as ${iso}` : (hint ?? "Pick a date and a time.")}>
        <input
          type="datetime-local"
          value={local}
          onChange={(event) => setLocal(event.target.value)}
          className={inputClass}
        />
      </Field>
      <input type="hidden" name={name} value={iso} />
    </div>
  );
}

export function EmailListField({
  label,
  name,
  hint,
  initial = [""]
}: {
  label: string;
  name: string;
  hint?: string;
  initial?: string[];
}) {
  const id = useId();
  const [rows, setRows] = useState<string[]>(initial.length > 0 ? initial : [""]);

  function update(index: number, next: string) {
    setRows((current) => current.map((row, position) => (position === index ? next : row)));
  }

  return (
    <div>
      <span className="block text-[0.625rem] font-medium uppercase tracking-[0.06em] text-[var(--color-faint)]">
        {label}
      </span>
      <div className="mt-1.5 space-y-2">
        {rows.map((row, index) => (
          <div key={`${id}-${index}`} className="flex gap-2">
            <input
              name={name}
              type="email"
              value={row}
              placeholder="name@company.com"
              onChange={(event) => update(index, event.target.value)}
              className={`${inputClass} mt-0`}
            />
            {rows.length > 1 ? (
              <button
                type="button"
                aria-label={`Remove ${row || "this address"}`}
                onClick={() =>
                  setRows((current) => current.filter((_, position) => position !== index))
                }
                className="shrink-0 cursor-pointer rounded-md border border-[var(--color-line)] px-3 text-sm text-[var(--color-muted)] transition-colors duration-150 hover:border-[var(--color-danger)] hover:bg-[var(--color-danger-soft)] hover:text-[var(--color-danger)]"
              >
                Remove
              </button>
            ) : null}
          </div>
        ))}
      </div>
      <button
        type="button"
        onClick={() => setRows((current) => [...current, ""])}
        className={`${secondaryButtonClass} mt-2 px-2.5 py-1 text-xs`}
      >
        Add another
      </button>
      {hint ? <span className="mt-1 block text-xs text-[var(--color-faint)]">{hint}</span> : null}
    </div>
  );
}
