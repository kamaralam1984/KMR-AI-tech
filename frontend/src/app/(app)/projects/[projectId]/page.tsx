import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { Authed } from "@/components/auth/authed";
import { OnboardingCard } from "@/components/org/onboarding-card";
import { ApiChecksCard } from "@/components/projects/api-checks-card";
import { RepositoryCard } from "@/components/projects/repository-card";
import { SiteCard } from "@/components/projects/site-card";
import { TrafficCard } from "@/components/projects/traffic-card";
import { ScanList } from "@/components/scans/scan-list";
import { StartScanButton } from "@/components/scans/start-scan-button";
import { Card } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { PageSkeleton } from "@/components/ui/page-skeleton";
import { atLeast } from "@/lib/org";
import { ACTIVE_STATUSES } from "@/lib/scan-text";
import { serverGet } from "@/lib/session";
import type { ApiCheck, Project, Repository, Scan, TrafficSummary } from "@/lib/types";

export const metadata: Metadata = { title: "Project" };

async function ProjectDetail({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  return (
    <Authed>
      {async (ctx) => {
        const org = ctx.activeOrg;
        if (!org) return <OnboardingCard />;
        const id = encodeURIComponent(projectId);
        const [project, scans, checks, repository, traffic, options] = await Promise.all([
          serverGet<Project>(`/organizations/${org.id}/projects/${id}`),
          serverGet<Scan[]>(`/organizations/${org.id}/projects/${id}/scans?limit=20`),
          serverGet<ApiCheck[]>(`/organizations/${org.id}/projects/${id}/api-checks`),
          serverGet<Repository | null>(`/organizations/${org.id}/projects/${id}/repository`),
          serverGet<TrafficSummary>(`/organizations/${org.id}/projects/${id}/traffic`),
          serverGet<{ engines: string[] }>(`/organizations/${org.id}/scan-options`),
        ]);
        if (project.kind === "not_found") notFound();
        if (
          project.kind !== "ok" ||
          scans.kind !== "ok" ||
          checks.kind !== "ok" ||
          repository.kind !== "ok" ||
          traffic.kind !== "ok"
        ) {
          return (
            <ErrorState
              title="Cannot load this project"
              description="Reload the page. If it keeps failing, switch organization from the account menu."
            />
          );
        }
        const p = project.data;
        const canWrite = atLeast(org.role, "member");
        const active = scans.data.find((s) => ACTIVE_STATUSES.includes(s.status));
        const blockedReason = !canWrite
          ? "You have view-only access, so you cannot start scans."
          : !p.site.site_url
            ? "Add the website address above first."
            : !p.site.verification.verified
              ? "Verify that you own the website first."
              : null;
        return (
          <div className="flex flex-col gap-6">
            <div>
              <Link
                href="/projects"
                className="inline-flex min-h-10 items-center text-sm font-medium text-primary-text underline underline-offset-2"
              >
                Projects
              </Link>
              <h1 className="mt-1 text-2xl font-semibold tracking-tight break-words text-ink">
                {p.name}
              </h1>
            </div>
            <Card title="Website" description="What to scan, and proof that it is yours.">
              <SiteCard orgId={org.id} project={p} canEdit={canWrite} />
            </Card>
            <Card
              title="Repository"
              description="Where the code lives. KMR AI reads it to plan fixes and never changes your default branch."
            >
              <RepositoryCard
                orgId={org.id}
                projectId={p.id}
                repository={repository.data}
                canEdit={atLeast(org.role, "admin")}
              />
            </Card>
            <Card
              title="API checks"
              description="Compare what your API returns with what you expect."
            >
              <ApiChecksCard
                orgId={org.id}
                projectId={p.id}
                checks={checks.data}
                canWrite={canWrite}
                verified={p.site.verification.verified}
              />
            </Card>
            <Card
              title="Traffic (optional)"
              description="Your own page-view numbers, used only to rank issues on busy pages higher."
            >
              <TrafficCard
                orgId={org.id}
                projectId={p.id}
                traffic={traffic.data}
                canEdit={canWrite}
              />
            </Card>
            <Card title="Scan" description="Run a read-only scan of this website.">
              <StartScanButton
                orgId={org.id}
                projectId={p.id}
                blockedReason={blockedReason}
                activeScanId={active?.id ?? null}
                engines={options.kind === "ok" ? options.data.engines : ["chromium"]}
              />
            </Card>
            <section aria-labelledby="history-heading" className="flex flex-col gap-3">
              <h2 id="history-heading" className="text-base font-semibold text-ink">
                Scan history
              </h2>
              {scans.data.length === 0 ? (
                <p className="text-sm text-muted">No scans yet.</p>
              ) : (
                <ScanList scans={scans.data} showProject={false} />
              )}
            </section>
          </div>
        );
      }}
    </Authed>
  );
}

export default function ProjectPage({ params }: PageProps<"/projects/[projectId]">) {
  return (
    <Suspense fallback={<PageSkeleton label="Loading project" />}>
      <ProjectDetail params={params} />
    </Suspense>
  );
}
