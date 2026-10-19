import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { CATEGORY_LABEL, STATE_LABEL } from "@/lib/issue-text";
import type { Impact, IssueSummary } from "@/lib/types";

import { SeverityBadge } from "./severity-badge";

export function IssueList({
  issues,
  impacts,
}: {
  issues: IssueSummary[];
  impacts?: Record<string, Impact>;
}) {
  return (
    <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
      {issues.map((issue) => (
        <li key={issue.id}>
          <Link
            href={`/issues/${issue.id}`}
            className="flex flex-col gap-2 px-4 py-3 hover:bg-page sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="min-w-0">
              <p className="font-medium break-words text-ink">{issue.title}</p>
              <p className="text-sm text-muted">
                {CATEGORY_LABEL[issue.category] ?? issue.category}
                {issue.component ? ` · ${issue.component}` : ""}
              </p>
              {impacts?.[issue.id] ? (
                <p className="text-xs text-muted">
                  Likely impact (estimate, not measured): {impacts[issue.id].journey_label}
                  {" · "}score {impacts[issue.id].score}
                  {impacts[issue.id].traffic
                    ? ` · ${impacts[issue.id].traffic?.share_percent}% of your uploaded views (your numbers, not verified)`
                    : ""}
                </p>
              ) : null}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {issue.classification === "needs_review" ? (
                <Badge tone="warning">Needs review</Badge>
              ) : null}
              <SeverityBadge severity={issue.severity} />
              {issue.state !== "open" && issue.state !== "needs_review" ? (
                <Badge tone="neutral">{STATE_LABEL[issue.state] ?? issue.state}</Badge>
              ) : null}
            </div>
          </Link>
        </li>
      ))}
    </ul>
  );
}
