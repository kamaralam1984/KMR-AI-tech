import type { ApiCheckReason, Observation, ScanStatus } from "./types";

/** Plain-language text for scan states, so the UI never shows a raw code. */
export const STATUS_LABEL: Record<ScanStatus, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const ACTIVE_STATUSES: ScanStatus[] = ["queued", "running"];

const ERROR_TEXT: Record<string, string> = {
  site_unreachable: "The site could not be loaded (it did not respond, or it redirected away).",
  timeout: "The scan took longer than the time limit and was stopped.",
  browser_unavailable: "The scanner browser was not available. Try again in a moment.",
  engine_unavailable:
    "The browser you chose is not available on this server. Scan again with Chromium.",
  not_authorized: "The person who started this scan no longer has access to the organization.",
  blocked_address: "The site's address points to a private network, which cannot be scanned.",
  blocked_port: "The site uses a port that cannot be scanned.",
  blocked_host: "The site's address is not allowed to be scanned.",
  dns_failed: "The site's name could not be looked up.",
  worker_lost: "The scanner stopped unexpectedly. Start a new scan.",
  internal_error: "Something went wrong on our side. Start a new scan.",
};

const STOP_TEXT: Record<string, string> = {
  page_limit: "Stopped at the page limit, so some pages were not scanned.",
  request_limit: "Stopped at the request limit.",
  byte_limit: "Stopped at the download size limit.",
  cancelled: "Cancelled.",
};

export function errorText(code: string | null): string | null {
  return code ? (ERROR_TEXT[code] ?? "The scan failed.") : null;
}

export function stopText(reason: string | null): string | null {
  return reason ? (STOP_TEXT[reason] ?? null) : null;
}

export function formatDuration(ms: number | undefined): string {
  if (ms === undefined) return "—";
  if (ms < 1000) return `${ms} ms`;
  const seconds = Math.round(ms / 1000);
  return seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
}

export function formatBytes(bytes: number | undefined): string {
  if (bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function formatWhen(iso: string | null): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(iso),
  );
}

const A11Y_TEXT: Record<string, (count: number) => string> = {
  images_missing_alt: (n) => `${n} image${n === 1 ? "" : "s"} without alternative text`,
  inputs_without_label: (n) => `${n} form field${n === 1 ? "" : "s"} without a label`,
  buttons_without_name: (n) => `${n} button${n === 1 ? "" : "s"} without a name`,
  links_without_name: (n) => `${n} link${n === 1 ? "" : "s"} without a name`,
  missing_lang: () => "The page does not declare its language",
  missing_title: () => "The page has no title",
  heading_structure: (n) => `The page has ${n} main headings (h1); one is expected`,
  missing_viewport_meta: () => "No viewport setting, so it may not scale well on phones",
};

const BLOCK_TEXT: Record<string, string> = {
  non_get_blocked: "A write request (POST/PUT/DELETE) was blocked: scans never change anything.",
  out_of_scope_navigation: "The page tried to open a different website. This was blocked.",
  out_of_scope_final_url: "The page ended up on a different website. This was blocked.",
  excluded_path: "A page you excluded was skipped.",
  unsupported_scheme: "A request with an unsupported address type was blocked.",
  proxy_blocked_address: "A request to a private or internal address was blocked.",
  proxy_blocked_host: "A request to an internal host name was blocked.",
  proxy_blocked_port: "A request to an unsupported port was blocked.",
};

/** One readable sentence for an observation. These are things the browser saw, not diagnoses. */
export function describe(o: Observation): string {
  const d = o.data;
  switch (o.kind) {
    case "page": {
      if (d.left_site) return String(d.note ?? "The page tried to leave the site.");
      if (d.error) return `Could not load the page: ${String(d.error)}`;
      const title = d.title ? `“${String(d.title)}”` : "Untitled page";
      return `${title}, HTTP ${String(d.status ?? "?")}${d.load_ms ? `, loaded in ${String(d.load_ms)} ms` : ""}`;
    }
    case "console_error":
      return `Console error: ${String(d.message ?? "")}`;
    case "page_error":
      return `Uncaught error: ${String(d.message ?? "")}`;
    case "failed_request":
      return `A ${String(d.resource_type ?? "request")} failed to load: ${String(d.url ?? "")}`;
    case "http_error":
      return `HTTP ${String(d.status ?? "?")} for a ${String(d.resource_type ?? "request")}: ${String(d.url ?? "")}`;
    case "blocked_request":
      return BLOCK_TEXT[String(d.reason)] ?? "A request was blocked by the scanner's safety rules.";
    case "a11y_issue": {
      const make = A11Y_TEXT[String(d.check)];
      return make ? make(Number(d.count ?? 1)) : "An accessibility signal was found.";
    }
    case "api_check": {
      const name = String(d.name ?? "API check");
      const why = Array.isArray(d.reasons) ? reasonsText(d.reasons as ApiCheckReason[]) : "";
      return d.status === "passed"
        ? `API check “${name}” passed (HTTP ${String(d.http_status ?? "?")}, ${String(d.latency_ms ?? "?")} ms)`
        : `API check “${name}” ${String(d.status)}: ${why}`;
    }
    case "limit":
      return d.stopped
        ? `Scan limit reached: ${String(d.stopped).replace("_", " ")}.`
        : "Some observations were not stored because there were too many.";
    default:
      return "Note.";
  }
}

const REASON_TEXT: Record<string, (r: ApiCheckReason) => string> = {
  status_mismatch: (r) => `expected HTTP ${String(r.expected)}, got ${String(r.actual)}`,
  too_slow: (r) => `took ${String(r.actual_ms)} ms, limit ${String(r.max_ms)} ms`,
  schema_mismatch: (r) => {
    const v = Array.isArray(r.violations) ? (r.violations as { path: string; rule: string }[]) : [];
    return `the response shape does not match (${v.map((x) => `${x.path}: ${x.rule}`).join("; ")})`;
  },
  invalid_json: () => "the response was not valid JSON",
  response_too_large: () => "the response was larger than 256 KB",
  auth_rejected: (r) => `the API rejected the credentials you provided (HTTP ${String(r.actual)})`,
  timeout: () => "the request timed out",
  network_error: () => "a network error occurred",
  proxy_unavailable: () => "the scanner's network gateway was not available",
  blocked: () => "the request was blocked by the scanner's safety rules",
  credentials_unreadable: () => "the stored credentials could not be read; enter them again",
};

/** Plain-language reasons for an API check result. Never includes response content. */
export function reasonsText(reasons: ApiCheckReason[]): string {
  if (reasons.length === 0) return "";
  return reasons.map((r) => REASON_TEXT[r.code]?.(r) ?? r.code.replaceAll("_", " ")).join("; ");
}

export const KIND_LABEL: Record<string, string> = {
  api_check: "API checks",
  page: "Pages",
  console_error: "Console errors",
  page_error: "Script errors",
  failed_request: "Failed requests",
  http_error: "HTTP errors",
  blocked_request: "Blocked by safety rules",
  a11y_issue: "Accessibility",
  limit: "Limits",
  note: "Notes",
};
