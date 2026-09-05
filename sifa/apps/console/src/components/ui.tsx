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
      <div className="max-w-2xl">
        <h1 className="text-[1.75rem] font-bold leading-tight tracking-[-0.02em]">{title}</h1>
        {subtitle ? (
          <p className="mt-2 text-[0.9375rem] leading-relaxed text-[var(--color-muted)]">
            {subtitle}
          </p>
        ) : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </header>
  );
}

export function Tabs({
  items,
  active
}: {
  items: { href: string; label: string }[];
  active: string;
}) {
  return (
    <nav className="mb-8 flex flex-wrap items-center gap-1">
      {items.map((item) => {
        const current = item.href === active;
        return (
          <a
            key={item.href}
            href={item.href}
            className={`rounded-lg px-3.5 py-2 text-sm font-medium transition-colors ${
              current
                ? "bg-[var(--color-ink)] text-white"
                : "text-[var(--color-muted)] hover:bg-[var(--color-raised)] hover:text-[var(--color-ink)]"
            }`}
          >
            {item.label}
          </a>
        );
      })}
    </nav>
  );
}

export function Card({
  children,
  title,
  description,
  actions
}: {
  children: ReactNode;
  title?: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)]">
      {title ? (
        <div className="flex flex-wrap items-start justify-between gap-3 px-6 pt-5 pb-4">
          <div>
            <h2 className="text-[0.9375rem] font-semibold tracking-[-0.01em]">{title}</h2>
            {description ? (
              <p className="mt-1 text-[0.8125rem] leading-relaxed text-[var(--color-muted)]">
                {description}
              </p>
            ) : null}
          </div>
          {actions}
        </div>
      ) : null}
      <div className={title ? "px-6 pb-6" : "p-6"}>{children}</div>
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
  tone?: "good" | "warn" | "danger" | "accent";
}) {
  const accent = {
    good: "text-[var(--color-good)]",
    warn: "text-[var(--color-warn)]",
    danger: "text-[var(--color-danger)]",
    accent: "text-[var(--color-accent)]"
  }[tone ?? "good"];

  const size =
    value.length <= 6
      ? "text-[1.375rem]"
      : value.length <= 12
        ? "text-[1.125rem]"
        : "text-[0.9375rem]";

  return (
    <div className="rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] px-5 py-4">
      <p className="text-[0.6875rem] font-medium uppercase tracking-[0.07em] text-[var(--color-faint)]">
        {label}
      </p>
      <p
        className={`mt-1.5 font-semibold tabular-nums tracking-[-0.01em] ${size} ${
          tone ? accent : ""
        }`}
      >
        {value}
      </p>
      {hint ? (
        <p className="mt-1 text-[0.75rem] leading-relaxed text-[var(--color-muted)]">{hint}</p>
      ) : null}
    </div>
  );
}

const TONE: Record<string, string> = {
  GOOD: "bg-[var(--color-good-soft)] text-[var(--color-good)]",
  LIVE: "bg-[var(--color-good-soft)] text-[var(--color-good)]",
  STABLE: "bg-[var(--color-good-soft)] text-[var(--color-good)]",
  TREATMENT_WINS: "bg-[var(--color-good-soft)] text-[var(--color-good)]",
  CANARY: "bg-[var(--color-warn-soft)] text-[var(--color-warn)]",
  WARN: "bg-[var(--color-warn-soft)] text-[var(--color-warn)]",
  CONTINUE: "bg-[var(--color-warn-soft)] text-[var(--color-warn)]",
  ALERT: "bg-[var(--color-danger-soft)] text-[var(--color-danger)]",
  ROLLED_BACK: "bg-[var(--color-danger-soft)] text-[var(--color-danger)]",
  DANGER: "bg-[var(--color-danger-soft)] text-[var(--color-danger)]",
  SHADOW: "bg-[var(--color-accent-soft)] text-[var(--color-accent)]",
  CONTROL: "bg-[var(--color-accent-soft)] text-[var(--color-accent)]",
  CONTROL_WINS: "bg-[var(--color-accent-soft)] text-[var(--color-accent)]",
  TREATMENT: "bg-[var(--color-violet-soft)] text-[var(--color-violet)]",
  EXPLORED: "bg-[var(--color-violet-soft)] text-[var(--color-violet)]",
  ACTIVE: "bg-[var(--color-good-soft)] text-[var(--color-good)]",
  COMPLETED: "bg-[var(--color-good-soft)] text-[var(--color-good)]",
  SETTLED: "bg-[var(--color-good-soft)] text-[var(--color-good)]",
  LOW: "bg-[var(--color-good-soft)] text-[var(--color-good)]",
  OPEN: "bg-[var(--color-warn-soft)] text-[var(--color-warn)]",
  PENDING: "bg-[var(--color-warn-soft)] text-[var(--color-warn)]",
  MEDIUM: "bg-[var(--color-warn-soft)] text-[var(--color-warn)]",
  REVIEWING: "bg-[var(--color-warn-soft)] text-[var(--color-warn)]",
  HIGH: "bg-[var(--color-danger-soft)] text-[var(--color-danger)]",
  CRITICAL: "bg-[var(--color-danger-soft)] text-[var(--color-danger)]",
  SUSPENDED: "bg-[var(--color-danger-soft)] text-[var(--color-danger)]",
  PLANNED: "bg-[var(--color-accent-soft)] text-[var(--color-accent)]",
  LISTED: "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
};

export function Badge({ value, dot }: { value: string; dot?: boolean }) {
  const key = value.toUpperCase().replaceAll(" ", "_");
  const tone = TONE[key] ?? "bg-[var(--color-raised)] text-[var(--color-muted)]";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[0.75rem] font-medium ${tone}`}
    >
      {dot ? <span className="h-1.5 w-1.5 rounded-full bg-current" /> : null}
      {value.replaceAll("_", " ")}
    </span>
  );
}

export function EmptyState({
  message,
  detail,
  action
}: {
  message: string;
  detail?: string;
  action?: ReactNode;
}) {
  return (
    <div className="px-6 py-16 text-center">
      <div className="mx-auto mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-[var(--color-raised)]">
        <svg viewBox="0 0 24 24" className="h-5 w-5 text-[var(--color-faint)]" fill="none" stroke="currentColor" strokeWidth="1.6">
          <circle cx="11" cy="11" r="7" />
          <path d="M20 20l-3.5-3.5" strokeLinecap="round" />
        </svg>
      </div>
      <p className="text-[0.9375rem] font-medium">{message}</p>
      {detail ? (
        <p className="mx-auto mt-1.5 max-w-md text-[0.8125rem] text-[var(--color-muted)]">
          {detail}
        </p>
      ) : null}
      {action ? <div className="mt-5 flex justify-center">{action}</div> : null}
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
    good: "border-[#c8e9db] bg-[var(--color-good-soft)] text-[var(--color-good)]",
    warn: "border-[#f0dfbd] bg-[var(--color-warn-soft)] text-[var(--color-warn)]",
    danger: "border-[#f5cdcb] bg-[var(--color-danger-soft)] text-[var(--color-danger)]"
  }[tone];
  return (
    <div className={`rounded-xl border px-5 py-4 text-[0.875rem] leading-relaxed ${styles}`}>
      {children}
    </div>
  );
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
      <span className="block text-[0.8125rem] font-medium text-[var(--color-ink)]">{label}</span>
      {children}
      {hint ? (
        <span className="mt-1.5 block text-[0.75rem] text-[var(--color-muted)]">{hint}</span>
      ) : null}
    </label>
  );
}

export const inputClass =
  "mt-2 w-full rounded-lg border border-[var(--color-line)] bg-[var(--color-surface)] px-3.5 py-2.5 text-sm outline-none transition-colors placeholder:text-[var(--color-faint)] hover:border-[var(--color-faint)] focus:border-[var(--color-accent)] focus:ring-4 focus:ring-[var(--color-accent-soft)]";

export const selectClass =
  "mt-2 w-full cursor-pointer appearance-none rounded-lg border border-[var(--color-line)] bg-[var(--color-surface)] bg-[length:16px] bg-[right_0.875rem_center] bg-no-repeat py-2.5 pl-3.5 pr-10 text-sm outline-none transition-colors hover:border-[var(--color-faint)] focus:border-[var(--color-accent)] focus:ring-4 focus:ring-[var(--color-accent-soft)] bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2020%2020%22%20fill%3D%22none%22%20stroke%3D%22%236b7280%22%20stroke-width%3D%221.75%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%3E%3Cpath%20d%3D%22M6%208l4%204%204-4%22%2F%3E%3C%2Fsvg%3E')]";

const buttonBase =
  "inline-flex cursor-pointer items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-medium transition-all active:translate-y-px disabled:pointer-events-none disabled:opacity-50";

export const buttonClass = `${buttonBase} bg-[var(--color-ink)] text-white hover:bg-[#242832]`;

export const secondaryButtonClass = `${buttonBase} border border-[var(--color-line)] bg-[var(--color-surface)] text-[var(--color-ink)] hover:bg-[var(--color-raised)]`;

export const dangerButtonClass = `${buttonBase} border border-[#f5cdcb] bg-[var(--color-surface)] text-[var(--color-danger)] hover:bg-[var(--color-danger-soft)]`;

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
  return label ? (
    <Field label={label} hint={hint}>
      {field}
    </Field>
  ) : (
    field
  );
}

export const rowClass =
  "border-b border-[var(--color-line)] transition-colors last:border-0 hover:bg-[var(--color-raised)]";

export function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[var(--color-line)] text-left">
            {head.map((column) => (
              <th
                key={column}
                className="px-4 py-3 text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-[var(--color-muted)]"
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

export function Meter({ value, tone = "accent" }: { value: number; tone?: "accent" | "danger" | "good" }) {
  const percent = Math.max(0, Math.min(1, value)) * 100;
  const colour = {
    accent: "var(--color-accent)",
    danger: "var(--color-danger)",
    good: "var(--color-good)"
  }[tone];
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-line)]">
      <div className="h-full rounded-full transition-all" style={{ width: `${percent}%`, background: colour }} />
    </div>
  );
}

export function KeyValue({ items }: { items: [string, string][] }) {
  return (
    <dl className="grid gap-x-8 gap-y-4 sm:grid-cols-2">
      {items.map(([key, value]) => (
        <div key={key}>
          <dt className="text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-[var(--color-muted)]">
            {key}
          </dt>
          <dd className="mt-1 break-words text-sm">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
