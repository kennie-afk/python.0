import { ImpactForm } from "@/components/impact-form";
import { PageHeader } from "@/components/ui";
import { requireSession } from "@/lib/session";

export default async function CompliancePage() {
  await requireSession();

  return (
    <>
      <PageHeader
        title="Compliance"
        subtitle="Check a hiring process for adverse impact before a regulator or a claimant does it for you."
      />
      <ImpactForm />
    </>
  );
}
