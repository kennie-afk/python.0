import { Rail, type RailItem } from "@/components/rail";

const ITEMS: RailItem[] = [
  { href: "/", label: "Overview", icon: "home" },
  { href: "/feed", label: "Feed", icon: "feed" },
  { href: "/retrieval", label: "Search", icon: "search" },
  { href: "/model", label: "Ranker", icon: "model" },
  { href: "/registry", label: "Registry", icon: "registry" },
  { href: "/experiment", label: "Experiment", icon: "experiment" },
  { href: "/drift", label: "Drift", icon: "drift" },
  { href: "/load", label: "Load", icon: "load" }
];

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen bg-[var(--color-canvas)]">
      <Rail items={ITEMS} />
      <main className="flex-1 px-6 py-10 md:px-12 lg:px-16">
        <div className="mx-auto max-w-5xl">{children}</div>
      </main>
    </div>
  );
}
