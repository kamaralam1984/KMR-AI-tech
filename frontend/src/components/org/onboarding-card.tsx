import { Card } from "@/components/ui/card";

import { CreateOrgForm } from "./create-org-form";

export function OnboardingCard() {
  return (
    <Card
      title="Create your organization"
      description="Projects, members and scans belong to an organization. You will be its owner."
    >
      <CreateOrgForm />
    </Card>
  );
}
