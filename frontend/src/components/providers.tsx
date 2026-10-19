"use client";

import { MotionConfig } from "framer-motion";
import type { ReactNode } from "react";

import { ToastProvider } from "@/components/ui/toast";

export function Providers({ children }: { children: ReactNode }) {
  // reducedMotion="user" makes every animation honor the OS "reduce motion" setting.
  return (
    <MotionConfig reducedMotion="user">
      <ToastProvider>{children}</ToastProvider>
    </MotionConfig>
  );
}
