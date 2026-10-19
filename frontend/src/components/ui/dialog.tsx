"use client";

import { X } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef } from "react";

import { Button } from "./button";

/**
 * Modal built on the native <dialog> element, which provides the focus trap, Escape to close
 * and inert background. The parent owns the `open` state.
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children?: ReactNode;
  footer?: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      aria-describedby={description ? descriptionId : undefined}
      onClose={onClose}
      onClick={(event) => {
        // A click on the backdrop targets the dialog element itself.
        if (event.target === ref.current) onClose();
      }}
      className="m-0 mt-auto w-full max-w-none rounded-t-xl rounded-b-none border border-line bg-surface p-0 pb-[env(safe-area-inset-bottom)] text-ink shadow-pop sm:m-auto sm:w-[min(32rem,calc(100vw-2rem))] sm:rounded-lg sm:pb-0"
    >
      <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
        <div>
          <h2 id={titleId} className="text-base font-semibold">
            {title}
          </h2>
          {description ? (
            <p id={descriptionId} className="mt-1 text-sm text-muted">
              {description}
            </p>
          ) : null}
        </div>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close dialog">
          <X className="size-4" aria-hidden="true" />
        </Button>
      </div>
      {children ? <div className="px-5 py-4 text-sm">{children}</div> : null}
      {footer ? (
        <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>
      ) : null}
    </dialog>
  );
}
