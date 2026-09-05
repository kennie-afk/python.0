"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useTransition } from "react";
import { Select } from "@/components/ui";
import type { UserSummary } from "@/lib/types";

export function UserPicker({
  users,
  selected,
  basePath
}: {
  users: UserSummary[];
  selected: string;
  basePath: string;
}) {
  const router = useRouter();
  const params = useSearchParams();
  const [pending, startTransition] = useTransition();

  return (
    <div className="w-72">
      <Select
        label="Viewer"
        hint={pending ? "Loading their feed…" : "Pick anyone in the corpus."}
        value={selected}
        placeholder="Choose a user"
        options={users.map((user) => ({
          value: user.user_id,
          label: `${user.user_id} · ${user.topic} · ${user.clicks} clicks`
        }))}
        onChange={(event) => {
          const next = new URLSearchParams(params.toString());
          next.set("user", event.target.value);
          startTransition(() => router.push(`${basePath}?${next.toString()}`));
        }}
      />
    </div>
  );
}
