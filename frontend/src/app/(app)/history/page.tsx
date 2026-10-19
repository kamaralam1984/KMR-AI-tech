import type { Metadata } from "next";
import Link from "next/link";
import { History as HistoryIcon, ShieldCheck, ShieldAlert } from "lucide-react";
import { Suspense } from "react";

import { Authed } from "@/components/auth/authed";
import { OnboardingCard } from "@/components/org/onboarding-card";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { PageSkeleton } from "@/components/ui/page-skeleton";
import { TD, TH, THead, TR, Table } from "@/components/ui/table";
import { FILTER_ACTIONS, VERIFY_REASON, actionLabel, detailsSummary } from "@/lib/audit-text";
import { atLeast } from "@/lib/org";
import { serverGet, type SessionContext } from "@/lib/session";
import type { AuditEvent, AuditVerify } from "@/lib/types";

export const metadata: Metadata = { title: "History & audit" };
const PAGE_SIZE = 25;

async function HistoryContent({
  ctx,
  searchParams,
}: {
  ctx: SessionContext;
  searchParams: Promise<{ action?: string; page?: string }>;
}) {
  const org = ctx.activeOrg;
  if (!org) return <OnboardingCard />;
  if (!atLeast(org.role, "admin")) {
    return (
      <Alert tone="info" title="Only owners and admins can read the audit trail">
        You can still follow your own work on the{" "}
        <Link href="/scans" className="font-medium underline underline-offset-2">
          Scans
        </Link>{" "}
        and{" "}
        <Link href="/issues" className="font-medium underline underline-offset-2">
          Issues
        </Link>{" "}
        pages.
      </Alert>
    );
  }
  const { action, page } = await searchParams;
  const filter = (FILTER_ACTIONS as readonly string[]).includes(action ?? "") ? action : undefined;
  const pageNumber = Math.max(1, Math.min(1000, Number.parseInt(page ?? "1", 10) || 1));
  const query = `limit=${PAGE_SIZE + 1}&offset=${(pageNumber - 1) * PAGE_SIZE}${filter ? `&action=${filter}` : ""}`;
  const [events, verify] = await Promise.all([
    serverGet<AuditEvent[]>(`/organizations/${org.id}/audit?${query}`),
    serverGet<AuditVerify>(`/organizations/${org.id}/audit/verify`),
  ]);
  if (events.kind !== "ok") {
    return (
      <ErrorState
        title="Cannot load the audit trail"
        description="Reload the page. If this keeps happening, check that the API is running."
      />
    );
  }
  const rows = events.data.slice(0, PAGE_SIZE);
  const hasNext = events.data.length > PAGE_SIZE;
  const href = (p: number) => `/history?page=${p}${filter ? `&action=${filter}` : ""}`;
  return (
    <div className="flex flex-col gap-4">
      {verify.kind === "ok" ? (
        verify.data.ok ? (
          <div className="flex flex-wrap items-center gap-2 text-sm text-ink">
            <ShieldCheck className="size-5 text-success-text" aria-hidden="true" />
            <Badge tone="success">Chain intact</Badge>
            <span className="text-muted">
              {verify.data.checked} events checked
              {verify.data.unchained
                ? `, ${verify.data.unchained} older events predate the chain`
                : ""}
              .
            </span>
          </div>
        ) : (
          <Alert tone="danger" title="The audit trail has been altered">
            <span className="flex items-start gap-2">
              <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              {VERIFY_REASON[verify.data.reason ?? ""] ?? "The chain failed verification."} First
              problem at event {verify.data.broken_at}.
            </span>
          </Alert>
        )
      ) : null}
      <form method="get" action="/history" className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1.5 text-sm font-medium text-ink">
          Show
          <select
            name="action"
            defaultValue={filter ?? ""}
            className="h-11 rounded-md border border-line bg-surface px-3 text-base lg:h-10 lg:text-sm"
          >
            <option value="">Everything</option>
            {FILTER_ACTIONS.map((a) => (
              <option key={a} value={a}>
                {actionLabel(a)}
              </option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          className="inline-flex h-11 items-center rounded-md border border-line bg-surface px-4 text-sm font-medium text-ink hover:bg-page lg:h-10"
        >
          Apply
        </button>
      </form>
      {rows.length === 0 ? (
        <EmptyState
          icon={HistoryIcon}
          title="No events here"
          description="Planning fixes, approvals and verifications are recorded here as they happen."
        />
      ) : (
        <Table caption="Audit events, newest first">
          <THead>
            <TR>
              <TH>When</TH>
              <TH>What</TH>
              <TH>Outcome</TH>
              <TH>Details</TH>
            </TR>
          </THead>
          <tbody>
            {rows.map((e) => (
              <TR key={e.id}>
                <TD>{new Date(e.created_at).toLocaleString()}</TD>
                <TD>{actionLabel(e.action)}</TD>
                <TD>
                  <Badge
                    tone={
                      e.outcome === "success"
                        ? "success"
                        : e.outcome === "denied"
                          ? "warning"
                          : "danger"
                    }
                  >
                    {e.outcome === "success"
                      ? "Success"
                      : e.outcome === "denied"
                        ? "Refused"
                        : "Failed"}
                  </Badge>
                </TD>
                <TD>
                  <span className="text-xs break-words text-muted">
                    {detailsSummary(e.details)}
                  </span>
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}
      <nav aria-label="Pages" className="flex items-center gap-3 text-sm">
        {pageNumber > 1 ? (
          <Link href={href(pageNumber - 1)} className="font-medium text-primary-text underline">
            Newer
          </Link>
        ) : null}
        <span className="text-muted">Page {pageNumber}</span>
        {hasNext ? (
          <Link href={href(pageNumber + 1)} className="font-medium text-primary-text underline">
            Older
          </Link>
        ) : null}
      </nav>
    </div>
  );
}

export default function HistoryPage({ searchParams }: PageProps<"/history">) {
  return (
    <>
      <PageHeader
        title="History & audit"
        description="A tamper-evident record of who decided what, and what happened."
      />
      <Suspense fallback={<PageSkeleton label="Loading history" />}>
        <Authed>{(ctx) => <HistoryContent ctx={ctx} searchParams={searchParams} />}</Authed>
      </Suspense>
    </>
  );
}
