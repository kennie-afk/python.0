import { Suspense } from "react";
import { api, describeError } from "@/lib/api";
import type { Retrieval, UserSummary } from "@/lib/types";
import { UserPicker } from "@/components/user-picker";
import { Badge, Card, Notice, PageHeader, Stat, Table } from "@/components/ui";

export default async function RetrievalPage({
  searchParams
}: {
  searchParams: Promise<{ user?: string }>;
}) {
  const { user } = await searchParams;

  let users: UserSummary[] = [];
  let result: Retrieval | null = null;
  let error: string | null = null;

  try {
    users = await api.get<UserSummary[]>("/v1/users?limit=60");
    const chosen = user ?? users[0]?.user_id;
    if (chosen) {
      result = await api.get<Retrieval>(`/v1/retrieval/${chosen}?k=20`);
    }
  } catch (caught) {
    error = describeError(caught);
  }

  return (
    <>
      <PageHeader
        title="Vector search"
        subtitle="An HNSW graph index written from scratch, measured live against exhaustive search on the same query."
      />

      {error ? <Notice tone="danger">{error}</Notice> : null}

      {users.length > 0 ? (
        <div className="mb-6">
          <Suspense fallback={null}>
            <UserPicker users={users} selected={result?.user_id ?? ""} basePath="/retrieval" />
          </Suspense>
        </div>
      ) : null}

      {result ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="Recall vs exhaustive"
              value={result.recall_vs_brute_force.toFixed(3)}
              hint="how much of the true top-k it found"
              tone={result.recall_vs_brute_force >= 0.9 ? "good" : "warn"}
            />
            <Stat label="Approximate" value={`${result.approximate_ms.toFixed(2)} ms`} hint="graph traversal" />
            <Stat label="Exhaustive" value={`${result.exact_ms.toFixed(2)} ms`} hint="every vector, no index" />
            <Stat label="Speed-up" value={`${result.speedup.toFixed(1)}x`} hint="at this corpus size" tone="good" />
          </div>

          <div className="mt-4">
            <Notice>
              The point of an approximate index is that the speed-up grows with the corpus while
              recall holds. At a few hundred items exhaustive search is still competitive; at a
              hundred million it is not possible at all.
            </Notice>
          </div>

          <div className="mt-6">
            <Card
              title={`Nearest neighbours for ${result.user_id}`}
              description={`Their genuine interest is ${result.user_topic}. Rows missing from the exhaustive set are the recall cost.`}
            >
              <Table head={["#", "Item", "Similarity", "Topic", "In exhaustive top-k"]}>
                {result.results.map((row, index) => (
                  <tr
                    key={row.item_id}
                    className="border-b border-[var(--color-line)] transition-colors last:border-0 hover:bg-[var(--color-raised)]"
                  >
                    <td className="px-3 py-2.5 tabular-nums text-[var(--color-faint)]">{index + 1}</td>
                    <td className="px-3 py-2.5 font-mono text-xs">{row.item_id}</td>
                    <td className="px-3 py-2.5 tabular-nums">{row.similarity.toFixed(4)}</td>
                    <td className="px-3 py-2.5">
                      <span
                        className={
                          row.on_topic
                            ? "text-sm font-medium text-[var(--color-good)]"
                            : "text-sm text-[var(--color-muted)]"
                        }
                      >
                        {row.topic}
                      </span>
                    </td>
                    <td className="px-3 py-2.5">
                      {row.in_exact_set ? (
                        <Badge value="stable" />
                      ) : (
                        <Badge value="alert" />
                      )}
                    </td>
                  </tr>
                ))}
              </Table>
            </Card>
          </div>
        </>
      ) : null}
    </>
  );
}
