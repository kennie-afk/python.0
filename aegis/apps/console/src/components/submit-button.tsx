"use client";

import { useFormStatus } from "react-dom";
import { buttonClass, dangerButtonClass, secondaryButtonClass } from "@/components/ui";

const VARIANTS = {
  primary: buttonClass,
  secondary: secondaryButtonClass,
  danger: dangerButtonClass
};

export function SubmitButton({
  label,
  pendingLabel,
  variant = "primary",
  disabled
}: {
  label: string;
  pendingLabel?: string;
  variant?: keyof typeof VARIANTS;
  disabled?: boolean;
}) {
  const { pending } = useFormStatus();
  return (
    <button type="submit" className={VARIANTS[variant]} disabled={pending || disabled}>
      {pending ? (pendingLabel ?? "Working…") : label}
    </button>
  );
}
