import Link from "next/link";

import { Card } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { SEVERITY_LABEL, SEVERITY_ORDER } from "@/lib/issue-text";
import { serverGet } from "@/lib/session";
import type { DashboardData, Severity } from "@/lib/types";

function Stat({
  label,
  value,
  href,
  note,
}: {
  label: string;
  value: number;
  href?: string;
  note?: string;
}) {
  return (
    <div className="relative rounded-lg border border-line p-4 hover:bg-page">
      <dt className="text-sm text-muted">
        {href ? (
          // The link covers the whole tile, so the tile is one big target for mouse and touch.
          <Link
            href={href}
            className="inline-flex min-h-10 items-center after:absolute after:inset-0 after:rounded-lg"
          >
            {label}
          </Link>
        ) : (
          label
        )}
      </dt>
      <dd className="mt-1 text-2xl font-semibold text-ink">{value}</dd>
      {note ? <dd className="mt-0.5 text-xs text-muted">{note}</dd> : null}
    </div>
  );
}

/** Every number here is a count of stored records for the active organization. */
export async function Metrics({ orgId }: { orgId: string }) {
  const result = await serverGet<DashboardData>(`/organizations/${orgId}/dashboard`);
  if (result.kind !== "ok") {
    return (
      <ErrorState
        title="Cannot load your numbers"
        description="Reload the page. If this keeps happening, check that the API is running."
      />
    );
  }
  const d = result.data;
  const severities = SEVERITY_ORDER.filter((s) => d.issues.open_by_severity[s]).map(
    (s) => `${d.issues.open_by_severity[s]} ${SEVERITY_LABEL[s as Severity].toLowerCase()}`,
  );
  return (
    <div className="flex flex-col gap-4">
      <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Projects" value={d.projects} href="/projects" />
        <Stat
          label="Scans completed"
          value={d.scans.completed}
          href="/scans"
          note={d.scans.in_progress ? `${d.scans.in_progress} in progress` : undefined}
        />
        <Stat
          label="Open issues"
          value={d.issues.open + d.issues.needs_review}
          href="/issues"
          note={severities.length ? severities.join(", ") : "None open"}
        />
        <Stat
          label="Awaiting your decision"
          value={d.repairs.awaiting_decision}
          href="/approvals"
        />
        <Stat label="Branches created" value={d.repairs.branches_created} href="/repairs" />
        <Stat label="Verified Fixed" value={d.verifications.fixed} />
        <Stat label="Not Fixed" value={d.verifications.not_fixed} />
        <Stat label="Needs Review" value={d.verifications.needs_review} />
      </dl>
      <p className="text-xs text-muted">
        {d.scans.last_completed_at
          ? `Last completed scan: ${new Date(d.scans.last_completed_at).toLocaleString()}.`
          : "No scan has completed yet."}{" "}
        Counts come from your stored records, nothing else.
      </p>
    </div>
  );
}

export function MetricsCard({ orgId }: { orgId: string }) {
  return (
    <Card title="Your workspace in numbers">
      <Metrics orgId={orgId} />
    </Card>
  );
}
