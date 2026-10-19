import type { Metadata } from "next";
import Link from "next/link";
import { ScanSearch } from "lucide-react";
import { Suspense } from "react";

import { Authed } from "@/components/auth/authed";
import { OnboardingCard } from "@/components/org/onboarding-card";
import { ScanList } from "@/components/scans/scan-list";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { PageSkeleton } from "@/components/ui/page-skeleton";
import { serverGet, type SessionContext } from "@/lib/session";
import type { Scan } from "@/lib/types";

export const metadata: Metadata = { title: "Scans" };

async function ScansContent({ ctx }: { ctx: SessionContext }) {
  const org = ctx.activeOrg;
  if (!org) return <OnboardingCard />;
  const result = await serverGet<Scan[]>(`/organizations/${org.id}/scans?limit=50`);
  if (result.kind !== "ok") {
    return (
      <ErrorState
        title="Cannot load scans"
        description="Reload the page. If this keeps happening, check that the API is running."
      />
    );
  }
  if (result.data.length === 0) {
    return (
      <EmptyState
        icon={ScanSearch}
        title="No scans yet"
        description="Open a project, add its website, verify that you own it, then start a scan."
        action={
          <Link
            href="/projects"
            className="inline-flex h-11 items-center rounded-md bg-primary px-4 text-sm font-medium text-white hover:bg-primary-strong"
          >
            Go to projects
          </Link>
        }
      />
    );
  }
  return <ScanList scans={result.data} />;
}

export default function ScansPage() {
  return (
    <>
      <PageHeader
        title="Scans"
        description="Read-only scans of your verified websites. Newest first."
      />
      <Suspense fallback={<PageSkeleton label="Loading scans" />}>
        <Authed>{(ctx) => <ScansContent ctx={ctx} />}</Authed>
      </Suspense>
    </>
  );
}
