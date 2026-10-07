import { api, describeError } from "@/lib/api";
import type { MovielensEvaluation } from "@/lib/types";
import { Card, Notice, PageHeader, Stat, Table } from "@/components/ui";

const METHODS: Record<string, { label: string; note: string }> = {
  random: { label: "Random", note: "a floor, not a baseline" },
  popularity: { label: "Most popular", note: "same list for everyone" },
  item_knn_cosine: { label: "Item-based nearest neighbours", note: "cosine over co-ratings" },
  sifa_two_tower_exact: { label: "Sifa two-tower, exact scoring", note: "dot product over every item" },
  sifa_two_tower_hnsw: { label: "Sifa two-tower, served by HNSW", note: "the retrieval path in production" }
};

const ORDER = [
  "random",
  "popularity",
  "item_knn_cosine",
  "sifa_two_tower_exact",
  "sifa_two_tower_hnsw"
];

const pct = (value: number) => `${(value * 100).toFixed(2)}%`;

export default async function EvaluationPage() {
  let data: MovielensEvaluation | null = null;
  let error: string | null = null;

  try {
    data = await api.get<MovielensEvaluation>("/v1/evaluation/movielens");
  } catch (caught) {
    error = describeError(caught);
  }

  const best = data
    ? Math.max(...ORDER.map((key) => data.test[key]?.ndcg_at_10 ?? 0))
    : 0;

  return (
    <>
      <PageHeader
        title="Evaluation"
        subtitle="Offline ranking quality on a public dataset, not on the simulator the rest of the console runs on."
      />

      {error ? <Notice tone="danger">{error}</Notice> : null}

      {data ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Users" value={data.users.toLocaleString()} hint="each with a held-out last like" tone="accent" />
            <Stat label="Items ranked" value={data.items.toLocaleString()} hint="the whole catalogue, no sampling" tone="accent" />
            <Stat label="Training interactions" value={data.train_interactions.toLocaleString()} hint="ratings of 4.0 or more" tone="accent" />
            <Stat
              label="Index recall at ef 128"
              value={(data.ann_index.sweep.find((row) => row.ef_search === 128)?.sifa_recall_at_10 ?? 0).toFixed(3)}
              hint="against exact search, same vectors"
              tone="good"
            />
          </div>

          <div className="mt-6">
            <Card
              title="Next-item ranking"
              description={`${data.dataset}. ${data.protocol}.`}
            >
              <Table head={["Method", "Hit rate at 10", "95% interval", "NDCG at 10", "MRR at 10"]}>
                {ORDER.filter((key) => data.test[key]).map((key) => {
                  const row = data.test[key];
                  const method = METHODS[key];
                  return (
                    <tr key={key} className="border-b border-[var(--color-line)] last:border-0">
                      <td className="px-3 py-2.5">
                        <p className="font-medium">{method.label}</p>
                        <p className="text-xs text-[var(--color-muted)]">{method.note}</p>
                      </td>
                      <td className="px-3 py-2.5 tabular-nums">{pct(row.hit_rate_at_10)}</td>
                      <td className="px-3 py-2.5 tabular-nums text-[var(--color-muted)]">
                        {pct(row.hit_rate_ci95_low)} to {pct(row.hit_rate_ci95_high)}
                      </td>
                      <td
                        className={`px-3 py-2.5 tabular-nums ${
                          row.ndcg_at_10 === best ? "font-semibold text-[var(--color-good)]" : ""
                        }`}
                      >
                        {row.ndcg_at_10.toFixed(4)}
                      </td>
                      <td className="px-3 py-2.5 tabular-nums">{row.mrr_at_10.toFixed(4)}</td>
                    </tr>
                  );
                })}
              </Table>
            </Card>
          </div>

          <div className="mt-4">
            <Notice>
              {`Test set of ${data.users} users, so the intervals are wide and several rows overlap: differences between neighbouring methods are not established. ${data.dropped_users_cold_target} users were left out because their held-out item never appears in training. Hyper-parameters were chosen on a separate validation item, then the model was refit and scored once.`}
            </Notice>
          </div>

          <div className="mt-6">
            <Card
              title="Index against FAISS"
              description={`${data.ann_index.vectors.toLocaleString()} item vectors of dimension ${data.ann_index.dimension}, ${data.ann_index.queries} queries, same graph parameters (M 16, efConstruction 200), recall against exact search. Build time ${data.ann_index.sifa_build_seconds.toFixed(1)} s for Sifa's pure-Python index against ${data.ann_index.faiss_build_seconds.toFixed(2)} s for FAISS; exact NumPy scoring takes ${data.ann_index.numpy_exact_p50_ms.toFixed(3)} ms here, so a graph only pays off as the catalogue grows.`}
            >
              <Table
                head={["ef_search", "Sifa recall", "Sifa p50", "Sifa p95", "FAISS recall", "FAISS p50", "FAISS p95"]}
              >
                {data.ann_index.sweep.map((row) => (
                  <tr key={row.ef_search} className="border-b border-[var(--color-line)] last:border-0">
                    <td className="px-3 py-2.5 tabular-nums">{row.ef_search}</td>
                    <td className="px-3 py-2.5 tabular-nums">{row.sifa_recall_at_10.toFixed(3)}</td>
                    <td className="px-3 py-2.5 tabular-nums">{row.sifa_p50_ms.toFixed(2)} ms</td>
                    <td className="px-3 py-2.5 tabular-nums">{row.sifa_p95_ms.toFixed(2)} ms</td>
                    <td className="px-3 py-2.5 tabular-nums">{row.faiss_recall_at_10.toFixed(3)}</td>
                    <td className="px-3 py-2.5 tabular-nums">{row.faiss_p50_ms.toFixed(3)} ms</td>
                    <td className="px-3 py-2.5 tabular-nums">{row.faiss_p95_ms.toFixed(3)} ms</td>
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
