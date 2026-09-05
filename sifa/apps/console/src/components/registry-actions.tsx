"use client";

import { useState, useTransition } from "react";
import { promoteCandidate, rollbackServing } from "@/lib/actions";
import { Notice, buttonClass, dangerButtonClass } from "@/components/ui";

export function RegistryActions() {
  const [pending, startTransition] = useTransition();
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function run(action: () => Promise<{ error: string | null; message: string | null }>) {
    startTransition(async () => {
      const outcome = await action();
      setError(outcome.error);
      setMessage(outcome.message);
    });
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={pending}
          onClick={() => run(promoteCandidate)}
          className={buttonClass}
        >
          {pending ? "Working…" : "Promote a new candidate"}
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={() => run(rollbackServing)}
          className={dangerButtonClass}
        >
          Roll back what is serving
        </button>
      </div>
      {error ? <Notice tone="danger">{error}</Notice> : null}
      {message ? <Notice tone="good">{message}</Notice> : null}
      <p className="text-xs leading-relaxed text-[var(--color-faint)]">
        A promotion enters shadow, then canary at ten percent of traffic. A rollback demotes
        whatever is serving and restores the previous archived version in the same step.
      </p>
    </div>
  );
}
