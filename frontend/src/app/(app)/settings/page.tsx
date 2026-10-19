import type { Metadata } from "next";
import { Suspense } from "react";

import { Authed } from "@/components/auth/authed";
import { PageHeader } from "@/components/ui/page-header";
import { PageSkeleton } from "@/components/ui/page-skeleton";
import { SettingsContent } from "@/components/settings/settings-content";

export const metadata: Metadata = { title: "Settings" };

export default function SettingsPage() {
  return (
    <>
      <PageHeader title="Settings" description="Organization, members, roles, and your account." />
      <Suspense fallback={<PageSkeleton label="Loading settings" />}>
        <Authed>{(ctx) => <SettingsContent ctx={ctx} />}</Authed>
      </Suspense>
    </>
  );
}
