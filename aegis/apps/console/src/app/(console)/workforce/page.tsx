import { WorkforcePanel } from "@/components/analytics-panels";
import { PageHeader } from "@/components/ui";

export default function WorkforcePage() {
  return (
    <>
      <PageHeader
        title="Workforce"
        subtitle="Project headcount, attrition and effective capacity month by month, planned hiring against a freeze."
      />
      <WorkforcePanel />
    </>
  );
}
