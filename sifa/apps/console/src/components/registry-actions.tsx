"use client";

import { useState, useTransition } from "react";
import { advanceCanary, promoteCandidate, rollbackServing } from "@/lib/actions";
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
          onClick={() => run(advanceCanary)}
          className={buttonClass}
        >
          Advance the canary to live
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
        A promotion trains a new ranker and puts it on a canary for ten percent of users, chosen by hashing the user so each person keeps one model. The rollout guard watches the canary and withdraws it on its own if click through, calibration or latency slip; it can advance to live only once the guard has seen enough traffic and finds it healthy. A rollback withdraws
        the canary if there is one, leaving the live model untouched; with no canary it withdraws the live
        model and restores the previous archived version in the same step.
      </p>
    </div>
  );
}
