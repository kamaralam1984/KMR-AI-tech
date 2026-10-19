"use client";

import { Eye, XCircle } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { apiRequest } from "@/lib/client-api";
import { atLeast } from "@/lib/org";
import { profileLabel } from "@/lib/scan-profiles";
import {
  ACTIVE_STATUSES,
  describe,
  errorText,
  formatBytes,
  formatDuration,
  formatWhen,
  KIND_LABEL,
  stopText,
} from "@/lib/scan-text";
import type { ObservationPage, Role, ScanDetail } from "@/lib/types";

import { CompareCard } from "./compare-card";
import { BackendEvidence } from "./backend-evidence";
import { DiagnosePanel } from "./diagnose-panel";
import { StatusBadge } from "./status-badge";

const PAGE_SIZE = 100;
const POLL_MS = 2000;
const SEVERITY_TONE: Record<string, Tone> = {
  info: "info",
  warning: "warning",
  error: "danger",
};
const SEVERITY_LABEL: Record<string, string> = { info: "Info", warning: "Warning", error: "Error" };

export function ScanView({
  orgId,
  role,
  initialScan,
  initialObservations,
}: {
  orgId: string;
  role: Role;
  initialScan: ScanDetail;
  initialObservations: ObservationPage;
}) {
  const toast = useToast();
  const [scan, setScan] = useState(initialScan);
  const [observations, setObservations] = useState(initialObservations);
  const [kind, setKind] = useState<string>("all");
  const [loadingMore, setLoadingMore] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [shot, setShot] = useState<{ id: string; url: string | null } | null>(null);

  const base = `/organizations/${orgId}/scans/${scan.id}`;
  const active = ACTIVE_STATUSES.includes(scan.status);

  const query = useCallback(
    (selected: string, offset: number) =>
      `${base}/observations?limit=${PAGE_SIZE}&offset=${offset}${selected === "all" ? "" : `&kind=${selected}`}`,
    [base],
  );

  // While the scan is queued or running, refresh it every couple of seconds. Stops by itself.
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const tick = async () => {
      const [detail, list] = await Promise.all([
        apiRequest<ScanDetail>("GET", base),
        apiRequest<ObservationPage>("GET", query(kind, 0)),
      ]);
      if (cancelled) return;
      if (detail.ok) setScan(detail.data);
      if (list.ok) setObservations(list.data);
    };
    const timer = setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [active, base, kind, query]);

  async function chooseKind(next: string) {
    setKind(next);
    const list = await apiRequest<ObservationPage>("GET", query(next, 0));
    if (list.ok) setObservations(list.data);
  }

  async function loadMore() {
    setLoadingMore(true);
    const list = await apiRequest<ObservationPage>("GET", query(kind, observations.items.length));
    setLoadingMore(false);
    if (list.ok) {
      setObservations({
        total: list.data.total,
        items: [...observations.items, ...list.data.items],
      });
    }
  }

  async function cancel() {
    setCancelling(true);
    const result = await apiRequest<ScanDetail>("POST", `${base}/cancel`);
    setCancelling(false);
    setConfirmCancel(false);
    if (result.ok) {
      toast({ tone: "info", title: "Cancelling the scan" });
      setScan((current) => ({ ...current, ...result.data }));
    } else {
      toast({ tone: "danger", title: "Could not cancel", description: result.error.message });
    }
  }

  const failure = scan.status === "failed" ? errorText(scan.error_code) : null;
  const stop = stopText(scan.stopped_reason);
  const total = Object.values(scan.counts).reduce((sum, n) => sum + n, 0);
  const kinds = Object.keys(scan.counts).sort();

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <Link
                href={`/projects/${scan.project_id}`}
                className="inline-flex min-h-10 items-center text-sm font-medium text-primary-text underline underline-offset-2"
              >
                {scan.project_name ?? "Project"}
              </Link>
              <p className="mt-1 break-all font-medium text-ink">{scan.site_url}</p>
            </div>
            <div className="flex flex-col items-end gap-1">
              <StatusBadge status={scan.status} />
              <span className="text-xs text-muted">Ran as: {profileLabel(scan.limits)}</span>
            </div>
          </div>

          <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-muted">Pages scanned</dt>
              <dd className="mt-0.5 text-lg font-semibold text-ink">
                {scan.summary.pages_scanned ?? 0}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Requests</dt>
              <dd className="mt-0.5 text-lg font-semibold text-ink">
                {scan.summary.requests ?? 0}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Downloaded</dt>
              <dd className="mt-0.5 text-lg font-semibold text-ink">
                {formatBytes(scan.summary.bytes)}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Time</dt>
              <dd className="mt-0.5 text-lg font-semibold text-ink">
                {formatDuration(scan.summary.duration_ms)}
              </dd>
            </div>
          </dl>
          <p className="text-xs text-muted">
            Started {formatWhen(scan.started_at ?? scan.created_at)}
            {scan.finished_at ? ` · Finished ${formatWhen(scan.finished_at)}` : ""}
          </p>

          {active ? (
            <div className="flex flex-col gap-2">
              <p role="status" className="text-sm text-ink">
                {scan.status === "queued"
                  ? "Waiting for a scanner to pick this up…"
                  : scan.cancel_requested
                    ? "Cancelling…"
                    : "Scanning your site. Results appear below as they arrive."}
              </p>
              {atLeast(role, "member") && !scan.cancel_requested ? (
                <div>
                  <Button variant="secondary" onClick={() => setConfirmCancel(true)}>
                    <XCircle className="size-4" aria-hidden="true" />
                    Cancel scan
                  </Button>
                </div>
              ) : null}
            </div>
          ) : null}
          {failure ? (
            <Alert tone="danger" title="The scan did not finish">
              {failure}
            </Alert>
          ) : null}
          {stop && scan.status !== "failed" ? (
            <Alert tone="info" title="Scan limit">
              {stop}
            </Alert>
          ) : null}
        </div>
      </Card>

      <Alert tone="info" title="What you are looking at">
        Everything below was observed by a real browser while it loaded your pages. KMR AI does not
        yet explain causes or suggest fixes, so treat these as facts to investigate.
      </Alert>

      <BackendEvidence orgId={orgId} scanId={scan.id} role={role} status={scan.status} />

      <DiagnosePanel orgId={orgId} scanId={scan.id} role={role} status={scan.status} />

      {scan.status === "completed" ? <CompareCard orgId={orgId} scan={scan} /> : null}

      <Card title="Observations" description={`${total} recorded`}>
        <div className="flex flex-col gap-4">
          {kinds.length > 0 ? (
            <div role="group" aria-label="Filter observations" className="flex flex-wrap gap-2">
              {["all", ...kinds].map((name) => (
                <Button
                  key={name}
                  size="sm"
                  variant={kind === name ? "primary" : "secondary"}
                  aria-pressed={kind === name}
                  onClick={() => chooseKind(name)}
                >
                  {name === "all" ? "All" : (KIND_LABEL[name] ?? name)}
                  {name === "all" ? "" : ` (${scan.counts[name]})`}
                </Button>
              ))}
            </div>
          ) : null}

          {observations.items.length === 0 ? (
            <p className="text-sm text-muted">
              {active ? "Nothing recorded yet." : "No observations were recorded."}
            </p>
          ) : (
            <ul className="divide-y divide-line rounded-lg border border-line">
              {observations.items.map((o) => (
                <li
                  key={o.id}
                  className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-start sm:gap-4"
                >
                  <div className="flex shrink-0 items-center gap-2 sm:w-44 sm:flex-col sm:items-start">
                    <Badge tone={SEVERITY_TONE[o.severity] ?? "neutral"}>
                      {SEVERITY_LABEL[o.severity] ?? o.severity}
                    </Badge>
                    <span className="text-xs text-muted">{KIND_LABEL[o.kind] ?? o.kind}</span>
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm break-words text-ink">{describe(o)}</p>
                    {o.page_url ? (
                      <p className="mt-0.5 truncate text-xs text-muted">{o.page_url}</p>
                    ) : null}
                  </div>
                  {o.evidence_id ? (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setShot({ id: o.evidence_id as string, url: o.page_url })}
                      aria-label={`View screenshot of ${o.page_url ?? "the page"}`}
                    >
                      <Eye className="size-4" aria-hidden="true" />
                      Screenshot
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          )}

          {observations.items.length < observations.total ? (
            <Button
              variant="secondary"
              onClick={loadMore}
              isLoading={loadingMore}
              className="self-start"
            >
              Show more ({observations.total - observations.items.length} left)
            </Button>
          ) : null}
        </div>
      </Card>

      <Dialog
        open={shot !== null}
        onClose={() => setShot(null)}
        title="Screenshot"
        description={shot?.url ?? undefined}
      >
        {shot ? (
          // eslint-disable-next-line @next/next/no-img-element -- served by our own tenant-checked API route
          <img
            src={`/api${base}/evidence/${shot.id}`}
            alt={`Screenshot of ${shot.url ?? "the page"} as the scanner saw it`}
            className="max-h-[70dvh] w-full rounded-md border border-line object-contain"
          />
        ) : null}
      </Dialog>

      <Dialog
        open={confirmCancel}
        onClose={() => setConfirmCancel(false)}
        title="Cancel this scan?"
        description="What was found so far is kept. You can start a new scan afterwards."
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirmCancel(false)}>
              Keep scanning
            </Button>
            <Button variant="danger" onClick={cancel} isLoading={cancelling}>
              Cancel scan
            </Button>
          </>
        }
      />
    </div>
  );
}
