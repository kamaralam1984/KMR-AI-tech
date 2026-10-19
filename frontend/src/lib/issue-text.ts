import type { Severity } from "./types";

export const CATEGORY_LABEL: Record<string, string> = {
  frontend_error: "Page script error",
  backend_api: "Backend / API",
  network: "Network",
  accessibility: "Accessibility",
  performance: "Speed",
  content: "Content",
  other: "Other",
};

export const SEVERITY_ORDER: Severity[] = ["critical", "high", "medium", "low", "info"];

export const SEVERITY_LABEL: Record<Severity, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
  info: "Info",
};

export const STATE_LABEL: Record<string, string> = {
  open: "Open",
  stale: "No longer seen",
  fixed: "Fixed",
  not_fixed: "Not fixed",
  needs_review: "Needs review",
};

export const SOURCE_LABEL: Record<string, string> = {
  browser_observed: "Observed in the browser",
  api_observed: "Observed by an API check",
  customer_supplied: "Supplied by you",
  repository_derived: "Derived from your code",
};

export const CONFIDENCE_LABEL: Record<string, string> = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence",
};

const RUN_ERRORS: Record<string, string> = {
  no_api_key: "The AI provider is not configured on this server.",
  provider_unavailable: "The diagnosis service was temporarily unavailable.",
  provider_timeout: "The diagnosis service took too long to answer.",
  provider_rejected: "The diagnosis service rejected the request.",
  invalid_output: "The diagnosis service returned an answer that could not be used.",
};

export function runErrorText(code: string | null): string | null {
  return code ? (RUN_ERRORS[code] ?? "Diagnosis failed.") : null;
}

export function providerNote(isAi: boolean, model: string): string {
  return isAi
    ? `Written by an AI model (${model}). AI can be wrong: check each statement against its evidence.`
    : "Produced by the built-in rules, not by AI. Every statement is tied to the evidence listed below.";
}
