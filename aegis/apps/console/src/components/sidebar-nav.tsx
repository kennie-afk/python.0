"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

export interface NavSection {
  key: string;
  label: string;
  items: { href: string; label: string }[];
}

const STORAGE_KEY = "aegis.nav.collapsed";

function readCollapsed(): string[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

export function SidebarNav({ sections }: { sections: NavSection[] }) {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setCollapsed(readCollapsed());
    setReady(true);
  }, []);

  function toggle(key: string) {
    setCollapsed((current) => {
      const next = current.includes(key)
        ? current.filter((item) => item !== key)
        : [...current, key];
      try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      } catch {
        return next;
      }
      return next;
    });
  }

  function isActive(href: string): boolean {
    return href === "/" ? pathname === "/" : pathname.startsWith(href);
  }

  return (
    <nav className="flex flex-1 flex-col overflow-y-auto px-3 pb-4">
      {sections.map((section) => {
        const holdsActive = section.items.some((item) => isActive(item.href));
        const open = !ready || !collapsed.includes(section.key) || holdsActive;

        return (
          <div key={section.key} className="mt-5 first:mt-0">
            <button
              type="button"
              onClick={() => toggle(section.key)}
              aria-expanded={open}
              className="flex w-full cursor-pointer items-center justify-between px-2 pb-1.5 text-[0.625rem] font-bold uppercase tracking-[0.16em] text-[var(--color-faint)] transition-colors duration-150 hover:text-[var(--color-muted)]"
            >
              {section.label}
              <svg
                viewBox="0 0 20 20"
                aria-hidden="true"
                className={`h-3.5 w-3.5 transition-transform duration-200 ${
                  open ? "" : "-rotate-90"
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

            <div
              className={`grid transition-all duration-200 ${
                open ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
              }`}
            >
              <div className="overflow-hidden">
                <div className="ml-2 flex flex-col gap-0.5 border-l border-[var(--color-line)] pl-2.5">
                  {section.items.map((item) => {
                    const active = isActive(item.href);
                    return (
                      <Link
                        key={item.href}
                        href={item.href}
                        tabIndex={open ? undefined : -1}
                        aria-current={active ? "page" : undefined}
                        className={`relative rounded-md px-2.5 py-1.5 text-sm transition-colors duration-150 ${
                          active
                            ? "bg-[var(--color-brand-soft)] font-semibold text-[var(--color-brand)] before:absolute before:-left-[0.6875rem] before:bottom-0.5 before:top-0.5 before:w-[2px] before:rounded-full before:bg-[var(--color-brand)] before:content-['']"
                            : "text-[var(--color-muted)] hover:bg-[var(--color-raised)] hover:text-[var(--color-ink)]"
                        }`}
                      >
                        {item.label}
                      </Link>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </nav>
  );
}
