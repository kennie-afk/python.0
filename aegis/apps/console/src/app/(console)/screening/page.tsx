import { ScreeningForm } from "@/components/screening-form";
import { PageHeader } from "@/components/ui";
import { requireSession } from "@/lib/session";

export default async function ScreeningPage() {
  await requireSession();

  return (
    <>
      <PageHeader
        title="Screening"
        subtitle="Score an applicant against a requirement. Identity is removed before the model sees the record, and the prompt is fingerprinted so the score can be reproduced."
      />
      <ScreeningForm />
    </>
  );
}
