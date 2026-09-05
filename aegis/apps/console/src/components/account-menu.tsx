"use client";

import { useEffect, useRef, useState } from "react";
import { signOut } from "@/lib/actions";

export function AccountMenu({ subject, tenantId }: { subject: string; tenantId: string }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    function onPointerDown(event: MouseEvent) {
      if (!container.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  async function copyTenant() {
    try {
      await navigator.clipboard.writeText(tenantId);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  const initials = subject.replace(/^key:/, "").slice(0, 2).toUpperCase();

  return (
    <div ref={container} className="relative">
      {open ? (
        <div className="absolute bottom-full left-0 z-20 mb-2 w-full min-w-56 overflow-hidden rounded-lg border border-[var(--color-line)] bg-[var(--color-surface)] shadow-[0_8px_24px_rgba(12,42,48,0.14)]">
          <div className="border-b border-[var(--color-line)] px-3 py-2.5">
            <p className="truncate text-sm font-medium">{subject}</p>
            <p className="mt-0.5 break-all font-mono text-[0.6875rem] text-[var(--color-faint)]">
              {tenantId}
            </p>
          </div>
          <button
            type="button"
            onClick={copyTenant}
            className="flex w-full cursor-pointer items-center justify-between px-3 py-2 text-left text-sm text-[var(--color-muted)] transition-colors duration-150 hover:bg-[var(--color-raised)] hover:text-[var(--color-ink)]"
          >
            Copy tenant id
            {copied ? <span className="text-xs text-[var(--color-good)]">Copied</span> : null}
          </button>
          <form action={signOut} className="border-t border-[var(--color-line)]">
            <button
              type="submit"
              className="w-full cursor-pointer px-3 py-2 text-left text-sm text-[var(--color-danger)] transition-colors duration-150 hover:bg-[var(--color-danger-soft)]"
            >
              Sign out
            </button>
          </form>
        </div>
      ) : null}

      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        aria-haspopup="menu"
        className={`flex w-full cursor-pointer items-center gap-2.5 rounded-md px-2 py-2 text-left transition-colors duration-150 ${
          open ? "bg-[var(--color-raised)]" : "hover:bg-[var(--color-raised)]"
        }`}
      >
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--color-brand-soft)] text-[0.625rem] font-semibold text-[var(--color-brand)]">
          {initials}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-medium">{subject}</span>
          <span className="block truncate text-[0.6875rem] text-[var(--color-faint)]">
            tenant {tenantId.slice(0, 8)}
          </span>
        </span>
        <svg
          viewBox="0 0 20 20"
          aria-hidden="true"
          className={`h-3.5 w-3.5 shrink-0 text-[var(--color-faint)] transition-transform duration-200 ${
            open ? "rotate-180" : ""
          }`}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M6 8l4 4 4-4" />
        </svg>
      </button>
    </div>
  );
}
