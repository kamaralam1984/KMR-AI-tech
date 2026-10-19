import type { Metadata } from "next";
import { Suspense } from "react";

import { Authed } from "@/components/auth/authed";
import { MetricsCard } from "@/components/dashboard/metrics";
import { SystemStatus } from "@/components/dashboard/system-status";
import { WorkspaceCard } from "@/components/dashboard/workspace-card";
import { Card } from "@/components/ui/card";
import { LoadingRegion, Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/ui/page-header";
import { PageSkeleton } from "@/components/ui/page-skeleton";

export const metadata: Metadata = { title: "Dashboard" };

export default function DashboardPage() {
  return (
    <>
      <PageHeader
        title="Dashboard"
        description="KMR AI scans websites, explains issues with evidence, and proposes fixes that only apply after your approval."
      />
      <div className="mb-6">
        <Suspense fallback={<PageSkeleton label="Loading your workspace" />}>
          <Authed>{(ctx) => <WorkspaceCard ctx={ctx} />}</Authed>
        </Suspense>
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-2">
        <Card title="System status" description="Live from the KMR AI backend.">
          <Suspense
            fallback={
              <LoadingRegion label="Checking system status">
                <div className="flex flex-col gap-3">
                  <Skeleton className="h-6 w-2/3" />
                  <Skeleton className="h-24 w-full" />
                </div>
              </LoadingRegion>
            }
          >
            <SystemStatus />
          </Suspense>
        </Card>
        <Suspense fallback={<PageSkeleton label="Loading your numbers" />}>
          <Authed>
            {(ctx) => (ctx.activeOrg ? <MetricsCard orgId={ctx.activeOrg.id} /> : null)}
          </Authed>
        </Suspense>
      </div>
    </>
  );
}
