"use client";

import { useActionState } from "react";
import { SubmitButton } from "@/components/submit-button";
import { Field, Notice, inputClass } from "@/components/ui";
import { signIn } from "@/lib/actions";
import { idleForm } from "@/lib/form-state";

export function SignInForm() {
  const [state, action] = useActionState(signIn, idleForm);

  return (
    <form action={action} className="space-y-4">
      <Field label="Tenant API key" hint="Issued by aegis-provision when your tenant was created.">
        <input
          name="api_key"
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder="aeg_…"
          className={inputClass}
        />
      </Field>
      {state.error ? <Notice tone="danger">{state.error}</Notice> : null}
      <SubmitButton label="Sign in" pendingLabel="Checking…" />
    </form>
  );
}
