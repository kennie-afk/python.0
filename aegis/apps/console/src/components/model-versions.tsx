"use client";

import { useState, useTransition } from "react";
import { Badge, Card, Notice, Table, rowClass, secondaryButtonClass } from "@/components/ui";
import { activateModelVersion, rollbackModelVersion } from "@/lib/analytics-actions";
import type { ModelVersionView } from "@/lib/types";

export function ModelVersions({ versions }: { versions: ModelVersionView[] }) {
  const [pending, start] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function run(action: () => Promise<{ error: string | null }>) {
    start(async () => setError((await action()).error));
  }

  return (
    <Card
      title="Model versions"
      description="Every training run is kept. A model passes a determinism, drift and adverse-impact gate before it can serve; a blocked one is stored but cannot be activated."
      actions={
        <button type="button" className={secondaryButtonClass} disabled={pending || versions.length < 2} onClick={() => run(rollbackModelVersion)}>
          Roll back
        </button>
      }
    >
      {error ? <div className="mb-3"><Notice tone="danger">{error}</Notice></div> : null}
      <Table head={["Version", "Gate", "Trained on", "Why", "By", "When", ""]}>
        {versions.map((version) => {
          const findings = version.fidelity.findings ?? [];
          return (
            <tr key={version.version} className={rowClass}>
              <td className="px-3 py-2.5 tabular-nums">
                v{version.version} {version.active ? <Badge value="active" /> : null}
              </td>
              <td className="px-3 py-2.5"><Badge value={version.gate} /></td>
              <td className="px-3 py-2.5 text-xs text-[var(--color-muted)]">{version.rows} rows, {version.positives} leavers</td>
              <td className="max-w-[20rem] px-3 py-2.5 text-xs text-[var(--color-faint)]">
                {findings.length > 0 ? findings.slice(0, 2).join("; ") : (version.fidelity.notes ?? [])[0] ?? "no findings"}
              </td>
              <td className="px-3 py-2.5 text-xs text-[var(--color-muted)]">{version.created_by}</td>
              <td className="whitespace-nowrap px-3 py-2.5 text-xs text-[var(--color-faint)]">{new Date(version.created_at).toLocaleString()}</td>
              <td className="px-3 py-2.5 text-right">
                {!version.active && version.gate !== "BLOCK" ? (
                  <button type="button" className={secondaryButtonClass} disabled={pending} onClick={() => run(() => activateModelVersion(version.version))}>
                    Activate
                  </button>
                ) : null}
              </td>
            </tr>
          );
        })}
      </Table>
    </Card>
  );
}
