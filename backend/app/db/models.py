"""ORM models. Row-Level Security policies and grants live in the migration, not here."""

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROLES = ("owner", "admin", "member", "viewer")


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text, server_default="")
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime] = _created_at()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created_at()


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = _created_at()


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()
    site_url: Mapped[str | None] = mapped_column(Text)
    site_host: Mapped[str | None] = mapped_column(Text)
    verification_token: Mapped[str] = mapped_column(
        Text, server_default=text("replace(gen_random_uuid()::text, '-', '')")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_method: Mapped[str | None] = mapped_column(String(8))
    verified_host: Mapped[str | None] = mapped_column(Text)
    scan_exclusions: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    scan_max_pages: Mapped[int] = mapped_column(server_default=text("10"))
    inject_request_id: Mapped[bool] = mapped_column(server_default=text("true"))

    @property
    def is_verified(self) -> bool:
        return (
            self.verified_at is not None
            and self.site_host is not None
            and self.verified_host == self.site_host
        )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # SHA-256 of the cookie token. The raw token is never stored.
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    created_at: Mapped[datetime] = _created_at()
    last_seen_at: Mapped[datetime] = _created_at()
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(Text)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (CheckConstraint("outcome in ('success','denied','failure')"),)

    # id and created_at are generated here, not by the database, so the INSERT needs no
    # RETURNING clause: RETURNING would also have to pass the SELECT policy, which org-less
    # rows (for example failed logins) deliberately do not.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(16))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    # Hash chain over an organization's events (see app/db/audit_chain.py). Null for events
    # without an organization and for rows written before the chain existed.
    chain_seq: Mapped[int | None] = mapped_column(BigInteger)
    prev_hash: Mapped[str | None] = mapped_column(Text)
    entry_hash: Mapped[str | None] = mapped_column(Text)


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID]
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16), server_default="queued")
    site_url: Mapped[str] = mapped_column(Text)
    allowed_host: Mapped[str] = mapped_column(Text)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(Text)
    stopped_reason: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[uuid.UUID] = mapped_column(server_default=text("gen_random_uuid()"))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    queued_at: Mapped[datetime] = _created_at()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    scan_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int]
    sha256: Mapped[str] = mapped_column(Text)
    page_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Observation(Base):
    __tablename__ = "observations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    scan_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text, default="info")
    page_url: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evidence.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ApiCheck(Base):
    __tablename__ = "api_checks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID]
    project_id: Mapped[uuid.UUID]
    name: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(String(8), server_default="GET")
    expected_status: Mapped[int] = mapped_column(server_default=text("200"))
    max_latency_ms: Mapped[int] = mapped_column(server_default=text("3000"))
    response_schema: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    auth_header_name: Mapped[str | None] = mapped_column(Text)
    auth_secret_ct: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()


class ApiCheckRun(Base):
    __tablename__ = "api_check_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    check_id: Mapped[uuid.UUID]
    scan_id: Mapped[uuid.UUID | None]
    triggered_by: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    http_status: Mapped[int | None]
    latency_ms: Mapped[int | None]
    response_bytes: Mapped[int | None]
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    request_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class BackendLogEntry(Base):
    __tablename__ = "backend_log_entries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    scan_id: Mapped[uuid.UUID]
    ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    level: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None]
    path: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DiagnosisRun(Base):
    __tablename__ = "diagnosis_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    scan_id: Mapped[uuid.UUID]
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    config_version: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    input_items: Mapped[int] = mapped_column(default=0)
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    latency_ms: Mapped[int | None]
    issues_count: Mapped[int] = mapped_column(default=0)
    dropped_issues: Mapped[int] = mapped_column(default=0)
    flags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Issue(Base):
    __tablename__ = "issues"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    project_id: Mapped[uuid.UUID]
    scan_id: Mapped[uuid.UUID]
    run_id: Mapped[uuid.UUID]
    fingerprint: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    component: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(Text, default="open")
    classification: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(Text)
    needs_review_reason: Mapped[str | None] = mapped_column(Text)
    facts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    hypotheses: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    unverified: Mapped[list[str]] = mapped_column(JSONB, default=list)
    reproduction_steps: Mapped[list[str]] = mapped_column(JSONB, default=list)
    impact: Mapped[str] = mapped_column(Text, default="")
    proposed_fix: Mapped[str] = mapped_column(Text, default="")
    verification_plan: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class RepositoryConnection(Base):
    __tablename__ = "repository_connections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    project_id: Mapped[uuid.UUID]
    provider: Mapped[str] = mapped_column(Text)
    location: Mapped[str] = mapped_column(Text)
    default_branch: Mapped[str] = mapped_column(Text)
    token_ct: Mapped[bytes | None] = mapped_column(LargeBinary)
    check_command: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class RepairPlan(Base):
    __tablename__ = "repair_plans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    project_id: Mapped[uuid.UUID]
    issue_id: Mapped[uuid.UUID]
    connection_id: Mapped[uuid.UUID]
    requested_by: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    base_branch: Mapped[str] = mapped_column(Text)
    base_sha: Mapped[str] = mapped_column(Text)
    branch_name: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text, default="")
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    diff: Mapped[str] = mapped_column(Text, default="")
    diff_sha256: Mapped[str] = mapped_column(Text, default="")
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class CheckRun(Base):
    __tablename__ = "check_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    repair_id: Mapped[uuid.UUID]
    requested_by: Mapped[uuid.UUID]
    diff_sha256: Mapped[str] = mapped_column(Text)
    base_sha: Mapped[str] = mapped_column(Text)
    overall: Mapped[str] = mapped_column(Text)
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    repair_id: Mapped[uuid.UUID]
    project_id: Mapped[uuid.UUID]
    approver_id: Mapped[uuid.UUID]
    decision: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    issue_ids: Mapped[list[str]] = mapped_column(JSONB)
    repo_provider: Mapped[str] = mapped_column(Text)
    repo_location: Mapped[str] = mapped_column(Text)
    base_branch: Mapped[str] = mapped_column(Text)
    branch_name: Mapped[str] = mapped_column(Text)
    base_sha: Mapped[str] = mapped_column(Text)
    diff_sha256: Mapped[str] = mapped_column(Text)
    bound_hash: Mapped[str] = mapped_column(Text)
    check_run_id: Mapped[uuid.UUID | None]
    acknowledged_incomplete_checks: Mapped[bool] = mapped_column(default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[uuid.UUID | None]
    revoked_reason: Mapped[str | None] = mapped_column(Text)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class RepairApplication(Base):
    __tablename__ = "repair_applications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    repair_id: Mapped[uuid.UUID]
    approval_id: Mapped[uuid.UUID]
    applied_by: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(Text)
    branch_name: Mapped[str] = mapped_column(Text)
    commit_sha: Mapped[str | None] = mapped_column(Text)
    base_sha: Mapped[str] = mapped_column(Text)
    diff_sha256: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class VerificationRun(Base):
    __tablename__ = "verification_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    project_id: Mapped[uuid.UUID]
    issue_id: Mapped[uuid.UUID]
    repair_id: Mapped[uuid.UUID | None]
    application_id: Mapped[uuid.UUID | None]
    before_scan_id: Mapped[uuid.UUID]
    after_scan_id: Mapped[uuid.UUID]
    requested_by: Mapped[uuid.UUID]
    outcome: Mapped[str] = mapped_column(Text)
    reasons: Mapped[list[str]] = mapped_column(JSONB, default=list)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    verified_diff_sha256: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class WidgetInstall(Base):
    __tablename__ = "widget_installs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    project_id: Mapped[uuid.UUID | None]
    name: Mapped[str] = mapped_column(Text)
    key_hash: Mapped[bytes] = mapped_column(LargeBinary)
    key_prefix: Mapped[str] = mapped_column(Text)
    allowed_origins: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    modules: Mapped[list[str]] = mapped_column(ARRAY(Text), default=lambda: ["guide"])
    enabled: Mapped[bool] = mapped_column(default=True)
    consent_text: Mapped[str] = mapped_column(Text, default="")
    handoff_retention_days: Mapped[int] = mapped_column(default=30)
    learning_ack_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    learning_ack_by: Mapped[uuid.UUID | None]
    learning_consent_required: Mapped[bool] = mapped_column(default=True)
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KbPage(Base):
    __tablename__ = "kb_pages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    install_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(default=0)
    blocked_chunks: Mapped[int] = mapped_column(default=0)
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class KbChunk(Base):
    __tablename__ = "kb_chunks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    install_id: Mapped[uuid.UUID]
    page_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(Text)
    idx: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    tokens: Mapped[list[str]] = mapped_column(ARRAY(Text))
    blocked: Mapped[bool] = mapped_column(default=False)


class FormDefinitionRow(Base):
    __tablename__ = "form_definitions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    install_id: Mapped[uuid.UUID]
    name: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Handoff(Base):
    __tablename__ = "handoffs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    install_id: Mapped[uuid.UUID]
    summary: Mapped[str] = mapped_column(Text)
    contact: Mapped[str | None] = mapped_column(Text)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, default="new")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ExtensionToken(Base):
    __tablename__ = "extension_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]
    name: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary)
    token_prefix: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, default="extension")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class LearningConsent(Base):
    __tablename__ = "learning_consents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    install_id: Mapped[uuid.UUID]
    label: Mapped[str] = mapped_column(Text)
    age_band: Mapped[str] = mapped_column(Text)
    consent_basis: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class TrafficImport(Base):
    __tablename__ = "traffic_imports"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    project_id: Mapped[uuid.UUID]
    path: Mapped[str] = mapped_column(Text)
    views: Mapped[int] = mapped_column(BigInteger)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    source_label: Mapped[str] = mapped_column(Text)
    imported_by: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class IssueExport(Base):
    __tablename__ = "issue_exports"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID]
    issue_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(Text)
    external_url: Mapped[str] = mapped_column(Text)
    external_number: Mapped[int | None]
    content_sha256: Mapped[str] = mapped_column(Text)
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DeskStep(Base):
    __tablename__ = "desk_steps"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    org_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]
    token_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
