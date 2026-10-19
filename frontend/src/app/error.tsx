"use client";

import { ErrorState } from "@/components/ui/error-state";
import { Button } from "@/components/ui/button";

export default function GlobalError({ reset }: { error: Error; reset: () => void }) {
  return (
    <main id="main-content" tabIndex={-1} className="mx-auto max-w-xl px-4 py-16">
      <ErrorState
        title="Something went wrong"
        description="An unexpected error occurred. Details were not shown to protect your data. You can try again."
        action={<Button onClick={reset}>Try again</Button>}
      />
    </main>
  );
}
