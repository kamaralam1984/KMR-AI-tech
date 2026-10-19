import { cookies } from "next/headers";

import { ORG_COOKIE, pickActiveOrg } from "./org";
import type { Me, OrgSummary } from "./types";

const API_URL = process.env.KMR_API_URL ?? "http://127.0.0.1:8000";

export type ServerResult<T> =
  | { kind: "ok"; data: T }
  | { kind: "unauthenticated" }
  | { kind: "not_found" }
  | { kind: "forbidden" }
  | { kind: "error" };

/** Server-side GET against the backend, forwarding the visitor's cookies. */
export async function serverGet<T>(path: string): Promise<ServerResult<T>> {
  const cookie = (await cookies()).toString();
  try {
    const response = await fetch(`${API_URL}/v1${path}`, {
      headers: { cookie },
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    if (response.status === 401) return { kind: "unauthenticated" };
    if (response.status === 404) return { kind: "not_found" };
    if (response.status === 403) return { kind: "forbidden" };
    if (!response.ok) return { kind: "error" };
    return { kind: "ok", data: (await response.json()) as T };
  } catch {
    return { kind: "error" };
  }
}

export type SessionContext = { me: Me; activeOrg: OrgSummary | null };

export async function getSessionContext(): Promise<ServerResult<SessionContext>> {
  const result = await serverGet<Me>("/auth/me");
  if (result.kind !== "ok") return result;
  const preferred = (await cookies()).get(ORG_COOKIE)?.value;
  return {
    kind: "ok",
    data: { me: result.data, activeOrg: pickActiveOrg(result.data.organizations, preferred) },
  };
}
