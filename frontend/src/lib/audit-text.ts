export const ACTION_LABEL: Record<string, string> = {
  "repository.connect": "Repository connected",
  "repository.disconnect": "Repository disconnected",
  "repair.plan": "Repair planned",
  "repair.checks": "Checks run",
  "repair.apply": "Branch creation",
  "approval.approved": "Change approved",
  "approval.rejected": "Change rejected",
  "approval.revoke": "Approval revoked",
  "approval.decide": "Approval refused",
  "verification.run": "Fix verification",
  "org.create": "Organization created",
  "project.create": "Project created",
  "ai.cost_ceiling_reached": "AI spending limit reached",
};

export const FILTER_ACTIONS = [
  "repair.plan",
  "repair.checks",
  "approval.approved",
  "approval.rejected",
  "approval.revoke",
  "repair.apply",
  "verification.run",
  "repository.connect",
] as const;

export function actionLabel(action: string): string {
  return ACTION_LABEL[action] ?? action;
}

export const VERIFY_REASON: Record<string, string> = {
  event_modified: "An event was changed after it was written.",
  missing_event: "An event is missing from the sequence.",
  broken_link: "The chain of hashes is broken.",
};

export function detailsSummary(details: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const key of ["result", "status", "overall", "code", "decision", "error"]) {
    const value = details[key];
    if (typeof value === "string" && value) parts.push(`${key.replaceAll("_", " ")}: ${value}`);
  }
  const hash = details["diff_sha256"];
  if (typeof hash === "string" && hash) parts.push(`change ${hash.slice(0, 8)}`);
  return parts.join(" · ");
}
