import { OctagonAlert } from "lucide-react";
import type { ReactNode } from "react";

export function ErrorState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center rounded-lg border border-danger/30 bg-danger-soft px-6 py-10 text-center"
    >
      <OctagonAlert className="size-8 text-danger-text" aria-hidden="true" />
      <h2 className="mt-3 text-base font-semibold text-danger-text">{title}</h2>
      <p className="mt-1 max-w-md text-sm text-ink">{description}</p>
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}
