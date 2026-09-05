import Link from "next/link";
import { notFound } from "next/navigation";
import { ApiError, api, describeError } from "@/lib/api";
import { requireSession } from "@/lib/session";
import { ApprovalActions, RetryAction } from "@/components/step-actions";
import { Badge, Card, KeyValue, Notice, PageHeader, secondaryButtonClass } from "@/components/ui";
import type { RunView, WorkflowCatalogue } from "@/lib/types";

const DOT: Record<string, string> = {
  COMPLETED: "bg-[var(--color-good)]",
  SKIPPED: "bg-[var(--color-faint)]",
  PENDING: "bg-[#cddcde]",
  AWAITING_APPROVAL: "bg-[var(--color-warn)]",
  AWAITING_EXTERNAL: "bg-[var(--color-warn)]",
  FAILED: "bg-[var(--color-danger)]",
  DENIED: "bg-[var(--color-danger)]",
  REJECTED: "bg-[var(--color-danger)]"
};

export default async function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  const session = await requireSession();

  let run: RunView;
  let catalogue: WorkflowCatalogue = {};
  try {
    [run, catalogue] = await Promise.all([
      api.get<RunView>(`/v1/runs/${runId}`, session.token),
      api.get<WorkflowCatalogue>("/v1/workflows")
    ]);
  } catch (caught) {
    if (caught instanceof ApiError && caught.status === 404) {
      notFound();
    }
    return (
      <>
        <PageHeader title="Run" />
        <Notice tone="danger">{describeError(caught)}</Notice>
      </>
    );
  }

  const definition = catalogue[run.workflow];
  const contextEntries = Object.entries(run.context).map(
    ([key, value]) =>
      [key.replaceAll("_", " "), Array.isArray(value) ? value.join(", ") : String(value)] as [
        string,
        string
      ]
  );

  return (
    <>
      <PageHeader
        title={run.subject_id}
        subtitle={`${run.workflow.replaceAll("_", " ")} · run ${run.run_id.slice(0, 8)}`}
        actions={
          <Link href="/runs" className={secondaryButtonClass}>
            All runs
          </Link>
        }
      />

      <div className="mb-6 flex items-center gap-3">
        <Badge value={run.status} />
        {run.pending_approvals.length > 0 ? (
          <span className="text-[0.8125rem] text-[var(--color-muted)]">
            waiting on {run.pending_approvals.join(", ").replaceAll("_", " ")}
          </span>
        ) : null}
      </div>

      {run.status === "FAILED" ? (
        <div className="mb-6">
          <Notice tone="danger">
            A step failed. Nothing further will run until it is retried or the run is abandoned.
          </Notice>
        </div>
      ) : null}

      <Card title="Steps" description="What the agent did, in order, and why it stopped.">
        <ol className="relative space-y-6 border-l border-[var(--color-line)] pl-6">
          {run.steps.map((step) => {
            const spec = definition?.steps.find((item) => item.key === step.key);
            return (
              <li key={step.key} className="group relative">
                <span
                  className={`absolute -left-[1.6875rem] top-1.5 h-2.5 w-2.5 rounded-full ring-4 ring-[var(--color-surface)] ${
                    DOT[step.status] ?? "bg-[#cddcde]"
                  }`}
                />
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">{step.key.replaceAll("_", " ")}</span>
                  <Badge value={step.status} />
                  {step.irreversible ? (
                    <span className="text-xs text-[var(--color-danger)]">irreversible</span>
                  ) : null}
                </div>
                <p className="mt-1 text-sm text-[var(--color-muted)] transition-colors duration-150 group-hover:text-[var(--color-ink)]">
                  {step.description}
                </p>

                {step.reasons.length > 0 ? (
                  <ul className="mt-2 space-y-1">
                    {step.reasons.map((reason) => (
                      <li key={reason} className="text-xs text-[var(--color-faint)]">
                        {reason}
                      </li>
                    ))}
                  </ul>
                ) : null}

                {step.approver ? (
                  <p className="mt-1.5 text-xs text-[var(--color-muted)]">
                    signed off by {step.approver}
                  </p>
                ) : null}

                {step.status === "AWAITING_APPROVAL" ? (
                  <ApprovalActions runId={run.run_id} step={step} approver={session.subject} />
                ) : null}

                {step.retryable ? (
                  <RetryAction
                    runId={run.run_id}
                    step={step}
                    actor={session.subject}
                    amendable={spec?.requires_context ?? []}
                  />
                ) : null}

                {step.status === "FAILED" && !step.retryable ? (
                  <p className="mt-2 text-xs text-[var(--color-danger)]">
                    This step has been attempted {step.attempts} times. It needs a change of
                    approach rather than another attempt.
                  </p>
                ) : null}
              </li>
            );
          })}
        </ol>
      </Card>

      {contextEntries.length > 0 ? (
        <div className="mt-6">
          <Card
            title="Run context"
            description="What was supplied up front, plus anything the steps produced."
          >
            <KeyValue items={contextEntries} />
          </Card>
        </div>
      ) : null}
    </>
  );
}
