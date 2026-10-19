import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    EmailStr,
    Field,
    SecretStr,
    StringConstraints,
    ValidationInfo,
    field_validator,
)

from app.security.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
RoleName = Literal["owner", "admin", "member", "viewer"]


def _normalize_email(value: str) -> str:
    return value.strip().lower()


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    display_name: Name

    @field_validator("email")
    @classmethod
    def lower_email(cls, value: str) -> str:
        return _normalize_email(value)

    @field_validator("password")
    @classmethod
    def password_not_email(cls, value: str, info: ValidationInfo) -> str:
        if value.lower() == str(info.data.get("email", "")).lower():
            raise ValueError("Password must not be the same as the email address.")
        return value


class LoginIn(BaseModel):
    email: EmailStr
    # No length floor: a wrong-length password is simply a failed login, not a distinct error.
    password: str = Field(max_length=1024)

    @field_validator("email")
    @classmethod
    def lower_email(cls, value: str) -> str:
        return _normalize_email(value)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str


class OrgSummary(BaseModel):
    id: uuid.UUID
    name: str
    role: RoleName


class MeOut(BaseModel):
    user: UserOut
    organizations: list[OrgSummary]


class CsrfOut(BaseModel):
    csrf_token: str


class OrgCreateIn(BaseModel):
    name: Name


class OrgUpdateIn(BaseModel):
    name: Name


class OrgOut(BaseModel):
    id: uuid.UUID
    name: str
    role: RoleName
    created_at: datetime


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: RoleName


class MemberAddIn(BaseModel):
    email: EmailStr
    role: RoleName = "member"

    @field_validator("email")
    @classmethod
    def lower_email(cls, value: str) -> str:
        return _normalize_email(value)


class MemberRoleIn(BaseModel):
    role: RoleName


class ProjectIn(BaseModel):
    name: Name


Exclusion = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class SiteIn(BaseModel):
    site_url: str = Field(min_length=1, max_length=2048)
    # Adds an X-Request-ID header to same-origin requests during a scan, so backend logs can be
    # matched to what the browser saw. Never added to cross-origin requests.
    inject_request_id: bool = True
    exclusions: list[Exclusion] = Field(default_factory=list, max_length=50)
    max_pages: int = Field(default=10, ge=1, le=50)

    @field_validator("exclusions")
    @classmethod
    def exclusions_are_paths(cls, value: list[str]) -> list[str]:
        for item in value:
            if not item.startswith("/") or any(c.isspace() or ord(c) < 32 for c in item):
                raise ValueError("Each exclusion must be a path starting with / and no spaces.")
        return sorted(set(value))


class VerifyIn(BaseModel):
    method: Literal["dns", "file"]


class VerificationOut(BaseModel):
    verified: bool
    method: str | None
    verified_at: datetime | None
    dns_record_name: str | None
    dns_record_value: str | None
    file_url: str | None
    file_content: str | None


class SiteOut(BaseModel):
    site_url: str | None
    site_host: str | None
    inject_request_id: bool
    exclusions: list[str]
    max_pages: int
    verification: VerificationOut


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    site: SiteOut


class VerifyResultOut(BaseModel):
    verified: bool
    reason: str | None
    project: ProjectOut


class ScanOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str | None = None
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    site_url: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    stopped_reason: str | None
    cancel_requested: bool
    summary: dict[str, Any]
    limits: dict[str, Any]


class ScanDetailOut(ScanOut):
    counts: dict[str, int]
    severity_counts: dict[str, int]


class ObservationOut(BaseModel):
    id: uuid.UUID
    kind: str
    severity: str
    page_url: str | None
    data: dict[str, Any]
    evidence_id: uuid.UUID | None
    created_at: datetime


class ObservationPage(BaseModel):
    items: list[ObservationOut]
    total: int


class CredentialIn(BaseModel):
    header_name: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=512)


class ApiCheckIn(BaseModel):
    name: Name
    path: str = Field(min_length=1, max_length=1024)
    method: Literal["GET", "HEAD"] = "GET"
    expected_status: int = Field(default=200, ge=100, le=599)
    max_latency_ms: int = Field(default=3000, ge=50, le=30000)
    response_schema: dict[str, Any] | None = None
    credentials: CredentialIn | None = None


class ApiCheckPatch(BaseModel):
    name: Name | None = None
    path: str | None = Field(default=None, min_length=1, max_length=1024)
    method: Literal["GET", "HEAD"] | None = None
    expected_status: int | None = Field(default=None, ge=100, le=599)
    max_latency_ms: int | None = Field(default=None, ge=50, le=30000)
    response_schema: dict[str, Any] | None = None
    clear_schema: bool = False
    credentials: CredentialIn | None = None
    clear_credentials: bool = False


class ApiCheckRunOut(BaseModel):
    id: uuid.UUID
    check_id: uuid.UUID
    scan_id: uuid.UUID | None
    triggered_by: str
    status: Literal["passed", "failed", "error"]
    http_status: int | None
    latency_ms: int | None
    response_bytes: int | None
    reasons: list[dict[str, Any]]
    created_at: datetime


class ApiCheckOut(BaseModel):
    id: uuid.UUID
    name: str
    path: str
    method: str
    expected_status: int
    max_latency_ms: int
    response_schema: dict[str, Any] | None
    # The credential itself is never returned, only whether one is stored and its header name.
    has_credentials: bool
    auth_header_name: str | None
    last_run: ApiCheckRunOut | None
    created_at: datetime


class BackendLogsIn(BaseModel):
    format: Literal["jsonl", "text"]
    content: str = Field(min_length=1, max_length=1_000_000)


class BackendLogsResult(BaseModel):
    accepted: int
    rejected: int
    total_for_scan: int


class EvidenceSourceOut(BaseModel):
    key: Literal["browser", "api_checks", "customer_logs", "monitoring", "repository"]
    label: str
    state: Literal["available", "no_data", "not_connected"]
    detail: str


class EvidenceRefOut(BaseModel):
    source: Literal["browser_observed", "api_observed", "customer_supplied", "repository_derived"]
    ref_type: Literal["observation", "api_check_run", "log_entry"]
    ref_id: uuid.UUID
    summary: str


class CorrelationOut(BaseModel):
    observation_id: uuid.UUID
    kind: str
    page_url: str | None
    request_url: str | None
    http_status: int | None
    request_id: str | None
    classification: Literal["backend_confirmed", "uncertain", "no_backend_data", "third_party"]
    needs_review: bool
    statement: str
    sources: list[str]
    evidence: list[EvidenceRefOut]


class BackendEvidenceOut(BaseModel):
    backend_telemetry_available: bool
    notice: str | None
    sources: list[EvidenceSourceOut]
    correlations: list[CorrelationOut]


class FactOut(BaseModel):
    text: str
    evidence_ids: list[str]


class HypothesisOut(BaseModel):
    text: str
    confidence: Literal["high", "medium", "low"]
    evidence_ids: list[str]


class IssueEvidenceOut(BaseModel):
    id: str
    type: str
    source: str
    kind: str


class IssueSummaryOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    scan_id: uuid.UUID
    title: str
    category: str
    severity: Literal["critical", "high", "medium", "low", "info"]
    component: str
    state: str
    classification: Literal["diagnosed", "needs_review"]
    confidence: Literal["high", "medium", "low"]
    provider: str
    is_ai: bool
    created_at: datetime
    updated_at: datetime


class IssueOut(IssueSummaryOut):
    summary: str
    needs_review_reason: str | None
    facts: list[FactOut]
    hypotheses: list[HypothesisOut]
    unverified: list[str]
    reproduction_steps: list[str]
    impact: str
    proposed_fix: str
    verification_plan: str
    evidence: list[IssueEvidenceOut]
    model: str


class DiagnosisRunOut(BaseModel):
    id: uuid.UUID
    scan_id: uuid.UUID
    provider: str
    model: str
    is_ai: bool
    status: Literal["completed", "failed"]
    error_code: str | None
    issues_count: int
    dropped_issues: int
    flags: list[str]
    input_tokens: int | None
    output_tokens: int | None
    cost_micros: int | None
    latency_ms: int | None
    created_at: datetime


class DiagnoseOut(BaseModel):
    run: DiagnosisRunOut
    issues: list[IssueSummaryOut]


class AiStatusOut(BaseModel):
    provider: str
    model: str
    is_ai: bool
    label: str


class AiUsageOut(AiStatusOut):
    daily_runs_used: int
    daily_runs_limit: int
    month_runs: int
    month_cost_usd: float | None
    monthly_cost_ceiling_usd: float | None


class RepositoryIn(BaseModel):
    provider: Literal["local_git", "github"]
    location: str = Field(min_length=1, max_length=500)
    default_branch: str = Field(default="main", min_length=1, max_length=100)
    token: SecretStr | None = Field(default=None, max_length=300)
    check_command: str | None = Field(default=None, max_length=500)
    clear_check_command: bool = False


class RepositoryOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    provider: Literal["local_git", "github"]
    location: str
    default_branch: str
    has_token: bool
    check_command: str | None
    head_sha: str | None = None
    created_at: datetime
    updated_at: datetime


class RepairIn(BaseModel):
    """`changes`: full new content per path. Omit it to let the configured provider propose."""

    changes: dict[str, str] | None = Field(default=None, max_length=50)
    explanation: str = Field(default="", max_length=1500)


class RepairFileOut(BaseModel):
    path: str
    action: Literal["modify", "create"]
    additions: int
    deletions: int


class RepairFindingOut(BaseModel):
    rule: str
    message: str
    path: str | None


class RepairOut(BaseModel):
    id: uuid.UUID
    issue_id: uuid.UUID
    project_id: uuid.UUID
    status: Literal["proposed", "no_fix", "rejected", "superseded"]
    source: Literal["rules", "ai", "manual"]
    provider: str
    model: str
    is_ai: bool
    base_branch: str
    base_sha: str
    branch_name: str
    explanation: str
    files: list[RepairFileOut]
    diff: str
    diff_sha256: str
    findings: list[RepairFindingOut]
    warnings: list[str]
    created_at: datetime


class CheckResultOut(BaseModel):
    name: str
    status: Literal["passed", "failed", "skipped", "unavailable"]
    detail: str
    duration_ms: int


class CheckRunOut(BaseModel):
    id: uuid.UUID
    repair_id: uuid.UUID
    overall: Literal["passed", "failed", "unavailable", "not_run"]
    results: list[CheckResultOut]
    diff_sha256: str
    base_sha: str
    created_at: datetime


class ApprovalIn(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str = Field(default="", max_length=500)
    acknowledge_incomplete_checks: bool = False


class RevokeIn(BaseModel):
    reason: str = Field(default="", max_length=500)


class ApprovalOut(BaseModel):
    id: uuid.UUID
    repair_id: uuid.UUID
    decision: Literal["approved", "rejected"]
    state: Literal["active", "used", "revoked", "expired", "invalidated", "rejected"]
    action: str
    approver_id: uuid.UUID
    issue_ids: list[str]
    base_branch: str
    branch_name: str
    base_sha: str
    diff_sha256: str
    bound_hash: str
    acknowledged_incomplete_checks: bool
    note: str
    expires_at: datetime
    revoked_at: datetime | None
    revoked_reason: str | None
    consumed_at: datetime | None
    created_at: datetime


class ApplyIn(BaseModel):
    approval_id: uuid.UUID


class ApplyOut(BaseModel):
    id: uuid.UUID
    repair_id: uuid.UUID
    approval_id: uuid.UUID
    status: Literal["applied", "failed"]
    branch_name: str
    commit_sha: str | None
    base_sha: str
    diff_sha256: str
    error_code: str | None
    message: str
    created_at: datetime


class AuditEventOut(BaseModel):
    id: uuid.UUID
    chain_seq: int | None
    action: str
    outcome: str
    actor_user_id: uuid.UUID | None
    target_type: str | None
    target_id: str | None
    details: dict[str, Any]
    entry_hash: str | None
    created_at: datetime


class AuditVerifyOut(BaseModel):
    ok: bool
    checked: int
    unchained: int
    tip: str | None
    broken_at: int | None
    reason: str | None


class FixVerifyIn(BaseModel):
    after_scan_id: uuid.UUID


class FixVerificationOut(BaseModel):
    id: uuid.UUID
    issue_id: uuid.UUID
    repair_id: uuid.UUID | None
    before_scan_id: uuid.UUID
    after_scan_id: uuid.UUID
    outcome: Literal["fixed", "not_fixed", "needs_review"]
    reasons: list[str]
    details: dict[str, Any]
    verified_diff_sha256: str | None
    via_kmr_ai: bool
    created_at: datetime


class DashboardOut(BaseModel):
    projects: int
    scans: dict[str, Any]
    issues: dict[str, Any]
    repairs: dict[str, int]
    verifications: dict[str, int]


class UsageOut(BaseModel):
    scans_last_24h: int
    scans_daily_limit: int
    diagnosis_runs_last_24h: int
    diagnosis_daily_limit: int
    diagnosis_runs_this_month: int
    ai_cost_usd_this_month: float | None
    ai_monthly_cost_ceiling_usd: float | None
    repair_plans_last_24h: int
    repair_plans_daily_limit: int
    check_runs_last_hour: int
    check_runs_hourly_limit: int


class SystemOut(BaseModel):
    database: Literal["ok"]
    queue: Literal["connected", "not_configured"]
    scans_queued: int
    oldest_queued_seconds: int | None
    scans_running: int
    scans_stalled: int
    last_scan_finished_at: datetime | None
    sandbox_docker_cli: bool
    scanner_mode: str
    ai_provider: str


class ImpactOut(BaseModel):
    score: int
    journey: str
    journey_label: str
    page: str | None
    reasons: list[str]
    basis: Literal["heuristic", "heuristic+customer_traffic"] = "heuristic"
    measured: bool = False
    traffic: dict[str, object] | None = None


class RankedIssueOut(BaseModel):
    issue: IssueSummaryOut
    impact: ImpactOut


class GithubInfo(BaseModel):
    available: bool
    reason: str | None = None
    repository: str | None = None
    existing_url: str | None = None


class TicketOut(BaseModel):
    format: Literal["markdown"] = "markdown"
    title: str
    filename: str
    content: str
    content_sha256: str
    github: GithubInfo
    notice: str
