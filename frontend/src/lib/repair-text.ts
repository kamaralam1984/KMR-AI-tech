import type { Repair, RepairStatus } from "./types";

export const REPAIR_STATUS_LABEL: Record<RepairStatus, string> = {
  proposed: "Proposed",
  no_fix: "No fix proposed",
  rejected: "Rejected by safety checks",
  superseded: "Replaced by a newer plan",
};

export function sourceNote(repair: Pick<Repair, "source" | "model">): string {
  if (repair.source === "ai") {
    return `Written by an AI model (${repair.model}). Read the diff before you rely on it.`;
  }
  if (repair.source === "manual") return "Submitted by a person. Checked by the same safety rules.";
  return "Produced by the built-in rules, not by AI. It only handles a few mechanical fixes.";
}

const RULES: Record<string, string> = {
  path_not_allowed: "Touches a file that automatic repairs may not change",
  adds_secret: "Would add a credential to the code",
  deletion: "Would delete a file's content",
  no_change: "Does not change the file",
  too_many_files: "Changes too many files",
  too_large: "Is too large",
  empty: "Changes nothing",
  binary_content: "Contains binary content",
  invalid_content: "Is not text",
  file_not_shown: "Changes a file the proposer was not shown",
  unreadable_file: "Changes a file that cannot be read",
};

export function findingText(rule: string): string {
  return RULES[rule] ?? "Failed a safety check";
}

export function shortSha(sha: string): string {
  return sha.slice(0, 8);
}

export const CHECK_LABEL: Record<string, string> = {
  syntax: "File syntax (JSON, Python, HTML)",
  javascript_syntax: "JavaScript syntax",
  project_check: "Your project's own check",
};

export const CHECK_STATUS_LABEL = {
  passed: "Passed",
  failed: "Failed",
  skipped: "Skipped (did not run)",
  unavailable: "Unavailable (could not run)",
} as const;

export const OVERALL_TEXT = {
  passed: "All checks that ran passed.",
  failed: "At least one check failed. This change cannot be approved.",
  unavailable: "Some checks could not run. Nothing is counted as passed for them.",
  not_run: "No check actually ran, so nothing is verified.",
} as const;

export const APPROVAL_STATE_LABEL: Record<string, string> = {
  active: "Active",
  used: "Used",
  revoked: "Revoked",
  expired: "Expired",
  invalidated: "No longer valid (the plan changed)",
  rejected: "Rejected",
};
