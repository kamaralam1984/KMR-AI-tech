import { PlugZap } from "lucide-react";

import { Badge, type Tone } from "@/components/ui/badge";
import { ErrorState } from "@/components/ui/error-state";
import { TD, TH, THead, TR, Table } from "@/components/ui/table";
import { type CheckState, fetchReadiness } from "@/lib/api";

const checkLabels: Record<string, string> = { database: "Database", redis: "Job queue (Redis)" };

const stateView: Record<CheckState, { tone: Tone; text: string }> = {
  reachable: { tone: "success", text: "Reachable" },
  unreachable: { tone: "danger", text: "Unreachable" },
  not_configured: { tone: "neutral", text: "Not configured" },
};

/** Real data: the backend's own readiness report. Runs on the server at request time. */
export async function SystemStatus() {
  const result = await fetchReadiness();

  if (result.kind === "unreachable") {
    return (
      <ErrorState
        title="Cannot reach the KMR AI API"
        description="The dashboard could not get a status from the backend. Check that the API is running and that KMR_API_URL points to it."
      />
    );
  }

  const { readiness } = result;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <PlugZap className="size-5 text-muted" aria-hidden="true" />
        <p className="text-sm text-ink">API</p>
        <Badge tone="success">Responding</Badge>
        <Badge tone={readiness.status === "ready" ? "success" : "danger"}>
          {readiness.status === "ready" ? "Ready" : "Not ready"}
        </Badge>
      </div>
      <Table caption="Backend dependency status">
        <THead>
          <TR>
            <TH>Dependency</TH>
            <TH>Status</TH>
          </TR>
        </THead>
        <tbody>
          {Object.entries(readiness.checks).map(([name, state]) => (
            <TR key={name}>
              <TD>{checkLabels[name] ?? name}</TD>
              <TD>
                <Badge tone={stateView[state].tone}>{stateView[state].text}</Badge>
              </TD>
            </TR>
          ))}
        </tbody>
      </Table>
      <p className="text-xs text-muted">
        &ldquo;Reachable&rdquo; means the service accepts a network connection. It does not check
        credentials or data.
      </p>
    </div>
  );
}
