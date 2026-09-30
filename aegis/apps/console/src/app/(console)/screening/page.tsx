import { FilterBar } from "@/components/filter-bar";
import { PAGE_SIZE, Pager, first, pageOf } from "@/components/pager";
import { ScreeningForm } from "@/components/screening-form";
import { Badge, Card, EmptyState, Meter, Notice, PageHeader, Table, rowClass } from "@/components/ui";
import { api, describeError } from "@/lib/api";
import { requireSession } from "@/lib/session";
import type { StoredScreeningView } from "@/lib/types";

export default async function ScreeningPage({
  searchParams
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const session = await requireSession();
  const raw = await searchParams;
  const page = pageOf(raw.page);
  const filters = { q: first(raw.q), recommendation: first(raw.recommendation) };

  const query = new URLSearchParams({
    limit: String(PAGE_SIZE),
    offset: String((page - 1) * PAGE_SIZE)
  });
  for (const [key, value] of Object.entries(filters)) {
    if (value) {
      query.set(key, value);
    }
  }

  let rows: StoredScreeningView[] = [];
  let total = 0;
  let error: string | null = null;
  try {
    const result = await api.page<StoredScreeningView>(`/v1/screenings?${query}`, session.token);
    rows = result.items;
    total = result.total;
  } catch (caught) {
    error = describeError(caught);
  }

  return (
    <>
      <PageHeader
        title="Screening"
        subtitle="Score an applicant against a requirement. Identity is removed before the model sees the record, and the prompt is fingerprinted so the score can be reproduced."
      />

      <section aria-labelledby="history" className="mb-10">
        <h2 id="history" className="mb-3 text-[0.875rem] font-semibold">
          Screened so far
        </h2>
        {error ? <Notice tone="danger">{error}</Notice> : null}
        {!error ? (
          <FilterBar
            action="/screening"
            fields={[
              { name: "q", label: "Search by applicant reference", value: filters.q, kind: "search", placeholder: "Applicant reference" },
              {
                name: "recommendation",
                label: "Recommendation",
                value: filters.recommendation,
                kind: "select",
                placeholder: "Any recommendation",
                options: [
                  { value: "ADVANCE", label: "Advance" },
                  { value: "REVIEW", label: "Review" },
                  { value: "HOLD", label: "Hold" }
                ]
              }
            ]}
          />
        ) : null}
        {!error && total === 0 ? (
          <div className="rounded-md border border-[var(--color-line)] bg-[var(--color-surface)]">
            <EmptyState
              message={Object.values(filters).some(Boolean) ? "No screenings match those filters." : "Nobody has been screened yet."}
              detail="Each applicant you screen below is kept here under a pseudonymous reference, never their name."
            />
          </div>
        ) : null}
        {rows.length > 0 ? (
          <>
            <Card>
              <Table head={["Applicant", "Score", "Recommendation", "Why", "Fingerprint", "When"]}>
                {rows.map((row) => (
                  <tr key={row.id} className={rowClass}>
                    <td className="px-3 py-2.5 font-mono text-xs">{row.subject_key}</td>
                    <td className="w-40 px-3 py-2.5">
                      <div className="flex items-center gap-3">
                        <span className="w-9 tabular-nums">{row.score.toFixed(2)}</span>
                        <Meter value={row.score} tone={row.recommendation === "ADVANCE" ? "good" : "accent"} />
                      </div>
                    </td>
                    <td className="px-3 py-2.5">
                      <Badge value={row.recommendation} />
                    </td>
                    <td className="max-w-[20rem] px-3 py-2.5 text-xs text-[var(--color-muted)]" title={row.rationale}>
                      <span className="line-clamp-2">{row.rationale}</span>
                    </td>
                    <td className="px-3 py-2.5 font-mono text-[0.6875rem] text-[var(--color-faint)]" title={row.requirement}>
                      {row.prompt_fingerprint.slice(0, 10)}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-xs text-[var(--color-faint)]">
                      {new Date(row.screened_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </Table>
            </Card>
            <Pager base="/screening" params={filters} page={page} total={total} noun="screenings" />
          </>
        ) : null}
      </section>

      <section aria-labelledby="score-one">
        <h2 id="score-one" className="mb-3 text-[0.875rem] font-semibold">
          Screen an applicant
        </h2>
        <ScreeningForm />
      </section>
    </>
  );
}
