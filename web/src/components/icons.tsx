import { useId } from "react";
import type { Severity } from "../types";
import type { StreamStatus } from "../useAlertStream";

/** Rounded square with a heartbeat line, in the blue gradient. */
export function Logo({ className = "logo" }: { className?: string }) {
  const id = useId();
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#0a3cff" />
          <stop offset="1" stopColor="#2e9bff" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill={`url(#${id})`} />
      <path
        d="M5 17h5l3-7 4 13 3-9 2 3h5"
        fill="none"
        stroke="#fff"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

const SEVERITY_ICON: Record<Severity, JSX.Element> = {
  critical: <path d="M8 1.5 15 14H1z M8 6v4 M8 11.6v.4" />,
  warning: <path d="M3 9.5 8 5l5 4.5 M3 13.5 8 9l5 4.5" />,
  info: <circle cx="8" cy="8" r="3" />,
};

/** Severity is never colour alone: each level also has its own icon shape and a label. */
export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className={`sev sev-${severity}-badge`}>
      <svg
        viewBox="0 0 16 16"
        fill={severity === "info" ? "currentColor" : "none"}
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        {SEVERITY_ICON[severity]}
      </svg>
      {severity}
    </span>
  );
}

const STATUS_LABEL: Record<StreamStatus, string> = {
  connecting: "Connecting…",
  open: "Live",
  closed: "Reconnecting…",
};

export function StatusPill({ status }: { status: StreamStatus }) {
  return (
    <span className={`status status-${status}`} role="status">
      <span className="dot" aria-hidden="true" />
      {STATUS_LABEL[status]}
    </span>
  );
}

export function CheckIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="m3 8.5 3 3 7-7" />
    </svg>
  );
}
