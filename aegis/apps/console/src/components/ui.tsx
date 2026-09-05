import type { ComponentPropsWithoutRef, ReactNode } from "react";

export function PageHeader({
  title,
  subtitle,
  actions
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-8 flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle ? (
          <p className="mt-1 max-w-2xl text-sm text-[var(--color-muted)]">{subtitle}</p>
        ) : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </header>
  );
}

export function Card({
  children,
  title,
  description,
  footer
}: {
  children: ReactNode;
  title?: string;
  description?: string;
  footer?: ReactNode;
}) {
  return (
    <section className="rounded-lg border border-[var(--color-line)] bg-[var(--color-surface)] shadow-[0_1px_2px_rgba(12,42,48,0.04)]">
      {title ? (
        <div className="border-b border-[var(--color-line)] px-5 py-4">
          <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
          {description ? (
            <p className="mt-1 text-xs text-[var(--color-muted)]">{description}</p>
          ) : null}
        </div>
      ) : null}
      <div className="p-5">{children}</div>
      {footer ? (
        <div className="border-t border-[var(--color-line)] bg-[var(--color-raised)] px-5 py-3">
          {footer}
        </div>
      ) : null}
    </section>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "good" | "warn" | "danger";
}) {
  const accent =
    tone === "good"
      ? "text-[var(--color-good)]"
      : tone === "warn"
        ? "text-[var(--color-warn)]"
        : tone === "danger"
          ? "text-[var(--color-danger)]"
          : "";
  return (
    <div className="rounded-lg border border-[var(--color-line)] bg-[var(--color-surface)] px-4 py-3 shadow-[0_1px_2px_rgba(12,42,48,0.04)] transition-shadow duration-150 hover:shadow-[0_2px_8px_rgba(12,42,48,0.08)]">
      <div className="flex items-baseline justify-between gap-3">
        <p className="truncate text-xs font-medium uppercase tracking-wide text-[var(--color-muted)]">
          {label}
        </p>
        <p
          className={`shrink-0 font-semibold tabular-nums ${
            value.length > 10 ? "text-lg" : "text-2xl"
          } ${accent}`}
        >
          {value}
        </p>
      </div>
      {hint ? (
        <p className="mt-0.5 truncate text-xs text-[var(--color-faint)]">{hint}</p>
      ) : null}
    </div>
  );
}

const TONE: Record<string, string> = {
  COMPLETED: "bg-[var(--color-good-soft)] text-[var(--color-good)]",
  ADVANCE: "bg-[var(--color-good-soft)] text-[var(--color-good)]",
  CLEAR: "bg-[var(--color-good-soft)] text-[var(--color-good)]",
  LOW: "bg-[var(--color-good-soft)] text-[var(--color-good)]",
  RUNNING: "bg-[var(--color-brand-soft)] text-[var(--color-brand)]",
  PENDING: "bg-[#e8eff0] text-[var(--color-muted)]",
  RETRIED: "bg-[var(--color-accent-soft)] text-[var(--color-accent)]",
  ADVERSE_IMPACT_NONE: "bg-[var(--color-good-soft)] text-[var(--color-good)]",
  AWAITING_APPROVAL: "bg-[var(--color-warn-soft)] text-[var(--color-warn)]",
  AWAITING_EXTERNAL: "bg-[var(--color-warn-soft)] text-[var(--color-warn)]",
  BLOCKED: "bg-[var(--color-warn-soft)] text-[var(--color-warn)]",
  REVIEW: "bg-[var(--color-warn-soft)] text-[var(--color-warn)]",
  MEDIUM: "bg-[var(--color-warn-soft)] text-[var(--color-warn)]",
  SKIPPED: "bg-[#e8eff0] text-[var(--color-muted)]",
  FAILED: "bg-[var(--color-danger-soft)] text-[var(--color-danger)]",
  DENIED: "bg-[var(--color-danger-soft)] text-[var(--color-danger)]",
  REJECTED: "bg-[var(--color-danger-soft)] text-[var(--color-danger)]",
  DECLINE: "bg-[var(--color-danger-soft)] text-[var(--color-danger)]",
  HIGH: "bg-[var(--color-danger-soft)] text-[var(--color-danger)]",
  ADVERSE_IMPACT: "bg-[var(--color-danger-soft)] text-[var(--color-danger)]"
};

export function Badge({ value, muted }: { value: string; muted?: boolean }) {
  const tone = muted
    ? "bg-[#e8eff0] text-[var(--color-muted)]"
    : (TONE[value] ?? "bg-[#e8eff0] text-[var(--color-muted)]");
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${tone}`}
    >
      {value.replaceAll("_", " ").toLowerCase()}
    </span>
  );
}

export function EmptyState({ message, action }: { message: string; action?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-[var(--color-line)] bg-[var(--color-surface)] px-5 py-10 text-center">
      <p className="text-sm text-[var(--color-muted)]">{message}</p>
      {action ? <div className="mt-4 flex justify-center">{action}</div> : null}
    </div>
  );
}

export function Notice({
  tone = "info",
  children
}: {
  tone?: "info" | "good" | "warn" | "danger";
  children: ReactNode;
}) {
  const styles = {
    info: "border-[var(--color-line)] bg-[var(--color-raised)] text-[var(--color-muted)]",
    good: "border-[#bfe4e0] bg-[var(--color-good-soft)] text-[var(--color-good)]",
    warn: "border-[#f0dcbd] bg-[var(--color-warn-soft)] text-[var(--color-warn)]",
    danger: "border-[#f2cdcb] bg-[var(--color-danger-soft)] text-[var(--color-danger)]"
  }[tone];
  return <div className={`rounded-md border px-4 py-3 text-sm ${styles}`}>{children}</div>;
}

export function Field({
  label,
  hint,
  children
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="block text-xs font-medium uppercase tracking-wide text-[var(--color-muted)]">
        {label}
      </span>
      {children}
      {hint ? <span className="mt-1 block text-xs text-[var(--color-faint)]">{hint}</span> : null}
    </label>
  );
}

export const selectClass =
  "mt-1.5 w-full cursor-pointer appearance-none rounded-md border border-[var(--color-line)] bg-[var(--color-surface)] bg-[length:16px] bg-[right_0.75rem_center] bg-no-repeat py-2 pl-3 pr-9 text-sm outline-none transition-colors duration-150 hover:border-[var(--color-faint)] focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)] bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2020%2020%22%20fill%3D%22none%22%20stroke%3D%22%23536f74%22%20stroke-width%3D%221.75%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%3E%3Cpath%20d%3D%22M6%208l4%204%204-4%22%2F%3E%3C%2Fsvg%3E')]";

export function Select({
  label,
  hint,
  placeholder = "Choose…",
  options,
  ...props
}: {
  label?: string;
  hint?: string;
  placeholder?: string;
  options: { value: string; label: string }[];
} & Omit<ComponentPropsWithoutRef<"select">, "children">) {
  const field = (
    <select {...props} className={selectClass}>
      <option value="">{placeholder}</option>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );

  if (!label) {
    return field;
  }

  return (
    <Field label={label} hint={hint}>
      {field}
    </Field>
  );
}

export const inputClass =
  "mt-1.5 w-full rounded-md border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none transition-colors duration-150 placeholder:text-[var(--color-faint)] hover:border-[var(--color-faint)] focus:border-[var(--color-brand)] focus:ring-2 focus:ring-[var(--color-brand-soft)] disabled:cursor-not-allowed disabled:bg-[var(--color-raised)]";

const buttonBase =
  "inline-flex cursor-pointer items-center justify-center rounded-md px-3.5 py-2 text-sm font-medium transition-all duration-150 active:translate-y-px disabled:pointer-events-none disabled:opacity-50";

export const buttonClass =
  `${buttonBase} bg-[var(--color-brand)] text-white shadow-sm hover:bg-[var(--color-deep)] hover:shadow active:bg-[var(--color-deep)]`;

export const secondaryButtonClass =
  `${buttonBase} border border-[var(--color-line)] bg-[var(--color-surface)] text-[var(--color-ink)] hover:border-[var(--color-faint)] hover:bg-[var(--color-raised)]`;

export const dangerButtonClass =
  `${buttonBase} border border-[#f2cdcb] bg-[var(--color-danger-soft)] text-[var(--color-danger)] hover:border-[var(--color-danger)] hover:bg-[#f9dedc]`;

export const rowClass =
  "border-b border-[var(--color-line)] transition-colors duration-150 last:border-0 hover:bg-[var(--color-raised)]";

export function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[var(--color-line)] text-left">
            {head.map((column) => (
              <th
                key={column}
                className="px-3 py-2 text-xs font-medium uppercase tracking-wide text-[var(--color-muted)]"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function KeyValue({ items }: { items: [string, string][] }) {
  return (
    <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
      {items.map(([key, value]) => (
        <div key={key}>
          <dt className="text-xs font-medium uppercase tracking-wide text-[var(--color-muted)]">
            {key}
          </dt>
          <dd className="mt-0.5 break-words text-sm">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Meter({ value, tone = "brand" }: { value: number; tone?: "brand" | "danger" }) {
  const percent = Math.max(0, Math.min(1, value)) * 100;
  const colour = tone === "danger" ? "var(--color-danger)" : "var(--color-brand)";
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-[#e2edee]">
      <div className="h-full rounded-full" style={{ width: `${percent}%`, background: colour }} />
    </div>
  );
}
