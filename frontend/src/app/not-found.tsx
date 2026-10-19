import { SearchX } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/ui/empty-state";

export default function NotFound() {
  return (
    <main id="main-content" tabIndex={-1} className="mx-auto max-w-xl px-4 py-16">
      <EmptyState
        icon={SearchX}
        title="Page not found"
        description="The page you are looking for does not exist or has moved."
        action={
          <Link
            href="/dashboard"
            className="inline-flex h-10 items-center rounded-md bg-primary px-4 text-sm font-medium text-white hover:bg-primary-strong"
          >
            Go to dashboard
          </Link>
        }
      />
    </main>
  );
}
