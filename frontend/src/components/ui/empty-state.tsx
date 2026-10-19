import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  badge,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  action?: ReactNode;
  badge?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center rounded-lg border border-dashed border-line bg-surface px-6 py-12 text-center">
      <span className="flex size-12 items-center justify-center rounded-full bg-primary-soft text-primary-text">
        <Icon className="size-6" aria-hidden="true" />
      </span>
      {badge ? <div className="mt-4">{badge}</div> : null}
      <h2 className="mt-3 text-base font-semibold text-ink">{title}</h2>
      <p className="mt-1 max-w-md text-sm text-muted">{description}</p>
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}
