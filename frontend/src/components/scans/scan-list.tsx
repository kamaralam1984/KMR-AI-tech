import Link from "next/link";

import { formatWhen } from "@/lib/scan-text";
import type { Scan } from "@/lib/types";

import { StatusBadge } from "./status-badge";

export function ScanList({ scans, showProject = true }: { scans: Scan[]; showProject?: boolean }) {
  return (
    <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
      {scans.map((scan) => (
        <li key={scan.id}>
          <Link
            href={`/scans/${scan.id}`}
            className="flex flex-col gap-1 px-4 py-3 hover:bg-page sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="min-w-0">
              <p className="truncate font-medium text-ink">
                {showProject && scan.project_name ? `${scan.project_name} · ` : ""}
                {new URL(scan.site_url).host}
              </p>
              <p className="text-sm text-muted">
                {formatWhen(scan.created_at)}
                {scan.summary.pages_scanned !== undefined
                  ? ` · ${scan.summary.pages_scanned} page${scan.summary.pages_scanned === 1 ? "" : "s"}`
                  : ""}
              </p>
            </div>
            <StatusBadge status={scan.status} />
          </Link>
        </li>
      ))}
    </ul>
  );
}
