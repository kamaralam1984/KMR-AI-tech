import { FolderKanban } from "lucide-react";

import { OnboardingCard } from "@/components/org/onboarding-card";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { atLeast } from "@/lib/org";
import { serverGet, type SessionContext } from "@/lib/session";
import type { Project } from "@/lib/types";

import { CreateProjectForm } from "./create-project-form";
import { ProjectList } from "./project-list";

export async function ProjectsContent({ ctx }: { ctx: SessionContext }) {
  const org = ctx.activeOrg;
  if (!org) return <OnboardingCard />;

  const result = await serverGet<Project[]>(`/organizations/${org.id}/projects`);
  if (result.kind !== "ok") {
    return (
      <ErrorState
        title="Cannot load projects"
        description="The projects could not be loaded. Reload the page, or switch organization from the account menu."
      />
    );
  }
  const canCreate = atLeast(org.role, "member");

  return (
    <div className="flex flex-col gap-6">
      {canCreate ? (
        <Card title="New project" description={`In ${org.name}`}>
          <CreateProjectForm orgId={org.id} />
        </Card>
      ) : (
        <p className="text-sm text-muted">
          You have view-only access to {org.name}, so you cannot create projects.
        </p>
      )}
      {result.data.length === 0 ? (
        <EmptyState
          icon={FolderKanban}
          title="No projects yet"
          description={
            canCreate
              ? "Create your first project above, then add the website you want to scan."
              : "Nobody has created a project in this organization yet."
          }
        />
      ) : (
        <ProjectList orgId={org.id} projects={result.data} canEdit={canCreate} />
      )}
    </div>
  );
}
