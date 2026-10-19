import { MessageSquareText } from "lucide-react";

import { OnboardingCard } from "@/components/org/onboarding-card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { atLeast } from "@/lib/org";
import { serverGet, type SessionContext } from "@/lib/session";
import type {
  AssistantHandoff,
  AssistantInstall,
  AssistantPage,
  LearningConsent,
} from "@/lib/types";

import { AssistantPanel } from "./assistant-panel";
import { CreateInstallCard } from "./create-install-card";

export async function AssistantContent({ ctx }: { ctx: SessionContext }) {
  const org = ctx.activeOrg;
  if (!org) return <OnboardingCard />;
  if (!atLeast(org.role, "admin")) {
    return (
      <EmptyState
        icon={MessageSquareText}
        title="Admins only"
        description="Installing and managing the visitor assistant needs the admin or owner role in this organization."
      />
    );
  }

  const base = `/organizations/${org.id}/assistant`;
  const installs = await serverGet<AssistantInstall[]>(`${base}/installs`);
  if (installs.kind !== "ok") {
    return (
      <ErrorState
        title="Cannot load the assistant"
        description="The installs could not be loaded. Reload the page, or switch organization from the account menu."
      />
    );
  }
  const details = await Promise.all(
    installs.data.map(async (install) => {
      const consents = install.modules.includes("learning")
        ? await serverGet<LearningConsent[]>(`${base}/installs/${install.id}/consents`)
        : null;
      const [pages, handoffs] = await Promise.all([
        serverGet<AssistantPage[]>(`${base}/installs/${install.id}/pages`),
        serverGet<AssistantHandoff[]>(`${base}/installs/${install.id}/handoffs`),
      ]);
      return {
        install,
        pages: pages.kind === "ok" ? pages.data : null,
        handoffs: handoffs.kind === "ok" ? handoffs.data : null,
        consents: consents === null ? undefined : consents.kind === "ok" ? consents.data : null,
      };
    }),
  );

  return (
    <div className="flex flex-col gap-6">
      <CreateInstallCard orgId={org.id} />
      {details.length === 0 ? (
        <EmptyState
          icon={MessageSquareText}
          title="No assistant installed"
          description="Create an install above, list the exact website addresses allowed to use it, then paste the snippet into your site."
        />
      ) : (
        details.map((d) => <AssistantPanel key={d.install.id} orgId={org.id} {...d} />)
      )}
    </div>
  );
}
