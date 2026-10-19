import { cn } from "@/lib/cn";

/** Placeholder block. Pair with <LoadingRegion> so screen readers hear that content is loading. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn("rounded-md bg-line motion-safe:animate-pulse", className)}
    />
  );
}

export function LoadingRegion({
  label = "Loading",
  children,
}: {
  label?: string;
  children: React.ReactNode;
}) {
  return (
    <div role="status" aria-busy="true">
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
}
