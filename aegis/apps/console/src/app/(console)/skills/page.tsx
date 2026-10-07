import { ExtractPanel, GapPanel, MobilityPanel } from "@/components/analytics-panels";
import { PageHeader } from "@/components/ui";

export default function SkillsPage() {
  return (
    <>
      <PageHeader
        title="Skills"
        subtitle="What people can do, where the organisation is short, and who could move into what. Computed on request from the evidence you give; nothing is stored."
      />
      <div className="space-y-6">
        <ExtractPanel />
        <GapPanel />
        <MobilityPanel />
      </div>
    </>
  );
}
