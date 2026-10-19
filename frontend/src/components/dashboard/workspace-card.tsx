import { OnboardingCard } from "@/components/org/onboarding-card";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { roleLabel } from "@/lib/org";
import { serverGet, type SessionContext } from "@/lib/session";
import type { Member, Project } from "@/lib/types";

/** Real data about the signed-in user's active organization. */
export async function WorkspaceCard({ ctx }: { ctx: SessionContext }) {
  const org = ctx.activeOrg;
  if (!org) return <OnboardingCard />;

  const [projects, members] = await Promise.all([
    serverGet<Project[]>(`/organizations/${org.id}/projects`),
    serverGet<Member[]>(`/organizations/${org.id}/members`),
  ]);
  const count = (result: { kind: string; data?: unknown[] }) =>
    result.kind === "ok" && result.data ? String(result.data.length) : "Unavailable";

  return (
    <Card title={org.name} description={`Signed in as ${ctx.me.user.display_name}`}>
      <dl className="grid grid-cols-3 gap-4 text-sm">
        <div>
          <dt className="text-muted">Your role</dt>
          <dd className="mt-1">
            <Badge tone={org.role === "owner" ? "info" : "neutral"}>{roleLabel(org.role)}</Badge>
          </dd>
        </div>
        <div>
          <dt className="text-muted">Projects</dt>
          <dd className="mt-1 text-xl font-semibold text-ink">{count(projects)}</dd>
        </div>
        <div>
          <dt className="text-muted">Members</dt>
          <dd className="mt-1 text-xl font-semibold text-ink">{count(members)}</dd>
        </div>
      </dl>
    </Card>
  );
}
