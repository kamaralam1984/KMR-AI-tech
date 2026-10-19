"""Request and response models for the extension API. Every model forbids unknown fields."""

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Short = Annotated[str, StringConstraints(max_length=300)]
SELECTOR = re.compile(r"^[A-Za-z0-9_#>:()\-. ]{1,200}$")
A11Y_CHECKS = (
    "images_missing_alt",
    "inputs_without_label",
    "buttons_without_name",
    "links_without_name",
    "missing_lang",
    "missing_title",
    "heading_structure",
    "missing_viewport_meta",
    "horizontal_overflow",
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TokenIn(Strict):
    name: str = Field(min_length=1, max_length=100)
    days: int = Field(default=30, ge=1, le=90)


class TokenOut(Strict):
    id: uuid.UUID
    name: str
    token_prefix: str
    user_id: uuid.UUID
    expires_at: datetime
    revoked: bool
    last_used_at: datetime | None
    created_at: datetime


class TokenCreatedOut(TokenOut):
    token: str  # shown once; only its hash is stored


class ExtProjectOut(Strict):
    id: uuid.UUID
    name: str
    site_host: str
    site_url: str


class ObservationIn(Strict):
    kind: Literal[
        "page", "console_error", "page_error", "failed_request", "http_error", "a11y_issue"
    ]
    page_url: Annotated[str, StringConstraints(max_length=500)]
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("data")
    @classmethod
    def small(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 12:
            raise ValueError("too many fields")
        for key, item in value.items():
            if len(key) > 30:
                raise ValueError("a field name is too long")
            if isinstance(item, str) and len(item) > 600:
                raise ValueError("a value is too long")
            if isinstance(item, list) and len(item) > 12:
                raise ValueError("a list is too long")
        return value


class ClientInfo(Strict):
    version: Annotated[str, StringConstraints(max_length=20)]
    sensitive_page: bool = False
    redaction_version: Annotated[str, StringConstraints(max_length=20)]
    redacted_counts: dict[str, int] = Field(default_factory=dict, max_length=20)
    masked_regions: int = Field(default=0, ge=0, le=1000)


class EvidenceIn(Strict):
    project_id: uuid.UUID
    page_url: Annotated[str, StringConstraints(max_length=500)]
    client: ClientInfo
    observations: list[ObservationIn] = Field(min_length=1, max_length=150)
    screenshot_png_b64: Annotated[str, StringConstraints(max_length=1_400_000)] | None = None


class EvidenceOut(Strict):
    scan_id: uuid.UUID
    observations_stored: int
    screenshot_stored: bool
    notice: str


class FindingOut(Strict):
    issue_id: uuid.UUID
    title: str
    severity: str
    category: str
    confidence: str
    classification: str
    summary: str
    observed: list[str]
    inferred: list[str]
    unverified: list[str]
    selectors: list[str]
    evidence_ids: list[str]
    proposed_fix: str
    is_ai: bool
    web_path: str


class AnalyzeOut(Strict):
    scan_id: uuid.UUID
    provider_label: str
    is_ai: bool
    findings: list[FindingOut]
    notice: str
