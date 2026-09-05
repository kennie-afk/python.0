import Image from "next/image";
import { NavLink } from "@/components/nav-link";
import { SignOutButton } from "@/components/sign-out-button";
import { requireSession } from "@/lib/session";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/runs", label: "Runs" },
  { href: "/workflows", label: "Workflows" },
  { href: "/screening", label: "Screening" },
  { href: "/compliance", label: "Compliance" },
  { href: "/attrition", label: "Retention" },
  { href: "/ledger", label: "Audit trail" }
];

export default async function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const session = await requireSession();

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-[var(--color-line)] bg-[var(--color-surface)] md:flex">
        <div className="flex items-center gap-2.5 px-5 py-6">
          <Image src="/mark.png" alt="TechMara" width={256} height={256} className="h-7 w-7" />
          <span className="text-base font-semibold tracking-tight">Aegis</span>
        </div>
        <nav className="flex flex-1 flex-col gap-0.5 px-3">
          {NAV.map((item) => (
            <NavLink key={item.href} href={item.href} label={item.label} />
          ))}
        </nav>
        <div className="border-t border-[var(--color-line)] p-3">
          <p className="px-3 pb-2 text-xs text-[var(--color-faint)]">
            <span className="block truncate">{session.subject}</span>
            <span className="block truncate">tenant {session.tenantId.slice(0, 8)}</span>
          </p>
          <SignOutButton />
        </div>
      </aside>
      <main className="flex-1 px-6 py-8 md:px-10">
        <div className="mx-auto max-w-5xl">{children}</div>
      </main>
    </div>
  );
}
