import { type SelectHTMLAttributes, useId } from "react";

import { cn } from "@/lib/cn";

export function SelectField({
  label,
  hint,
  error,
  className,
  children,
  hideLabel = false,
  ...props
}: Omit<SelectHTMLAttributes<HTMLSelectElement>, "id"> & {
  label: string;
  hint?: string;
  error?: string;
  hideLabel?: boolean;
}) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className={cn("text-sm font-medium text-ink", hideLabel && "sr-only")}>
        {label}
      </label>
      <select
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={[hintId, errorId].filter(Boolean).join(" ") || undefined}
        className={cn(
          "h-11 rounded-md border bg-surface px-3 text-base text-ink lg:h-10 lg:text-sm",
          "disabled:cursor-not-allowed disabled:bg-page disabled:text-muted",
          error ? "border-danger" : "border-line hover:border-muted",
          className,
        )}
        {...props}
      >
        {children}
      </select>
      {hint ? (
        <p id={hintId} className="text-xs text-muted">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="text-xs font-medium text-danger-text">
          {error}
        </p>
      ) : null}
    </div>
  );
}
