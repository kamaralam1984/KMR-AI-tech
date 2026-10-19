import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { RegisterForm } from "@/components/auth/register-form";
import { Card } from "@/components/ui/card";
import { PageSkeleton } from "@/components/ui/page-skeleton";

export const metadata: Metadata = { title: "Create account" };

export default function Page() {
  return (
    <Card titleAs="h1" title="Create account" description="Set up KMR AI for your team.">
      <div className="flex flex-col gap-4">
        <Suspense fallback={<PageSkeleton label="Loading form" />}>
          <RegisterForm />
        </Suspense>
        <Link
          href="/login"
          className="inline-flex min-h-11 items-center text-sm font-medium text-primary-text underline underline-offset-2"
        >
          I already have an account
        </Link>
      </div>
    </Card>
  );
}
