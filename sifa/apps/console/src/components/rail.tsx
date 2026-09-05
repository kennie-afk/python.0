"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { Icon, type IconName } from "@/components/icons";

export interface RailItem {
  href: string;
  label: string;
  icon: IconName;
}

export function Rail({ items }: { items: RailItem[] }) {
  const pathname = usePathname();

  return (
    <aside className="sticky top-0 hidden h-screen w-[92px] shrink-0 flex-col border-r border-[var(--color-line)] bg-[var(--color-rail)] md:flex">
      <div className="flex justify-center py-6">
        <Image src="/mark.svg" alt="Sifa" width={256} height={256} className="h-8 w-8" priority />
      </div>

      <nav className="flex flex-1 flex-col gap-1 px-2.5">
        {items.map((item) => {
          const active =
            item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={`flex flex-col items-center gap-1.5 rounded-xl px-1 py-3 text-[0.6875rem] font-medium transition-colors ${
                active
                  ? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                  : "text-[var(--color-muted)] hover:bg-[var(--color-raised)] hover:text-[var(--color-ink)]"
              }`}
            >
              <Icon name={item.icon} className="h-[22px] w-[22px]" />
              <span className="text-center leading-tight">{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="flex flex-col items-center gap-1.5 px-2 pb-6 pt-4">
        <span className="flex h-9 w-9 items-center justify-center rounded-full bg-[var(--color-violet)] text-sm font-semibold text-white">
          S
        </span>
        <span className="text-[0.6875rem] font-medium text-[var(--color-muted)]">Sifa</span>
      </div>
    </aside>
  );
}
