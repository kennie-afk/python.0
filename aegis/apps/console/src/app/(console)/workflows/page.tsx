import { api, describeError } from "@/lib/api";
import { StartRunForm } from "@/components/start-run-form";
import { Badge, Card, Notice, PageHeader } from "@/components/ui";
import type { WorkflowCatalogue } from "@/lib/types";

export default async function WorkflowsPage() {
  let catalogue: WorkflowCatalogue = {};
  let error: string | null = null;

  try {
    catalogue = await api.get<WorkflowCatalogue>("/v1/workflows");
  } catch (caught) {
    error = describeError(caught);
  }

  if (error) {
    return (
      <>
        <PageHeader title="Workflows" />
        <Notice tone="danger">{error}</Notice>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Workflows"
        subtitle="What the agents can do, step by step, and which steps can never be taken without a person."
      />

      <div className="mb-6">
        <Card title="Start a run" description="Pick a workflow and give it what its steps need.">
          <StartRunForm catalogue={catalogue} />
        </Card>
      </div>

      <div className="space-y-6">
        {Object.values(catalogue).map((workflow) => (
          <Card
            key={workflow.name}
            title={workflow.name.replaceAll("_", " ")}
            description={`${workflow.steps.length} steps · requires ${
              workflow.required_context.length > 0
                ? workflow.required_context.join(", ")
                : "no upfront context"
            }`}
          >
            <ol className="space-y-3">
              {workflow.steps.map((step, index) => (
                <li
                  key={step.key}
                  className="-mx-2 flex gap-3 rounded-md px-2 py-1.5 transition-colors duration-150 hover:bg-[var(--color-raised)]"
                >
                  <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--color-raised)] text-xs font-medium tabular-nums text-[var(--color-muted)]">
                    {index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium">{step.key.replaceAll("_", " ")}</span>
                      {step.irreversible ? (
                        <span className="inline-flex items-center rounded-full bg-[var(--color-danger-soft)] px-2 py-0.5 text-xs font-medium text-[var(--color-danger)]">
                          always needs a person
                        </span>
                      ) : null}
                      {step.optional ? <Badge value="optional" /> : null}
                    </div>
                    <p className="mt-0.5 text-sm text-[var(--color-muted)]">{step.description}</p>
                    {step.requires_context.length > 0 ? (
                      <p className="mt-1 text-xs text-[var(--color-faint)]">
                        needs {step.requires_context.join(", ")}
                      </p>
                    ) : null}
                  </div>
                </li>
              ))}
            </ol>
          </Card>
        ))}
      </div>
    </>
  );
}
