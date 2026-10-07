"use client";

import { useState, useTransition } from "react";
import { sendFeedback } from "@/lib/actions";
import { Notice, Select, buttonClass, secondaryButtonClass } from "@/components/ui";

export function FeedbackPanel({
  requestId,
  itemIds,
  outcomeSource
}: {
  requestId: string;
  itemIds: string[];
  outcomeSource: "simulated" | "feedback";
}) {
  const [pending, startTransition] = useTransition();
  const [item, setItem] = useState(itemIds[0] ?? "");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function send(clicked: boolean) {
    startTransition(async () => {
      const outcome = await sendFeedback(requestId, item, clicked);
      setError(outcome.error);
      setMessage(outcome.message);
    });
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-56">
          <Select
            label="Item from this feed"
            name="item"
            placeholder="Item"
            value={item}
            onChange={(event) => setItem(event.target.value)}
            options={itemIds.map((id) => ({ value: id, label: id }))}
          />
        </div>
        <button type="button" className={buttonClass} disabled={pending || !item} onClick={() => send(true)}>
          Record a click
        </button>
        <button
          type="button"
          className={secondaryButtonClass}
          disabled={pending || !item}
          onClick={() => send(false)}
        >
          Record a skip
        </button>
      </div>
      {error ? <Notice tone="danger">{error}</Notice> : null}
      {message ? <Notice tone="good">{message}</Notice> : null}
      <p className="text-xs leading-relaxed text-[var(--color-faint)]">
        Outcomes for this platform are currently{" "}
        <span className="font-medium text-[var(--color-muted)]">
          {outcomeSource === "feedback" ? "taken from feedback" : "simulated"}
        </span>
        .{" "}
        {outcomeSource === "feedback"
          ? "A click counts once per request as the success of its experiment arm and canary window."
          : "In simulated mode the experiment and guard use a computed outcome; feedback still trains the exploration bandit and is stored."}{" "}
        Request {requestId}. Recording the same item twice changes nothing.
      </p>
    </div>
  );
}
