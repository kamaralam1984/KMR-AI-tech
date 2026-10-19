import type { Metadata } from "next";
import { Wrench } from "lucide-react";
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

export const metadata: Metadata = { title: "Repairs" };

export default function RepairsPage() {
  return (
    <>
      <PageHeader
        title="Repairs"
        description="Proposed changes to your code. Planning never changes your repository."
      />
      <Suspense fallback={<PageSkeleton label="Loading repairs" />}>
        <Authed>
          {async (ctx) => {
            const org = ctx.activeOrg;
            if (!org) return <OnboardingCard />;
            const result = await serverGet<Repair[]>(`/organizations/${org.id}/repairs?limit=50`);
            if (result.kind !== "ok") {
              return (
                <ErrorState
                  title="Cannot load repairs"
                  description="Reload the page. If this keeps happening, check that the API is running."
                />
              );
            }
            if (result.data.length === 0) {
              return (
                <EmptyState
                  icon={Wrench}
                  title="No repair plans yet"
                  description="Open an issue and choose “Plan a fix”. You need a connected repository first."
                />
              );
            }
            return (
              <div className="flex flex-col gap-3">
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
