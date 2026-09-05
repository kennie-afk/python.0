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
  | "feed"
  | "search"
  | "model"
  | "registry"
  | "experiment"
  | "drift"
  | "load";

export function Icon({ name, className }: { name: IconName; className?: string }) {
  const paths: Record<IconName, ReactNode> = {
    home: (
      <>
        <path d="M4 10.5 12 4l8 6.5" />
        <path d="M6 10v9h12v-9" />
      </>
    ),
    feed: (
      <>
        <rect x="4" y="4" width="16" height="5" rx="1.5" />
        <rect x="4" y="12" width="16" height="3" rx="1" />
        <rect x="4" y="17" width="10" height="3" rx="1" />
      </>
    ),
    search: (
      <>
        <circle cx="11" cy="11" r="6.5" />
        <path d="M20 20l-4.2-4.2" />
      </>
    ),
    model: (
      <>
        <circle cx="6" cy="7" r="2.2" />
        <circle cx="6" cy="17" r="2.2" />
        <circle cx="18" cy="12" r="2.2" />
        <path d="M8.2 7.9 15.8 11.2M8.2 16.1 15.8 12.8" />
      </>
    ),
    registry: (
      <>
        <rect x="3.5" y="4" width="17" height="5" rx="1.5" />
        <rect x="3.5" y="12" width="17" height="8" rx="1.5" />
        <path d="M7 16h5" />
      </>
    ),
    experiment: (
      <>
        <path d="M9 3v6.5L4.6 17A2 2 0 0 0 6.3 20h11.4a2 2 0 0 0 1.7-3L15 9.5V3" />
        <path d="M8 3h8M7.5 14h9" />
      </>
    ),
    drift: (
      <>
        <path d="M3 17c3 0 3-8 6-8s3 8 6 8 3-8 6-8" />
        <path d="M3 21h18" />
      </>
    ),
    load: (
      <>
        <path d="M4 19V9M9.5 19V5M15 19v-7M20.5 19v-4" />
      </>
    )
  };

  return (
    <svg {...base} className={className} aria-hidden="true">
      {paths[name]}
    </svg>
  );
}
