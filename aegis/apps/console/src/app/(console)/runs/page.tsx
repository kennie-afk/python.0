import Link from "next/link";
import { api, describeError } from "@/lib/api";
import { requireSession } from "@/lib/session";
import { RunsTable } from "@/components/runs-table";
import { EmptyState, Notice, PageHeader, secondaryButtonClass } from "@/components/ui";
import type { RunView } from "@/lib/types";

export default async function RunsPage() {
  const session = await requireSession();

  let runs: RunView[] = [];
  let error: string | null = null;
  try {
    runs = await api.get<RunView[]>("/v1/runs", session.token);
  } catch (caught) {
    error = describeError(caught);
  }

  return (
    <>
      <PageHeader
        title="Runs"
        subtitle="Every workflow this tenant has started, newest first."
        actions={
          <Link href="/workflows" className={secondaryButtonClass}>
            Start a run
          </Link>
        }
      />

      {error ? <Notice tone="danger">{error}</Notice> : null}

      {!error && runs.length === 0 ? (
        <EmptyState
          message="No runs yet. A run is one workflow carried out for one person."
          action={
            <Link href="/workflows" className={secondaryButtonClass}>
              Start the first one
            </Link>
          }
        />
      ) : null}

      {!error && runs.length > 0 ? <RunsTable runs={runs} /> : null}
    </>
  );
}
