import { api, describeError } from "@/lib/api";
import type { RegistryEntry } from "@/lib/types";
import { RegistryActions } from "@/components/registry-actions";
import { Badge, Card, Notice, PageHeader, Table } from "@/components/ui";

export default async function RegistryPage() {
  let entries: RegistryEntry[] = [];
  let error: string | null = null;

  try {
    entries = await api.get<RegistryEntry[]>("/v1/registry");
  } catch (caught) {
    error = describeError(caught);
  }

  const newest = entries[entries.length - 1];

  return (
    <>
      <PageHeader
        title="Registry"
        subtitle="Every version, the stage it reached and how it got there. Nothing reaches live without passing through shadow and canary first."
      />

      {error ? <Notice tone="danger">{error}</Notice> : null}

      <div className="mb-6">
        <Card title="Rollout controls">
          <RegistryActions />
        </Card>
      </div>

      {entries.length > 0 ? (
        <div className="space-y-6">
          <Card title="Versions">
            <Table head={["Version", "Stage", "Traffic", "AUC", "Created"]}>
              {[...entries].reverse().map((entry) => (
                <tr
                  key={entry.label}
                  className="border-b border-[var(--color-line)] transition-colors last:border-0 hover:bg-[var(--color-raised)]"
                >
                  <td className="px-3 py-2.5 font-mono text-xs">{entry.label}</td>
                  <td className="px-3 py-2.5">
                    <Badge value={entry.stage} />
                  </td>
                  <td className="px-3 py-2.5 tabular-nums">
                    {(entry.traffic * 100).toFixed(0)}%
                  </td>
                  <td className="px-3 py-2.5 tabular-nums text-[var(--color-muted)]">
                    {entry.metrics.auc !== undefined ? entry.metrics.auc.toFixed(4) : "—"}
                  </td>
                  <td className="px-3 py-2.5 whitespace-nowrap text-xs text-[var(--color-faint)]">
                    {new Date(entry.created_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </Table>
          </Card>

          {newest ? (
            <Card title={`History of ${newest.label}`} description="Append only, in order.">
              <ol className="relative space-y-4 border-l border-[var(--color-line)] pl-6">
                {newest.history.map((event, index) => (
                  <li key={`${event.at}-${index}`} className="relative">
                    <span className="absolute -left-[1.6875rem] top-1.5 h-2.5 w-2.5 rounded-full bg-[var(--color-brand)] ring-4 ring-[var(--color-surface)]" />
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge value={event.stage} />
                      <span className="text-xs text-[var(--color-faint)]">
                        {new Date(event.at).toLocaleString()}
                      </span>
                    </div>
                    {event.reason ? (
                      <p className="mt-1 text-sm text-[var(--color-muted)]">{event.reason}</p>
                    ) : null}
                  </li>
                ))}
              </ol>
            </Card>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
