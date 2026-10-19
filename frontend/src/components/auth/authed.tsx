import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { ErrorState } from "@/components/ui/error-state";
import { getSessionContext, type SessionContext } from "@/lib/session";

/**
 * Validates the session on the server for every request and hands the result to its child.
 * An invalid or expired session redirects to sign-in; a backend outage shows an error state
 * instead of an empty page. Must be rendered inside <Suspense> (it reads cookies).
 */
export async function Authed({
  children,
}: {
  children: (ctx: SessionContext) => ReactNode | Promise<ReactNode>;
}) {
  const result = await getSessionContext();
  if (result.kind === "unauthenticated") redirect("/login");
  if (result.kind !== "ok") {
    return (
      <ErrorState
        title="Cannot load your workspace"
        description="The server did not respond as expected. Check that the API is running, then reload this page."
      />
    );
  }
  return <>{await children(result.data)}</>;
}
