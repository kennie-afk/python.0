import { api, describeError } from "@/lib/api";
import { requireSession } from "@/lib/session";
import { LedgerTable } from "@/components/ledger-table";
import { EmptyState, Notice, PageHeader } from "@/components/ui";
import type { IntegrityView, LedgerEntryView } from "@/lib/types";

export default async function LedgerPage() {
  const session = await requireSession();

  let entries: LedgerEntryView[] = [];
  let integrity: IntegrityView | null = null;
  let error: string | null = null;

  try {
    [entries, integrity] = await Promise.all([
      api.get<LedgerEntryView[]>("/v1/ledger", session.token),
      api.get<IntegrityView>("/v1/ledger/verify", session.token)
    ]);
  } catch (caught) {
    error = describeError(caught);
  }

  return (
    <>
      <PageHeader
        title="Audit trail"
        subtitle="Every decision an agent or a person made, hashed onto the one before it. Editing history breaks the chain and this page will say so."
      />

      {error ? <Notice tone="danger">{error}</Notice> : null}

      {integrity ? (
        <div className="mb-6">
          <Notice tone={integrity.intact ? "good" : "danger"}>
            {integrity.intact
              ? `Chain intact across ${integrity.entries_checked} entries.`
              : `Chain broken at entry ${integrity.broken_at}. ${integrity.reason ?? ""}`}
          </Notice>
        </div>
      ) : null}

      {!error && entries.length === 0 ? (
        <EmptyState message="Nothing has been recorded yet. Entries appear as soon as a run takes its first step." />
      ) : null}

      {entries.length > 0 ? <LedgerTable entries={entries} /> : null}
    </>
  );
}
