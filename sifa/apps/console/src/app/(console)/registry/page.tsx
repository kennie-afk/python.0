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

          <Card title="Release history" description="Append only, in order. Newest version first; open a version to read how it got where it is.">
            <div className="space-y-2">
              {[...entries].reverse().map((entry, position) => (
                <details key={entry.label} open={position === 0}
                  className="rounded-md border border-[var(--color-line)] px-3 py-2">
                  <summary className="flex cursor-pointer items-center gap-3 text-sm font-medium">
                    <span className="font-mono text-xs">{entry.label}</span>
                    <Badge value={entry.stage} />
                    <span className="text-xs font-normal text-[var(--color-faint)]">{entry.history.length} events</span>
                  </summary>
                  <ol className="relative mt-3 space-y-4 border-l border-[var(--color-line)] pl-6">
                    {entry.history.map((event, index) => (
                      <li key={`${event.at}-${index}`} className="relative">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge value={event.stage} />
                          <span className="text-xs text-[var(--color-faint)]">{new Date(event.at).toLocaleString()}</span>
                        </div>
                        {event.reason ? <p className="mt-1 text-sm text-[var(--color-muted)]">{event.reason}</p> : null}
                      </li>
                    ))}
                  </ol>
                </details>
              ))}
            </div>
          </Card>
        </div>
      ) : null}
    </>
  );
}
