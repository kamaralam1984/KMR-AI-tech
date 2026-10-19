import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { LoginForm } from "@/components/auth/login-form";
import { Card } from "@/components/ui/card";
import { PageSkeleton } from "@/components/ui/page-skeleton";

export const metadata: Metadata = { title: "Sign in" };

export default function Page() {
  return (
    <Card titleAs="h1" title="Sign in" description="Sign in to your KMR AI workspace.">
      <div className="flex flex-col gap-4">
        <Suspense fallback={<PageSkeleton label="Loading form" />}>
          <LoginForm />
        </Suspense>
        <Link
          href="/register"
          className="inline-flex min-h-11 items-center text-sm font-medium text-primary-text underline underline-offset-2"
        >
          Create an account
        </Link>
      </div>
    </Card>
  );
}
