import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export function Card({
  title,
  titleAs: Heading = "h2",
  description,
  children,
  className,
}: {
  title?: string;
  /** Use "h1" when the card is the page's main content (for example the sign-in form). */
  titleAs?: "h1" | "h2";
  description?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn("min-w-0 rounded-lg border border-line bg-surface shadow-card", className)}
    >
      {title ? (
        <header className="border-b border-line px-5 py-4">
          <Heading className="text-base font-semibold text-ink">{title}</Heading>
          {description ? <p className="mt-0.5 text-sm text-muted">{description}</p> : null}
        </header>
      ) : null}
      <div className="p-5">{children}</div>
    </section>
  );
}
