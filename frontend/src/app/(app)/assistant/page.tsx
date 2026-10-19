import type { Metadata } from "next";
import { Suspense } from "react";

import { AssistantContent } from "@/components/assistant/assistant-content";
import { Authed } from "@/components/auth/authed";
import { PageHeader } from "@/components/ui/page-header";
import { PageSkeleton } from "@/components/ui/page-skeleton";

export const metadata: Metadata = { title: "Assistant" };

export default function AssistantPage() {
  return (
    <>
      <PageHeader
        title="Assistant"
        description="A helper for your website's visitors. It quotes your own pages and says so when it cannot answer. No AI model is used."
      />
      <Suspense fallback={<PageSkeleton label="Loading assistant" />}>
        <Authed>{(ctx) => <AssistantContent ctx={ctx} />}</Authed>
      </Suspense>
    </>
  );
}
