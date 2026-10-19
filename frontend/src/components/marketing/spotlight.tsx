"use client";

import { type ReactNode, useRef } from "react";

/** A soft golden light that follows the pointer. Decoration only; nothing depends on it. */
export function Spotlight({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  return (
    <div
      ref={ref}
      className={`relative ${className}`}
      onPointerMove={(e) => {
        const el = ref.current;
        if (!el) return;
        const rect = el.getBoundingClientRect();
        el.style.setProperty("--mx", `${e.clientX - rect.left}px`);
        el.style.setProperty("--my", `${e.clientY - rect.top}px`);
      }}
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-70"
        style={{
          background:
            "radial-gradient(520px circle at var(--mx, 70%) var(--my, 30%), rgb(236 211 145 / 0.16), transparent 60%)",
        }}
      />
      {children}
    </div>
  );
}
