import type { Metadata } from "next";
import { Suspense } from "react";

import { Authed } from "@/components/auth/authed";
import { ProjectsContent } from "@/components/projects/projects-content";
import { PageHeader } from "@/components/ui/page-header";
import { PageSkeleton } from "@/components/ui/page-skeleton";

export const metadata: Metadata = { title: "Projects" };

export default function ProjectsPage() {
  return (
    <>
      <PageHeader
        title="Projects"
        description="Your organization's projects. Each one has a website to scan and, optionally, a repository."
      />
      <Suspense fallback={<PageSkeleton label="Loading projects" />}>
        <Authed>{(ctx) => <ProjectsContent ctx={ctx} />}</Authed>
      </Suspense>
    </>
  );
}
