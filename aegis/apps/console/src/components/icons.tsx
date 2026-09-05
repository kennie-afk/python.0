import type { ReactNode } from "react";

const base = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const
};

export type IconName =
  | "home"
  | "runs"
  | "workflows"
  | "screening"
  | "retention"
  | "compliance"
  | "ledger";

export function Icon({ name, className }: { name: IconName; className?: string }) {
  const paths: Record<IconName, ReactNode> = {
    home: (
      <>
        <path d="M4 10.5 12 4l8 6.5" />
        <path d="M6 10v9h12v-9" />
      </>
    ),
    runs: (
      <>
        <circle cx="6" cy="6.5" r="2" />
        <circle cx="6" cy="17.5" r="2" />
        <path d="M6 8.5v7" />
        <path d="M11 6.5h9M11 12h9M11 17.5h9" />
      </>
    ),
    workflows: (
      <>
        <rect x="3.5" y="4" width="7" height="6" rx="1.5" />
        <rect x="13.5" y="14" width="7" height="6" rx="1.5" />
        <path d="M10.5 7h3.5a3 3 0 0 1 3 3v4" />
      </>
    ),
    screening: (
      <>
        <circle cx="10" cy="8" r="3.2" />
        <path d="M4 20a6 6 0 0 1 12 0" />
        <path d="M17 6.5l2 2 3.5-3.5" />
      </>
    ),
    retention: (
      <>
        <path d="M4 17l5-5 4 3 7-8" />
        <path d="M16 7h4v4" />
      </>
    ),
    compliance: (
      <>
        <path d="M12 3.5 5 6.5v5c0 4.3 2.9 7.9 7 9 4.1-1.1 7-4.7 7-9v-5z" />
        <path d="M9.5 12l1.8 1.8 3.4-3.6" />
      </>
    ),
    ledger: (
      <>
        <rect x="4.5" y="3.5" width="15" height="17" rx="2" />
        <path d="M8.5 8h7M8.5 12h7M8.5 16h4" />
      </>
    )
  };

  return (
    <svg {...base} className={className} aria-hidden="true">
      {paths[name]}
    </svg>
  );
}
