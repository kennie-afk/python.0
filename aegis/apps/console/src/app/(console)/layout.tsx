import Image from "next/image";
import { AccountMenu } from "@/components/account-menu";
import { SidebarNav, type NavSection } from "@/components/sidebar-nav";
import { requireSession } from "@/lib/session";

const SECTIONS: NavSection[] = [
  {
    key: "operations",
    label: "Operations",
    items: [
      { href: "/", label: "Overview" },
      { href: "/runs", label: "Runs" },
      { href: "/workflows", label: "Workflows" }
    ]
  },
  {
    key: "decisions",
    label: "Decisions",
    items: [
      { href: "/screening", label: "Screening" },
      { href: "/attrition", label: "Retention" }
    ]
  },
  {
    key: "assurance",
    label: "Assurance",
    items: [
      { href: "/compliance", label: "Compliance" },
      { href: "/ledger", label: "Audit trail" }
    ]
  }
];

export default async function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const session = await requireSession();

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-[var(--color-line)] bg-[var(--color-surface)] md:flex">
        <div className="flex items-center gap-2.5 px-5 py-5">
          <Image src="/mark.png" alt="TechMara" width={256} height={256} className="h-7 w-7" />
          <span className="text-base font-semibold tracking-tight">Aegis</span>
        </div>
        <SidebarNav sections={SECTIONS} />
        <div className="border-t border-[var(--color-line)] p-2">
          <AccountMenu subject={session.subject} tenantId={session.tenantId} />
        </div>
      </aside>
      <main className="flex-1 px-6 py-8 md:px-10">
        <div className="mx-auto max-w-5xl">{children}</div>
      </main>
    </div>
  );
}
