"use client";

import { CircleCheck, CircleHelp, CircleX } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { SelectField } from "@/components/ui/select";
import { useToast } from "@/components/ui/toast";
import { apiRequest } from "@/lib/client-api";
import { atLeast } from "@/lib/org";
import type { Role, Scan, FixVerification } from "@/lib/types";

const OUTCOME = {
  fixed: { label: "Fixed", tone: "success" as Tone, Icon: CircleCheck },
  not_fixed: { label: "Not Fixed", tone: "danger" as Tone, Icon: CircleX },
  needs_review: { label: "Needs Review", tone: "warning" as Tone, Icon: CircleHelp },
};

/** Compares a newer scan with the one that found the issue and reports one honest outcome. */
export function VerifyPanel({
  orgId,
  projectId,
  issueId,
  issueCreatedAt,
  role,
}: {
  orgId: string;
  projectId: string;
  issueId: string;
  issueCreatedAt: string;
  role: Role;
}) {
  const toast = useToast();
  const [runs, setRuns] = useState<FixVerification[] | null>(null);
  const [scans, setScans] = useState<Scan[]>([]);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiRequest<FixVerification[]>(
        "GET",
        `/organizations/${orgId}/issues/${issueId}/verifications`,
      ),
      apiRequest<Scan[]>("GET", `/organizations/${orgId}/projects/${projectId}/scans?limit=20`),
    ]).then(([v, s]) => {
      if (cancelled) return;
      if (v.ok) setRuns(v.data);
      if (s.ok) {
        const newer = s.data.filter(
          (scan) => scan.status === "completed" && scan.created_at > issueCreatedAt,
        );
        setScans(newer);
        setSelected(newer[0]?.id ?? "");
      }
    });
    return () => {
      cancelled = true;
    };
  }, [orgId, projectId, issueId, issueCreatedAt]);

  async function verify() {
    setBusy(true);
    setError(null);
    const result = await apiRequest<FixVerification>(
      "POST",
      `/organizations/${orgId}/issues/${issueId}/verify`,
      { after_scan_id: selected },
    );
    setBusy(false);
    if (!result.ok) return setError(result.error.message);
    setRuns((current) => [result.data, ...(current ?? [])]);
    toast({ tone: "success", title: `Result: ${OUTCOME[result.data.outcome].label}` });
  }

  return (
    <Card
      title="Verify the fix"
      description="After the fix is live on your site, scan again and compare. A build passing is not proof."
    >
      <div className="flex flex-col gap-4">
        {error ? (
          <Alert tone="danger" title="Could not verify">
            {error}
          </Alert>
        ) : null}
        {atLeast(role, "member") ? (
          scans.length > 0 ? (
            <div className="flex flex-col gap-3 sm:max-w-md">
              <SelectField
                label="Scan to compare against"
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
              >
                {scans.map((scan) => (
                  <option key={scan.id} value={scan.id}>
                    {new Date(scan.created_at).toLocaleString()}
                  </option>
                ))}
              </SelectField>
              <div>
                <Button onClick={verify} isLoading={busy} disabled={!selected}>
                  Check whether it is fixed
                </Button>
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted">
              No finished scan newer than this issue yet. Once the fix is live, start a new scan and
              come back.
              <Link
                href={`/projects/${projectId}`}
                className="mt-1 flex min-h-10 items-center font-medium text-primary-text underline underline-offset-2"
              >
                Go to the project to start a scan
              </Link>
            </p>
          )
        ) : null}
        {runs === null ? null : runs.length === 0 ? (
          <p className="text-sm text-muted">Not verified yet.</p>
        ) : (
          <ul aria-label="Verification results" className="flex flex-col gap-3">
            {runs.map((run) => {
              const { label, tone, Icon } = OUTCOME[run.outcome];
              return (
                <li key={run.id} className="flex flex-col gap-2 rounded-lg border border-line p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Icon className="size-5" aria-hidden="true" />
                    <Badge tone={tone}>{label}</Badge>
                    <span className="text-xs text-muted">
                      {new Date(run.created_at).toLocaleString()}
                      {run.via_kmr_ai ? " · fix made through KMR AI" : " · fix made outside KMR AI"}
                    </span>
                  </div>
                  <ul className="list-disc pl-5 text-sm text-ink">
                    {run.reasons.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ul>
                  {run.details.pages_not_retested?.length ? (
                    <p className="text-xs break-all text-muted">
                      Not re-tested: {run.details.pages_not_retested.join(", ")}
                    </p>
                  ) : null}
                  {run.details.new_issues?.length ? (
                    <p className="text-xs text-muted">
                      New problems: {run.details.new_issues.map((i) => i.title).join("; ")}
                    </p>
                  ) : null}
                  {run.verified_diff_sha256 ? (
                    <p className="text-xs break-all text-muted">
                      Verified change (SHA-256): {run.verified_diff_sha256}
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Card>
  );
}
