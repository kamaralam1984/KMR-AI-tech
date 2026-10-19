import type { Metadata } from "next";
import { CheckSquare } from "lucide-react";
import { Suspense } from "react";

import { Authed } from "@/components/auth/authed";
import { OnboardingCard } from "@/components/org/onboarding-card";
import { RepairCard } from "@/components/repairs/repair-card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { PageSkeleton } from "@/components/ui/page-skeleton";
import { serverGet } from "@/lib/session";
import type { Repair } from "@/lib/types";

export const metadata: Metadata = { title: "Approvals" };

export default function ApprovalsPage() {
  return (
    <>
      <PageHeader
        title="Approvals"
        description="Changes waiting for a decision. Approval covers one exact change and expires."
      />
      <Suspense fallback={<PageSkeleton label="Loading approvals" />}>
        <Authed>
          {async (ctx) => {
            const org = ctx.activeOrg;
            if (!org) return <OnboardingCard />;
            const result = await serverGet<Repair[]>(
              `/organizations/${org.id}/repairs?status=proposed&limit=50`,
            );
            if (result.kind !== "ok") {
              return (
                <ErrorState
                  title="Cannot load approvals"
                  description="Reload the page. If this keeps happening, check that the API is running."
                />
              );
            }
            if (result.data.length === 0) {
              return (
                <EmptyState
                  icon={CheckSquare}
                  title="Nothing waiting for approval"
                  description="Plan a fix from an issue. Run its checks and decide on the issue page."
                />
              );
            }
            return (
              <div className="flex flex-col gap-3">
                <p className="text-sm text-muted">
                  Open the issue to run checks, approve or reject, and create the branch.
                </p>
                {result.data.map((repair) => (
                  <RepairCard key={repair.id} repair={repair} showIssueLink />
                ))}
              </div>
            );
          }}
        </Authed>
      </Suspense>
    </>
  );
}
