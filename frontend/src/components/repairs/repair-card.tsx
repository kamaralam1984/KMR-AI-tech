import Link from "next/link";

import { Alert } from "@/components/ui/alert";
import { Badge, type Tone } from "@/components/ui/badge";
import { findingText, REPAIR_STATUS_LABEL, shortSha, sourceNote } from "@/lib/repair-text";
import type { Repair, RepairStatus } from "@/lib/types";

import { DiffView } from "./diff-view";

const TONE: Record<RepairStatus, Tone> = {
  proposed: "info",
  no_fix: "neutral",
  rejected: "danger",
  superseded: "neutral",
};

/** One repair plan: what it would change, where it came from, and why it may have been refused. */
export function RepairCard({
  repair,
  showIssueLink = false,
}: {
  repair: Repair;
  showIssueLink?: boolean;
}) {
  return (
    <article
      aria-label={`Repair plan ${shortSha(repair.id)}`}
      className="flex flex-col gap-3 rounded-lg border border-line p-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={TONE[repair.status]}>{REPAIR_STATUS_LABEL[repair.status]}</Badge>
        <span className="text-xs text-muted">
          {new Date(repair.created_at).toLocaleString()} · based on {repair.base_branch}@
          {shortSha(repair.base_sha)}
        </span>
        {showIssueLink ? (
          <Link
            href={`/issues/${repair.issue_id}`}
            className="text-sm font-medium text-primary-text underline underline-offset-2"
          >
            View the issue
          </Link>
        ) : null}
      </div>
      <p className="text-xs text-muted">{sourceNote(repair)}</p>
      {repair.explanation ? <p className="text-sm text-ink">{repair.explanation}</p> : null}
      {repair.findings.length > 0 ? (
        <Alert tone="danger" title="This change was refused">
          <ul className="list-disc pl-5">
            {repair.findings.map((f, i) => (
              <li key={i}>
                {findingText(f.rule)}
                {f.path ? ` (${f.path})` : ""}
              </li>
            ))}
          </ul>
        </Alert>
      ) : null}
      {repair.warnings.length > 0 ? (
        <Alert tone="warning" title="Notes">
          <ul className="list-disc pl-5">
            {repair.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </Alert>
      ) : null}
      {repair.files.length > 0 ? (
        <>
          <ul className="flex flex-col gap-1 text-sm text-ink">
            {repair.files.map((file) => (
              <li key={file.path} className="break-all">
                <span className="font-medium">{file.path}</span>{" "}
                <span className="text-success-text">+{file.additions}</span>{" "}
                <span className="text-danger-text">−{file.deletions}</span>
                {file.action === "create" ? <span className="text-muted"> (new file)</span> : null}
              </li>
            ))}
          </ul>
          <details>
            <summary className="min-h-10 cursor-pointer py-2 text-sm font-medium text-primary-text">
              Show the exact change
            </summary>
            <DiffView diff={repair.diff} />
            <p className="mt-2 text-xs break-all text-muted">
              Change fingerprint (SHA-256): {repair.diff_sha256}
            </p>
          </details>
          <p className="text-xs text-muted">
            Branch it would use: <code>{repair.branch_name}</code>. Nothing has been changed in your
            repository.
          </p>
        </>
      ) : null}
    </article>
  );
}
