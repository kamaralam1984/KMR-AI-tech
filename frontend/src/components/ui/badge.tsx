import {
  CircleAlert,
  CircleCheck,
  CircleHelp,
  Info,
  type LucideIcon,
  OctagonAlert,
} from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export type Tone = "neutral" | "info" | "success" | "warning" | "danger";

const toneStyles: Record<Tone, string> = {
  neutral: "border-line bg-page text-ink",
  info: "border-primary/30 bg-primary-soft text-primary-text",
  success: "border-success/30 bg-success-soft text-success-text",
  warning: "border-warning/40 bg-warning-soft text-warning-text",
  danger: "border-danger/30 bg-danger-soft text-danger-text",
};

// Every tone has its own icon, so status is never conveyed by color alone.
const toneIcons: Record<Tone, LucideIcon> = {
  neutral: CircleHelp,
  info: Info,
  success: CircleCheck,
  warning: CircleAlert,
  danger: OctagonAlert,
};

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  const Icon = toneIcons[tone];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        toneStyles[tone],
      )}
    >
      <Icon className="size-3.5 shrink-0" aria-hidden="true" />
      {children}
    </span>
  );
}
