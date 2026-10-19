export type Role = "owner" | "admin" | "member" | "viewer";

export type User = { id: string; email: string; display_name: string };
export type OrgSummary = { id: string; name: string; role: Role };
export type Me = { user: User; organizations: OrgSummary[] };
export type Org = OrgSummary & { created_at: string };
export type Member = { user_id: string; email: string; display_name: string; role: Role };
export type Verification = {
  verified: boolean;
  method: "dns" | "file" | null;
  verified_at: string | null;
  dns_record_name: string | null;
  dns_record_value: string | null;
  file_url: string | null;
  file_content: string | null;
};
export type Site = {
  site_url: string | null;
  site_host: string | null;
  inject_request_id: boolean;
  exclusions: string[];
  max_pages: number;
  verification: Verification;
};
export type Project = {
  id: string;
  name: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  site: Site;
};
export type VerifyResult = { verified: boolean; reason: string | null; project: Project };

export type ScanStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
export type Scan = {
  id: string;
  project_id: string;
  project_name: string | null;
  status: ScanStatus;
  site_url: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  stopped_reason: string | null;
  cancel_requested: boolean;
  summary: {
    pages_scanned?: number;
    pages_failed?: number;
    requests?: number;
    bytes?: number;
    duration_ms?: number;
    observations_dropped?: number;
  };
  limits: Record<string, unknown>;
};
export type ScanDetail = Scan & {
  counts: Record<string, number>;
  severity_counts: Record<string, number>;
};
export type Observation = {
  id: string;
  kind: string;
  severity: "info" | "warning" | "error";
  page_url: string | null;
  data: Record<string, unknown>;
  evidence_id: string | null;
  created_at: string;
};
export type ObservationPage = { items: Observation[]; total: number };

export type ApiCheckReason = { code: string; [key: string]: unknown };
export type ApiCheckRun = {
  id: string;
  check_id: string;
  scan_id: string | null;
  triggered_by: "manual" | "scan";
  status: "passed" | "failed" | "error";
  http_status: number | null;
  latency_ms: number | null;
  response_bytes: number | null;
  reasons: ApiCheckReason[];
  created_at: string;
};
export type ApiCheck = {
  id: string;
  name: string;
  path: string;
  method: "GET" | "HEAD";
  expected_status: number;
  max_latency_ms: number;
  response_schema: Record<string, unknown> | null;
  has_credentials: boolean;
  auth_header_name: string | null;
  last_run: ApiCheckRun | null;
  created_at: string;
};

export type EvidenceSource = {
  key: "browser" | "api_checks" | "customer_logs" | "monitoring" | "repository";
  label: string;
  state: "available" | "no_data" | "not_connected";
  detail: string;
};
export type EvidenceRef = {
  source: "browser_observed" | "api_observed" | "customer_supplied" | "repository_derived";
  ref_type: "observation" | "api_check_run" | "log_entry";
  ref_id: string;
  summary: string;
};
export type Correlation = {
  observation_id: string;
  kind: string;
  page_url: string | null;
  request_url: string | null;
  http_status: number | null;
  request_id: string | null;
  classification: "backend_confirmed" | "uncertain" | "no_backend_data" | "third_party";
  needs_review: boolean;
  statement: string;
  sources: string[];
  evidence: EvidenceRef[];
};
export type BackendEvidence = {
  backend_telemetry_available: boolean;
  notice: string | null;
  sources: EvidenceSource[];
  correlations: Correlation[];
};

export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type IssueSummary = {
  id: string;
  project_id: string;
  scan_id: string;
  title: string;
  category: string;
  severity: Severity;
  component: string;
  state: string;
  classification: "diagnosed" | "needs_review";
  confidence: "high" | "medium" | "low";
  provider: string;
  is_ai: boolean;
  created_at: string;
  updated_at: string;
};
export type Issue = IssueSummary & {
  summary: string;
  needs_review_reason: string | null;
  facts: { text: string; evidence_ids: string[] }[];
  hypotheses: { text: string; confidence: "high" | "medium" | "low"; evidence_ids: string[] }[];
  unverified: string[];
  reproduction_steps: string[];
  impact: string;
  proposed_fix: string;
  verification_plan: string;
  evidence: { id: string; type: string; source: string; kind: string }[];
  model: string;
};
export type DiagnosisRun = {
  id: string;
  scan_id: string;
  provider: string;
  model: string;
  is_ai: boolean;
  status: "completed" | "failed";
  error_code: string | null;
  issues_count: number;
  dropped_issues: number;
  flags: string[];
  input_tokens: number | null;
  output_tokens: number | null;
  cost_micros: number | null;
  latency_ms: number | null;
  created_at: string;
};
export type AiStatus = { provider: string; model: string; is_ai: boolean; label: string };
export type AiUsage = AiStatus & {
  daily_runs_used: number;
  daily_runs_limit: number;
  month_runs: number;
  month_cost_usd: number | null;
  monthly_cost_ceiling_usd: number | null;
};

export type Repository = {
  id: string;
  project_id: string;
  provider: "local_git" | "github";
  location: string;
  default_branch: string;
  has_token: boolean;
  check_command: string | null;
  head_sha: string | null;
  created_at: string;
  updated_at: string;
};
export type RepairStatus = "proposed" | "no_fix" | "rejected" | "superseded";
export type Repair = {
  id: string;
  issue_id: string;
  project_id: string;
  status: RepairStatus;
  source: "rules" | "ai" | "manual";
  provider: string;
  model: string;
  is_ai: boolean;
  base_branch: string;
  base_sha: string;
  branch_name: string;
  explanation: string;
  files: { path: string; action: "modify" | "create"; additions: number; deletions: number }[];
  diff: string;
  diff_sha256: string;
  findings: { rule: string; message: string; path: string | null }[];
  warnings: string[];
  created_at: string;
};

export type CheckStatus = "passed" | "failed" | "skipped" | "unavailable";
export type CheckRun = {
  id: string;
  repair_id: string;
  overall: "passed" | "failed" | "unavailable" | "not_run";
  results: { name: string; status: CheckStatus; detail: string; duration_ms: number }[];
  diff_sha256: string;
  base_sha: string;
  created_at: string;
};
export type Approval = {
  id: string;
  repair_id: string;
  decision: "approved" | "rejected";
  state: "active" | "used" | "revoked" | "expired" | "invalidated" | "rejected";
  branch_name: string;
  base_sha: string;
  diff_sha256: string;
  acknowledged_incomplete_checks: boolean;
  note: string;
  expires_at: string;
  revoked_reason: string | null;
  created_at: string;
};
export type ApplyResult = {
  id: string;
  status: "applied" | "failed";
  branch_name: string;
  commit_sha: string | null;
  message: string;
};

export type FixVerification = {
  id: string;
  issue_id: string;
  repair_id: string | null;
  before_scan_id: string;
  after_scan_id: string;
  outcome: "fixed" | "not_fixed" | "needs_review";
  reasons: string[];
  details: {
    still_present?: boolean;
    pages_checked?: string[];
    pages_not_retested?: string[];
    pages_not_loading?: string[];
    new_issues?: { title: string; severity: string }[];
    before_issue_count?: number;
    after_issue_count?: number;
  };
  verified_diff_sha256: string | null;
  via_kmr_ai: boolean;
  created_at: string;
};

export type DashboardData = {
  projects: number;
  scans: {
    total: number;
    completed: number;
    failed: number;
    in_progress: number;
    last_completed_at: string | null;
  };
  issues: {
    open: number;
    needs_review: number;
    fixed: number;
    not_fixed: number;
    stale: number;
    open_by_severity: Record<string, number>;
  };
  repairs: {
    awaiting_decision: number;
    no_fix: number;
    rejected_by_rules: number;
    branches_created: number;
  };
  verifications: { fixed: number; not_fixed: number; needs_review: number };
};
export type UsageData = {
  scans_last_24h: number;
  scans_daily_limit: number;
  diagnosis_runs_last_24h: number;
  diagnosis_daily_limit: number;
  diagnosis_runs_this_month: number;
  ai_cost_usd_this_month: number | null;
  ai_monthly_cost_ceiling_usd: number | null;
  repair_plans_last_24h: number;
  repair_plans_daily_limit: number;
  check_runs_last_hour: number;
  check_runs_hourly_limit: number;
};
export type SystemData = {
  database: string;
  queue: "connected" | "not_configured";
  scans_queued: number;
  oldest_queued_seconds: number | null;
  scans_running: number;
  scans_stalled: number;
  last_scan_finished_at: string | null;
  sandbox_docker_cli: boolean;
  scanner_mode: string;
  ai_provider: string;
};
export type AuditEvent = {
  id: string;
  chain_seq: number | null;
  action: string;
  outcome: string;
  actor_user_id: string | null;
  target_type: string | null;
  target_id: string | null;
  details: Record<string, unknown>;
  entry_hash: string | null;
  created_at: string;
};
export type AuditVerify = {
  ok: boolean;
  checked: number;
  unchained: number;
  tip: string | null;
  broken_at: number | null;
  reason: string | null;
};

export type AssistantInstall = {
  id: string;
  name: string;
  key_prefix: string;
  allowed_origins: string[];
  modules: string[];
  enabled: boolean;
  revoked: boolean;
  consent_text: string;
  handoff_retention_days: number;
  child_safeguards_acknowledged: boolean;
  learning_consent_required: boolean;
  created_at: string;
  snippet: string;
};

export type AssistantPage = {
  id: string;
  kind: string;
  title: string;
  url: string | null;
  source: string;
  chunk_count: number;
  blocked_chunks: number;
  updated_at: string;
};

export type AssistantHandoff = {
  id: string;
  install_id: string;
  summary: string;
  contact: string | null;
  status: string;
  consent_at: string;
  expires_at: string;
  created_at: string;
};

export type Impact = {
  score: number;
  journey: string;
  journey_label: string;
  page: string | null;
  reasons: string[];
  basis: "heuristic" | "heuristic+customer_traffic";
  measured: boolean;
  traffic: {
    source: "customer_supplied";
    source_label: string;
    period: string;
    views: number;
    share_percent: number;
    factor: number;
  } | null;
};
export type RankedIssue = { issue: IssueSummary; impact: Impact };
export type Ticket = {
  format: "markdown";
  title: string;
  filename: string;
  content: string;
  content_sha256: string;
  github: {
    available: boolean;
    reason: string | null;
    repository: string | null;
    existing_url: string | null;
  };
  notice: string;
};
export type TrafficSummary = {
  present: boolean;
  source_label: string | null;
  period_start: string | null;
  period_end: string | null;
  total_views: number;
  pages: number;
  rows_rejected: number;
  top_pages: { path: string; views: number; share_percent: number }[];
  notice: string;
};
export type CompareFinding = { kind: string; page: string; severity: string };
export type ScanCompare = {
  basis: string;
  first: { scan_id: string; profile: { key: string; label: string }; findings: number };
  second: { scan_id: string; profile: { key: string; label: string }; findings: number };
  only_in_first: CompareFinding[];
  only_in_second: CompareFinding[];
  in_both: number;
  load_ms: { page: string; first: number | null; second: number | null }[];
};

export type ExtensionToken = {
  id: string;
  name: string;
  token_prefix: string;
  kind: "extension" | "desktop";
  user_id: string;
  expires_at: string;
  revoked: boolean;
  last_used_at: string | null;
  created_at: string;
};

export type LearningConsent = {
  id: string;
  label: string;
  age_band: string;
  consent_basis: string;
  expires_at: string;
  revoked: boolean;
  created_at: string;
};
