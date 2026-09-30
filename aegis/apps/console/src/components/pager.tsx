import Link from "next/link";
import { secondaryButtonClass } from "@/components/ui";

export const PAGE_SIZE = 25;

export function pageOf(raw: string | string[] | undefined): number {
  const value = Number(Array.isArray(raw) ? raw[0] : raw);
  return Number.isInteger(value) && value > 0 ? value : 1;
}

export function first(raw: string | string[] | undefined): string {
  return (Array.isArray(raw) ? raw[0] : raw) ?? "";
}

function link(base: string, params: Record<string, string>, page: number): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) {
      query.set(key, value);
    }
  }
  if (page > 1) {
    query.set("page", String(page));
  }
  const text = query.toString();
  return text ? `${base}?${text}` : base;
}

/** Real paging: the count says what is shown out of what exists, and an inert control is marked so. */
export function Pager({
  base,
  params,
  page,
  total,
  size = PAGE_SIZE,
  noun
}: {
  base: string;
  params: Record<string, string>;
  page: number;
  total: number;
  size?: number;
  noun: string;
}) {
  const pages = Math.max(1, Math.ceil(total / size));
  const from = total === 0 ? 0 : (page - 1) * size + 1;
  const to = Math.min(total, page * size);

  return (
    <nav aria-label={`${noun} pages`} className="mt-3 flex items-center justify-between gap-3">
      <p className="text-[0.75rem] tabular-nums text-[var(--color-muted)]">
        {total === 0 ? `No ${noun}` : `${from}–${to} of ${total} ${noun}`}
      </p>
      <div className="flex items-center gap-2">
        {page > 1 ? (
          <Link href={link(base, params, page - 1)} className={secondaryButtonClass} rel="prev">
            Previous
          </Link>
        ) : (
          <span aria-disabled="true" className={`${secondaryButtonClass} pointer-events-none opacity-50`}>
            Previous
          </span>
        )}
        <span className="text-[0.75rem] tabular-nums text-[var(--color-muted)]">
          Page {page} of {pages}
        </span>
        {page < pages ? (
          <Link href={link(base, params, page + 1)} className={secondaryButtonClass} rel="next">
            Next
          </Link>
        ) : (
          <span aria-disabled="true" className={`${secondaryButtonClass} pointer-events-none opacity-50`}>
            Next
          </span>
        )}
      </div>
    </nav>
  );
}
