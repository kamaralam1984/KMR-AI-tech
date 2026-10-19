import { type TextareaHTMLAttributes, useId } from "react";

import { cn } from "@/lib/cn";

export function TextAreaField({
  label,
  hint,
  error,
  className,
  ...props
}: Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id"> & {
  label: string;
  hint?: string;
  error?: string;
}) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-ink">
        {label}
      </label>
      <textarea
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={[hintId, errorId].filter(Boolean).join(" ") || undefined}
        className={cn(
          "min-h-24 rounded-md border bg-surface px-3 py-2 text-base text-ink placeholder:text-muted lg:text-sm",
          error ? "border-danger" : "border-line hover:border-muted",
          className,
        )}
        {...props}
      />
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
