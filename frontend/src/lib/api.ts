const API_URL = process.env.KMR_API_URL ?? "http://127.0.0.1:8000";

export type CheckState = "not_configured" | "reachable" | "unreachable";

export type Readiness = {
  status: "ready" | "not_ready";
  checks: Record<string, CheckState>;
};

export type ReadinessResult = { kind: "ok"; readiness: Readiness } | { kind: "unreachable" };

function isReadiness(value: unknown): value is Readiness {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    (v.status === "ready" || v.status === "not_ready") &&
    typeof v.checks === "object" &&
    v.checks !== null
  );
}

/** Server-side only: asks the backend whether it and its dependencies are ready. */
export async function fetchReadiness(): Promise<ReadinessResult> {
  try {
    // 503 is a valid, informative answer ("not ready"), so it is parsed rather than rejected.
    const response = await fetch(`${API_URL}/ready`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    const body: unknown = await response.json();
    if (!isReadiness(body)) return { kind: "unreachable" };
    return { kind: "ok", readiness: body };
  } catch {
    return { kind: "unreachable" };
  }
}
