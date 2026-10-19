"use client";

import { CircleCheck, CircleMinus, CircleX, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { apiRequest } from "@/lib/client-api";
import { atLeast } from "@/lib/org";
import {
  APPROVAL_STATE_LABEL,
  CHECK_LABEL,
  CHECK_STATUS_LABEL,
  OVERALL_TEXT,
  shortSha,
} from "@/lib/repair-text";
import type { ApplyResult, Approval, CheckRun, CheckStatus, Repair, Role } from "@/lib/types";

const ICON = {
  passed: CircleCheck,
  failed: CircleX,
  skipped: CircleMinus,
  unavailable: TriangleAlert,
} as const;
const TONE: Record<CheckStatus, Tone> = {
  passed: "success",
  failed: "danger",
  skipped: "neutral",
  unavailable: "warning",
};

/** Checks, the approval decision and the final apply for one proposed repair. */
export function RepairActions({
  orgId,
  repair,
  role,
}: {
  orgId: string;
  repair: Repair;
  role: Role;
}) {
  const toast = useToast();
  const base = `/organizations/${orgId}`;
  const [run, setRun] = useState<CheckRun | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ack, setAck] = useState(false);
  const [confirm, setConfirm] = useState<Approval | null>(null);
  const [applied, setApplied] = useState<ApplyResult | null>(null);

  const load = useCallback(async () => {
    const [checks, list] = await Promise.all([
      apiRequest<CheckRun[]>("GET", `${base}/repairs/${repair.id}/checks`),
      apiRequest<Approval[]>("GET", `${base}/repairs/${repair.id}/approvals`),
    ]);
    if (checks.ok) setRun(checks.data[0] ?? null);
    if (list.ok) setApprovals(list.data);
  }, [base, repair.id]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads server state on mount
    void load();
  }, [load]);

  async function act<T>(
    key: string,
    call: () => Promise<{ ok: boolean; data?: T; error?: { message: string } }>,
  ) {
    setBusy(key);
    setError(null);
    const result = await call();
    setBusy(null);
    if (!result.ok) {
      setError(result.error?.message ?? "Something went wrong.");
      return null;
    }
    await load();
    return result.data ?? null;
  }

  const canRun = atLeast(role, "member");
  const canDecide = atLeast(role, "admin");
  const active = approvals.find((a) => a.state === "active");
  const needsAck = run !== null && run.overall !== "passed" && run.overall !== "failed";

  return (
    <section
      aria-label="Checks and approval"
      className="flex flex-col gap-4 border-t border-line pt-4"
    >
      {error ? (
        <Alert tone="danger" title="That did not work">
          {error}
        </Alert>
      ) : null}

      <div className="flex flex-col gap-2">
        <h4 className="text-sm font-semibold text-ink">Checks</h4>
        {run ? (
          <>
            <ul className="flex flex-col gap-2">
              {run.results.map((r) => {
                const Icon = ICON[r.status];
                return (
                  <li key={r.name} className="flex flex-wrap items-start gap-2 text-sm">
                    <Icon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                    <span className="font-medium text-ink">{CHECK_LABEL[r.name] ?? r.name}</span>
                    <Badge tone={TONE[r.status]}>{CHECK_STATUS_LABEL[r.status]}</Badge>
                    {r.detail ? (
                      <span className="w-full pl-6 text-xs break-words text-muted">{r.detail}</span>
                    ) : null}
                  </li>
                );
              })}
            </ul>
            <p className="text-xs text-muted">{OVERALL_TEXT[run.overall]}</p>
          </>
        ) : (
          <p className="text-sm text-muted">No checks have been run on this change yet.</p>
        )}
        {canRun && repair.status === "proposed" ? (
          <div>
            <Button
              variant="secondary"
              isLoading={busy === "checks"}
              onClick={() =>
                act("checks", () =>
                  apiRequest<CheckRun>("POST", `${base}/repairs/${repair.id}/checks`),
                )
              }
            >
              {run ? "Run the checks again" : "Run the checks"}
            </Button>
            <p className="mt-1 text-xs text-muted">
              Runs on a copy in a sandbox with no network. Your repository is not touched.
            </p>
          </div>
        ) : null}
      </div>

      {repair.status === "proposed" ? (
        <div className="flex flex-col gap-3">
          <h4 className="text-sm font-semibold text-ink">Approval</h4>
          {approvals.length === 0 ? (
            <p className="text-sm text-muted">Not decided yet. Silence is not approval.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {approvals.map((a) => (
                <li key={a.id} className="flex flex-wrap items-center gap-2 text-sm">
                  <Badge tone={a.state === "active" ? "success" : "neutral"}>
                    {APPROVAL_STATE_LABEL[a.state]}
                  </Badge>
                  <span className="text-muted">
                    {a.decision === "approved" ? "Approved" : "Rejected"} for change{" "}
                    {shortSha(a.diff_sha256)}, expires {new Date(a.expires_at).toLocaleString()}
                  </span>
                  {a.state === "active" && canDecide ? (
                    <Button
                      variant="secondary"
                      isLoading={busy === `revoke-${a.id}`}
                      onClick={() =>
                        act(`revoke-${a.id}`, () =>
                          apiRequest("POST", `${base}/approvals/${a.id}/revoke`, {}),
                        )
                      }
                    >
                      Revoke
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
          <div className="rounded-lg border border-line bg-page p-4 text-sm text-ink">
            <h5 className="font-semibold">Before you approve</h5>
            <ul className="mt-2 list-disc pl-5">
              <li>
                Changes {repair.files.length} file(s):{" "}
                {repair.files.map((f) => `${f.path} (+${f.additions} −${f.deletions})`).join(", ")}.
              </li>
              <li>
                Checks:{" "}
                {run
                  ? OVERALL_TEXT[run.overall]
                  : "none run yet, so nothing about this change is verified."}
              </li>
              <li>
                Effect of approving: a new branch <code>{repair.branch_name}</code> is created from{" "}
                {repair.base_branch}@{shortSha(repair.base_sha)}. Nothing else is changed.
              </li>
              <li>
                Rollback: delete that branch. Your {repair.base_branch} branch is never modified by
                KMR AI, and merging or deploying stays your own separate decision.
              </li>
            </ul>
          </div>
          {canDecide ? (
            <div className="flex flex-col gap-3">
              {needsAck ? (
                <label className="flex items-start gap-2 text-sm text-ink">
                  <input
                    type="checkbox"
                    checked={ack}
                    onChange={(e) => setAck(e.target.checked)}
                    className="mt-1 size-4"
                  />
                  <span>I understand not every check ran, and I accept that.</span>
                </label>
              ) : null}
              <div className="flex flex-wrap gap-2">
                <Button
                  isLoading={busy === "approve"}
                  disabled={!run || run.overall === "failed" || (needsAck && !ack)}
                  onClick={() =>
                    act("approve", () =>
                      apiRequest<Approval>("POST", `${base}/repairs/${repair.id}/approvals`, {
                        decision: "approved",
                        acknowledge_incomplete_checks: needsAck && ack,
                      }),
                    )
                  }
                >
                  Approve this exact change
                </Button>
                <Button
                  variant="secondary"
                  isLoading={busy === "reject"}
                  onClick={() =>
                    act("reject", () =>
                      apiRequest<Approval>("POST", `${base}/repairs/${repair.id}/approvals`, {
                        decision: "rejected",
                      }),
                    )
                  }
                >
                  Reject
                </Button>
                {active ? (
                  <Button variant="secondary" onClick={() => setConfirm(active)}>
                    Create the branch
                  </Button>
                ) : null}
              </div>
              <p className="text-xs text-muted">
                Approval covers only this change ({shortSha(repair.diff_sha256)}) and creating the
                branch <code>{repair.branch_name}</code>. Merging and deploying are never done here;
                each needs your own separate decision outside KMR AI.
              </p>
            </div>
          ) : (
            <p className="text-xs text-muted">Only an owner or admin can approve or apply.</p>
          )}
        </div>
      ) : null}

      {applied ? (
        <Alert tone={applied.status === "applied" ? "success" : "danger"} title="Branch created">
          {applied.message}
          {applied.commit_sha ? ` Commit ${shortSha(applied.commit_sha)}.` : ""}
        </Alert>
      ) : null}

      <Dialog
        open={confirm !== null}
        onClose={() => setConfirm(null)}
        title="Create this branch?"
        description={`This creates the new branch ${repair.branch_name} from ${repair.base_branch}@${shortSha(repair.base_sha)}. Your default branch is not changed and nothing is merged or deployed.`}
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
            <Button
              isLoading={busy === "apply"}
              onClick={async () => {
                if (!confirm) return;
                const result = await act("apply", () =>
                  apiRequest<ApplyResult>("POST", `${base}/repairs/${repair.id}/apply`, {
                    approval_id: confirm.id,
                  }),
                );
                setConfirm(null);
                if (result) {
                  setApplied(result);
                  toast({ tone: "success", title: "Branch created" });
                }
              }}
            >
              Create the branch
            </Button>
          </>
        }
      />
    </section>
  );
}
