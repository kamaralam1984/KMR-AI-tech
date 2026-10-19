import type { ReactNode } from "react";

/** The wrapper scrolls horizontally on narrow screens so the page itself never does. */
export function Table({ caption, children }: { caption: string; children: ReactNode }) {
  return (
    // Focusable so keyboard users can scroll it; the label tells a screen reader what it is.
    <div
      role="region"
      aria-label={caption}
      tabIndex={0}
      className="overflow-x-auto rounded-md border border-line"
    >
      <table className="w-full min-w-[17rem] border-collapse text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        {children}
      </table>
    </div>
  );
}

export function THead({ children }: { children: ReactNode }) {
  return <thead className="bg-page text-xs uppercase tracking-wide text-muted">{children}</thead>;
}

export function TR({ children }: { children: ReactNode }) {
  return <tr className="border-t border-line first:border-t-0">{children}</tr>;
}

export function TH({ children }: { children: ReactNode }) {
  return (
    <th scope="col" className="px-4 py-2.5 font-medium">
      {children}
    </th>
  );
}

export function TD({ children }: { children: ReactNode }) {
  return <td className="px-4 py-3 align-top text-ink">{children}</td>;
}
