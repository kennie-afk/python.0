import { redirect } from "next/navigation";
import { Rail, type RailItem } from "@/components/rail";
import { readSession } from "@/lib/session";

const ITEMS: RailItem[] = [
  { href: "/", label: "Overview", icon: "home" },
  { href: "/runs", label: "Runs", icon: "runs" },
  { href: "/workflows", label: "Workflows", icon: "workflows" },
  { href: "/screening", label: "Screening", icon: "screening" },
  { href: "/attrition", label: "Retention", icon: "retention" },
  { href: "/compliance", label: "Compliance", icon: "compliance" },
  { href: "/ledger", label: "Audit", icon: "ledger" }
];

export default async function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const session = await readSession();
  if (!session) {
    redirect("/login");
  }

  return (
    <div className="flex min-h-screen bg-[var(--color-canvas)]">
      <Rail items={ITEMS} />
      <main className="flex-1 px-6 py-10 md:px-12 lg:px-16">
        <div className="mx-auto max-w-5xl">{children}</div>
      </main>
    </div>
  );
}
