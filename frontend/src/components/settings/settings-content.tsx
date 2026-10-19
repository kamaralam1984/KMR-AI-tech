import { OnboardingCard } from "@/components/org/onboarding-card";
import { CreateOrgForm } from "@/components/org/create-org-form";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { atLeast, roleLabel } from "@/lib/org";
import { serverGet, type SessionContext } from "@/lib/session";
import type { AiUsage, ExtensionToken, Member, SystemData, UsageData } from "@/lib/types";

import { ExtensionTokensCard } from "./extension-tokens-card";
import { AiUsageCard } from "./ai-usage-card";
import { UsageCard, WorkerCard } from "./system-cards";
import { MembersPanel } from "./members-panel";
import { OrgNameForm } from "./org-name-form";

export async function SettingsContent({ ctx }: { ctx: SessionContext }) {
  const { me, activeOrg: org } = ctx;
  const account = (
    <Card title="Your account">
      <dl className="grid gap-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-muted">Name</dt>
          <dd className="font-medium text-ink">{me.user.display_name}</dd>
        </div>
        <div>
          <dt className="text-muted">Email</dt>
          <dd className="font-medium break-all text-ink">{me.user.email}</dd>
        </div>
      </dl>
      <p className="mt-4 text-sm text-muted">
        Sign out from the account menu at the top. Password change and two-step verification are not
        available yet.
      </p>
    </Card>
  );

  if (!org) {
    return (
      <div className="flex flex-col gap-6">
        {account}
        <OnboardingCard />
      </div>
    );
  }

  const members = await serverGet<Member[]>(`/organizations/${org.id}/members`);
  const admin = atLeast(org.role, "admin");
  const usage = admin ? await serverGet<AiUsage>(`/organizations/${org.id}/ai-usage`) : null;
  const limits = admin ? await serverGet<UsageData>(`/organizations/${org.id}/usage`) : null;
  const system = admin ? await serverGet<SystemData>(`/organizations/${org.id}/system`) : null;
  const tokens = atLeast(org.role, "member")
    ? await serverGet<ExtensionToken[]>(`/organizations/${org.id}/extension-tokens`)
    : null;
  return (
    <div className="flex flex-col gap-6">
      {account}
      <Card title="Organization" description={`Your role: ${roleLabel(org.role)}`}>
        <OrgNameForm orgId={org.id} name={org.name} canEdit={atLeast(org.role, "admin")} />
      </Card>
      <Card
        title="Members"
        description={
          atLeast(org.role, "admin")
            ? "Owners manage everyone. Admins manage members and viewers."
            : "Only owners and admins can change members."
        }
      >
        {members.kind === "ok" ? (
          <MembersPanel
            orgId={org.id}
            myUserId={me.user.id}
            myRole={org.role}
            members={members.data}
          />
        ) : (
          <ErrorState
            title="Cannot load members"
            description="Reload the page. If this keeps happening, check that the API is running."
          />
        )}
      </Card>
      {tokens?.kind === "ok" ? <ExtensionTokensCard orgId={org.id} tokens={tokens.data} /> : null}
      {limits?.kind === "ok" ? <UsageCard usage={limits.data} /> : null}
      {system?.kind === "ok" ? <WorkerCard system={system.data} /> : null}
      {usage?.kind === "ok" ? <AiUsageCard usage={usage.data} /> : null}
      <Card
        title="Another organization"
        description="You can belong to several organizations and switch between them from the account menu."
      >
        <CreateOrgForm submitLabel="Create another organization" />
      </Card>
      <div className="flex items-center gap-2 text-sm text-muted">
        <Badge tone="neutral">Not available yet</Badge>
        Email invitations, password reset and two-step verification.
      </div>
    </div>
  );
}
