import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { Authed } from "@/components/auth/authed";
import { OnboardingCard } from "@/components/org/onboarding-card";
import { ScanView } from "@/components/scans/scan-view";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { PageSkeleton } from "@/components/ui/page-skeleton";
import { serverGet } from "@/lib/session";
import type { ObservationPage, ScanDetail } from "@/lib/types";

export const metadata: Metadata = { title: "Scan" };

async function ScanDetailContent({ params }: { params: Promise<{ scanId: string }> }) {
  const { scanId } = await params;
  return (
    <Authed>
      {async (ctx) => {
        const org = ctx.activeOrg;
        if (!org) return <OnboardingCard />;
        const base = `/organizations/${org.id}/scans/${encodeURIComponent(scanId)}`;
        const [scan, observations] = await Promise.all([
          serverGet<ScanDetail>(base),
          serverGet<ObservationPage>(`${base}/observations?limit=100`),
        ]);
        if (scan.kind === "not_found") notFound();
        if (scan.kind !== "ok" || observations.kind !== "ok") {
          return (
            <ErrorState
              title="Cannot load this scan"
              description="Reload the page. If it keeps failing, switch organization from the account menu."
            />
          );
        }
        return (
          <ScanView
            orgId={org.id}
            role={org.role}
            initialScan={scan.data}
            initialObservations={observations.data}
          />
        );
      }}
    </Authed>
  );
}

export default function ScanPage({ params }: PageProps<"/scans/[scanId]">) {
  return (
    <>
      <PageHeader title="Scan" description="What the scanner saw on your website." />
      <Suspense fallback={<PageSkeleton label="Loading scan" />}>
        <ScanDetailContent params={params} />
      </Suspense>
    </>
  );
}
