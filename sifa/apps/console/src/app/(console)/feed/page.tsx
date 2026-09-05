import { Suspense } from "react";
import { api, describeError } from "@/lib/api";
import type { Feed, UserSummary } from "@/lib/types";
import { UserPicker } from "@/components/user-picker";
import { Badge, Card, EmptyState, Meter, Notice, PageHeader, Stat, Table } from "@/components/ui";

export default async function FeedPage({
  searchParams
}: {
  searchParams: Promise<{ user?: string }>;
}) {
  const { user } = await searchParams;

  let users: UserSummary[] = [];
  let feed: Feed | null = null;
  let error: string | null = null;

  try {
    users = await api.get<UserSummary[]>("/v1/users?limit=60");
    const chosen = user ?? users[0]?.user_id;
    if (chosen) {
      feed = await api.get<Feed>(`/v1/feed/${chosen}`);
    }
  } catch (caught) {
    error = describeError(caught);
  }

  return (
    <>
      <PageHeader
        title="Feed explorer"
        subtitle="The whole pipeline for one person: retrieve, rank, diversify, then explore. Every row shows why it is there."
      />

      {error ? <Notice tone="danger">{error}</Notice> : null}

      {users.length > 0 ? (
        <div className="mb-6">
          <Suspense fallback={null}>
            <UserPicker users={users} selected={feed?.user_id ?? ""} basePath="/feed" />
          </Suspense>
        </div>
      ) : null}

      {feed ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="nDCG@10" value={feed.ndcg_at_10.toFixed(3)} hint="against the viewer's true interest" tone="good" />
            <Stat label="Latency" value={`${feed.latency_ms.toFixed(1)} ms`} hint="retrieve, rank and diversify" />
            <Stat label="Retrieved" value={String(feed.retrieved)} hint={`narrowed to ${feed.items.length}`} />
            <Stat
              label="Variant"
              value={feed.variant}
              hint={feed.variant === "control" ? "no diversity applied" : "diversity applied"}
            />
          </div>

          <div className="mt-6">
            <Card
              title={`Ranked feed for ${feed.user_id}`}
              description={`Their genuine interest is ${feed.user_topic}. Rows marked off-topic are deliberate exploration or diversity.`}
            >
              {feed.items.length === 0 ? (
                <EmptyState message="Nothing was retrieved for this user." />
              ) : (
                <Table head={["#", "Item", "Topic", "Score", "Retrieval", "Why"]}>
                  {feed.items.map((item, index) => (
                    <tr
                      key={item.item_id}
                      className="border-b border-[var(--color-line)] transition-colors last:border-0 hover:bg-[var(--color-raised)]"
                    >
                      <td className="px-3 py-2.5 tabular-nums text-[var(--color-faint)]">
                        {index + 1}
                      </td>
                      <td className="px-3 py-2.5 font-mono text-xs">{item.item_id}</td>
                      <td className="px-3 py-2.5">
                        <span
                          className={
                            item.on_topic
                              ? "text-sm font-medium text-[var(--color-good)]"
                              : "text-sm text-[var(--color-muted)]"
                          }
                        >
                          {item.topic}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 w-40">
                        <div className="flex items-center gap-2">
                          <span className="w-12 shrink-0 text-xs tabular-nums">
                            {item.score.toFixed(3)}
                          </span>
                          <Meter value={item.score} />
                        </div>
                      </td>
                      <td className="px-3 py-2.5 text-xs tabular-nums text-[var(--color-muted)]">
                        {item.retrieval_score.toFixed(3)}
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="flex flex-wrap gap-1">
                          {item.source === "exploration" ? <Badge value="explored" /> : null}
                          {item.reasons.slice(-2).map((reason) => (
                            <span
                              key={reason}
                              className="rounded-full bg-[var(--color-raised)] px-2 py-0.5 text-[0.6875rem] text-[var(--color-muted)]"
                            >
                              {reason}
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </Table>
              )}
            </Card>
          </div>

          <p className="mt-4 text-xs leading-relaxed text-[var(--color-faint)]">
            Request {feed.request_id}. Distinct topics in this feed:{" "}
            {String(feed.diagnostics.distinct_topics ?? "?")}. A feed of one topic scores well on
            relevance and badly on everything a person actually wants, which is why diversity is
            applied to the treatment arm.
          </p>
        </>
      ) : null}
    </>
  );
}
