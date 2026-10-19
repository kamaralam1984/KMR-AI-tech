"use client";

import { Sparkles } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { SeverityBadge } from "@/components/issues/severity-badge";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";
import { apiRequest } from "@/lib/client-api";
import { providerNote } from "@/lib/issue-text";
import { atLeast } from "@/lib/org";
import type { AiStatus, IssueSummary, Role, ScanStatus } from "@/lib/types";

type Diagnosed = {
  run: { issues_count: number; dropped_issues: number; flags: string[] };
  issues: IssueSummary[];
};

/** Runs a diagnosis of a finished scan and lists the issues found, saying who produced them. */
export function DiagnosePanel({
  orgId,
  scanId,
  role,
  status,
}: {
  orgId: string;
  scanId: string;
  role: Role;
  status: ScanStatus;
}) {
  const toast = useToast();
  const base = `/organizations/${orgId}/scans/${scanId}`;
  const [issues, setIssues] = useState<IssueSummary[] | null>(null);
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiRequest<IssueSummary[]>("GET", `${base}/issues`),
      apiRequest<AiStatus>("GET", "/ai/status"),
    ]).then(([list, status_]) => {
      if (cancelled) return;
      if (list.ok) setIssues(list.data);
      if (status_.ok) setAi(status_.data);
    });
    return () => {
      cancelled = true;
    };
  }, [base, status]);

  async function diagnose() {
    setBusy(true);
    setError(null);
    setNote(null);
    const result = await apiRequest<Diagnosed>("POST", `${base}/diagnose`);
    setBusy(false);
    if (!result.ok) return setError(result.error.message);
    setIssues(result.data.issues);
    const dropped = result.data.run.dropped_issues;
    if (dropped > 0 || result.data.run.flags.length > 0) {
      setNote(
        `${dropped} proposed issue(s) were discarded because they did not point at real evidence.`,
      );
    }
    toast({ tone: "success", title: `${result.data.run.issues_count} issue(s) found` });
  }

  const canRun = atLeast(role, "member") && status === "completed";
  return (
    <Card
      title="Issues"
      description="Turn what the scan saw into a list of problems, each tied to its evidence."
    >
      <div className="flex flex-col gap-4">
        {ai ? <p className="text-xs text-muted">{providerNote(ai.is_ai, ai.model)}</p> : null}
        {error ? (
          <Alert tone="danger" title="Diagnosis did not run">
            {error}
          </Alert>
        ) : null}
        {note ? (
          <Alert tone="warning" title="Some output was discarded">
            {note}
          </Alert>
        ) : null}
        {canRun ? (
          <div>
            <Button onClick={diagnose} isLoading={busy}>
              <Sparkles className="size-4" aria-hidden="true" />
              {issues && issues.length > 0 ? "Diagnose again" : "Diagnose this scan"}
            </Button>
          </div>
        ) : status !== "completed" ? (
          <p className="text-sm text-muted">Diagnosis is available once the scan has completed.</p>
        ) : null}
        {issues === null ? null : issues.length === 0 ? (
          <p className="text-sm text-muted">No issues recorded for this scan yet.</p>
        ) : (
          <ul
            aria-label="Issues from this scan"
            className="divide-y divide-line rounded-lg border border-line"
          >
            {issues.map((issue) => (
              <li
                key={issue.id}
                className="flex flex-wrap items-center justify-between gap-2 px-4 py-3"
              >
                <Link
                  href={`/issues/${issue.id}`}
                  className="min-w-0 flex-1 text-sm font-medium break-words text-primary-text underline-offset-2 hover:underline"
                >
                  {issue.title}
                </Link>
                <div className="flex items-center gap-2">
                  {issue.classification === "needs_review" ? (
                    <Badge tone="warning">Needs review</Badge>
                  ) : null}
                  <SeverityBadge severity={issue.severity} />
                </div>
              </li>
            ))}
          </ul>
        )}
        {issues && issues.length > 0 ? (
          <p className="text-sm text-ink">
            Would you like KMR AI to plan a fix? Open an issue and choose “Plan a fix” for the ones
            you want. Nothing is changed unless you pick it and approve it.
          </p>
        ) : null}
      </div>
    </Card>
  );
}
