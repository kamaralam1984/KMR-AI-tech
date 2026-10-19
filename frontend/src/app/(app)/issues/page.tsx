import type { Metadata } from "next";
import Link from "next/link";
import { TriangleAlert } from "lucide-react";
import { Suspense } from "react";

import { Authed } from "@/components/auth/authed";
import { IssueList } from "@/components/issues/issue-list";
import { OnboardingCard } from "@/components/org/onboarding-card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { PageSkeleton } from "@/components/ui/page-skeleton";
import { SEVERITY_LABEL, SEVERITY_ORDER } from "@/lib/issue-text";
import { serverGet, type SessionContext } from "@/lib/session";
import type { Impact, IssueSummary, RankedIssue, Severity } from "@/lib/types";

export const metadata: Metadata = { title: "Issues" };

async function IssuesContent({
  ctx,
  searchParams,
}: {
  ctx: SessionContext;
  searchParams: Promise<{ severity?: string; sort?: string }>;
}) {
  const org = ctx.activeOrg;
  if (!org) return <OnboardingCard />;
  const { severity, sort } = await searchParams;
  const byImpact = sort === "impact";
  const filter = SEVERITY_ORDER.includes(severity as Severity) ? (severity as Severity) : null;
  const impacts: Record<string, Impact> = {};
  let result: Awaited<ReturnType<typeof serverGet<IssueSummary[]>>>;
  if (byImpact) {
    const ranked = await serverGet<RankedIssue[]>(
      `/organizations/${org.id}/issues/ranked?limit=100`,
    );
    if (ranked.kind === "ok") {
      const items = ranked.data.filter((r) => !filter || r.issue.severity === filter);
      for (const r of items) impacts[r.issue.id] = r.impact;
      result = { kind: "ok", data: items.map((r) => r.issue) };
    } else {
      result = ranked;
    }
  } else {
    result = await serverGet<IssueSummary[]>(
      `/organizations/${org.id}/issues?limit=100${filter ? `&severity=${filter}` : ""}`,
    );
  }
  if (result.kind !== "ok") {
    return (
      <ErrorState
        title="Cannot load issues"
        description="Reload the page. If this keeps happening, check that the API is running."
      />
    );
  }
  const sortHref = (value: "newest" | "impact") =>
    `/issues?${[filter ? `severity=${filter}` : "", value === "impact" ? "sort=impact" : ""].filter(Boolean).join("&")}`;
  return (
    <div className="flex flex-col gap-4">
      <nav aria-label="Sort issues" className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted">Order:</span>
        {(["newest", "impact"] as const).map((value) => (
          <Link
            key={value}
            href={sortHref(value)}
            aria-current={(value === "impact") === byImpact ? "page" : undefined}
            className={`inline-flex min-h-10 items-center rounded-md border px-3 font-medium ${
              (value === "impact") === byImpact
                ? "border-primary bg-primary text-white"
                : "border-line bg-surface text-ink hover:bg-page"
            }`}
          >
            {value === "impact" ? "Likely business impact" : "Newest"}
          </Link>
        ))}
      </nav>
      {byImpact ? (
        <p className="text-xs text-muted">
          Impact order is an estimate from severity and what kind of page is affected (checkout,
          login, sign-up...), judged from the page address. It is not measured, and no revenue or
          user numbers are shown.
        </p>
      ) : null}
      <nav aria-label="Filter by severity" className="flex flex-wrap gap-2">
        {[null, ...SEVERITY_ORDER].map((level) => (
          <Link
            key={level ?? "all"}
            href={`/issues?${[level ? `severity=${level}` : "", byImpact ? "sort=impact" : ""].filter(Boolean).join("&")}`}
            aria-current={level === filter ? "page" : undefined}
            className={`inline-flex min-h-10 items-center rounded-md border px-3 text-sm font-medium ${
              level === filter
                ? "border-primary bg-primary text-white"
                : "border-line bg-surface text-ink hover:bg-page"
            }`}
          >
            {level ? SEVERITY_LABEL[level] : "All"}
          </Link>
        ))}
      </nav>
      {result.data.length === 0 ? (
        <EmptyState
          icon={TriangleAlert}
          title={filter ? "No issues at this severity" : "No issues yet"}
          description="Open a finished scan and choose “Diagnose this scan” to list its issues."
          action={
            <Link
              href="/scans"
              className="inline-flex h-11 items-center rounded-md bg-primary px-4 text-sm font-medium text-white hover:bg-primary-strong"
            >
              Go to scans
            </Link>
          }
        />
      ) : (
        <IssueList issues={result.data} impacts={byImpact ? impacts : undefined} />
      )}
    </div>
  );
}

export default function IssuesPage({ searchParams }: PageProps<"/issues">) {
  return (
    <>
      <PageHeader
        title="Issues"
        description="Problems found in your scans, each tied to the evidence it rests on."
      />
      <Suspense fallback={<PageSkeleton label="Loading issues" />}>
        <Authed>{(ctx) => <IssuesContent ctx={ctx} searchParams={searchParams} />}</Authed>
      </Suspense>
    </>
  );
}
