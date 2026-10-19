"use client";

import { X } from "lucide-react";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from "react";

import { cn } from "@/lib/cn";

import { Button } from "./button";
import type { Tone } from "./badge";

type ToastInput = { tone?: Tone; title: string; description?: string };
type ToastItem = ToastInput & { id: number };

const ToastContext = createContext<((toast: ToastInput) => void) | null>(null);

const toneBorder: Record<Tone, string> = {
  neutral: "border-l-muted",
  info: "border-l-primary",
  success: "border-l-success",
  warning: "border-l-warning",
  danger: "border-l-danger",
};

export function useToast(): (toast: ToastInput) => void {
  const push = useContext(ToastContext);
  if (!push) throw new Error("useToast must be used inside <ToastProvider>");
  return push;
}

function ToastView({ toast, onDismiss }: { toast: ToastItem; onDismiss: (id: number) => void }) {
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(toast.id), 6000);
    return () => clearTimeout(timer);
  }, [toast.id, onDismiss]);

  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-md border border-line border-l-4 bg-surface p-3 shadow-pop",
        toneBorder[toast.tone ?? "info"],
      )}
    >
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-ink">{toast.title}</p>
        {toast.description ? (
          <p className="mt-0.5 text-sm text-muted">{toast.description}</p>
        ) : null}
      </div>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => onDismiss(toast.id)}
        aria-label="Dismiss notification"
      >
        <X className="size-4" aria-hidden="true" />
      </Button>
    </div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback((toast: ToastInput) => {
    setToasts((current) => [...current, { ...toast, id: Date.now() + Math.random() }]);
  }, []);

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div
        aria-live="polite"
        // On phones, sit above the bottom tab bar (about 3.5rem plus the safe-area inset).
        className="fixed right-4 bottom-[calc(4.5rem+env(safe-area-inset-bottom))] z-50 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2 lg:bottom-4"
      >
        {toasts.map((toast) => (
          <ToastView key={toast.id} toast={toast} onDismiss={dismiss} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}
