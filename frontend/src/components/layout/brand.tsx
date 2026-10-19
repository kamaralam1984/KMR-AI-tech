import { ScanSearch } from "lucide-react";

export function Brand() {
  return (
    <span className="flex items-center gap-2.5">
      <span className="flex size-8 items-center justify-center rounded-md bg-navy-950 text-gold-400 ring-1 ring-gold-500/60">
        <ScanSearch className="size-4.5" aria-hidden="true" />
      </span>
      <span className="font-display text-xl font-semibold tracking-wide">KMR AI</span>
    </span>
  );
}
