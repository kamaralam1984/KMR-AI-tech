import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import { Suspense } from "react";

import { Authed } from "@/components/auth/authed";
import { VerifyPanel } from "@/components/repairs/verify-panel";
import { RepairPanel } from "@/components/repairs/repair-panel";
import { IssueDetail } from "@/components/issues/issue-detail";
import { TicketPanel } from "@/components/issues/ticket-panel";
import { OnboardingCard } from "@/components/org/onboarding-card";
import { ErrorState } from "@/components/ui/error-state";
import { PageSkeleton } from "@/components/ui/page-skeleton";
import { serverGet } from "@/lib/session";
import type { Issue } from "@/lib/types";

export const metadata: Metadata = { title: "Issue" };

async function IssueContent({ params }: { params: Promise<{ issueId: string }> }) {
  const { issueId } = await params;
  return (
    <Authed>
      {async (ctx) => {
        const org = ctx.activeOrg;
        if (!org) return <OnboardingCard />;
        const result = await serverGet<Issue>(
          `/organizations/${org.id}/issues/${encodeURIComponent(issueId)}`,
        );
        if (result.kind === "not_found") notFound();
        if (result.kind !== "ok") {
          return (
            <ErrorState
              title="Cannot load this issue"
              description="Reload the page. If it keeps failing, switch organization from the account menu."
            />
          );
        }
        return (
          <div className="flex flex-col gap-6">
            <IssueDetail issue={result.data} />
            <TicketPanel orgId={org.id} issueId={result.data.id} />
            <RepairPanel
              orgId={org.id}
              projectId={result.data.project_id}
              issueId={result.data.id}
              role={org.role}
              issueOpen={result.data.state === "open"}
            />
            <VerifyPanel
              orgId={org.id}
              projectId={result.data.project_id}
              issueId={result.data.id}
              issueCreatedAt={result.data.created_at}
              role={org.role}
            />
          </div>
        );
      }}
    </Authed>
  );
}

export default function IssuePage({ params }: PageProps<"/issues/[issueId]">) {
  return (
    <>
      <Link
        href="/issues"
        className="mb-2 inline-flex min-h-10 items-center text-sm font-medium text-primary-text underline underline-offset-2"
      >
        Issues
      </Link>
      <Suspense fallback={<PageSkeleton label="Loading issue" />}>
        <IssueContent params={params} />
      </Suspense>
    </>
  );
}
