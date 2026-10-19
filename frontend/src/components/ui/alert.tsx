import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

import { type Tone } from "./badge";

const styles: Record<Tone, string> = {
  neutral: "border-line bg-surface text-ink",
  info: "border-primary/30 bg-primary-soft text-ink",
  success: "border-success/30 bg-success-soft text-ink",
  warning: "border-warning/40 bg-warning-soft text-ink",
  danger: "border-danger/30 bg-danger-soft text-ink",
};

const titleStyles: Record<Tone, string> = {
  neutral: "text-ink",
  info: "text-primary-text",
  success: "text-success-text",
  warning: "text-warning-text",
  danger: "text-danger-text",
};

export function Alert({
  tone = "info",
  title,
  children,
}: {
  tone?: Tone;
  title: string;
  children?: ReactNode;
}) {
  const urgent = tone === "danger" || tone === "warning";
  return (
    <div
      role={urgent ? "alert" : "status"}
      className={cn("rounded-md border p-4 text-sm", styles[tone])}
    >
      <p className={cn("font-semibold", titleStyles[tone])}>{title}</p>
      {children ? <div className="mt-1 text-ink">{children}</div> : null}
    </div>
  );
}
