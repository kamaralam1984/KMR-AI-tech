"""Request and response models for the owner and visitor assistant APIs."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.assistant.forms import CONFIRM_ACTIONS, FormDefinition
from app.assistant.learning import AGE_BANDS
from app.assistant.service import valid_origin

Module = Literal["guide", "forms", "learning"]
ShortName = Field(min_length=1, max_length=100)


def _origins(values: list[str]) -> list[str]:
    cleaned = []
    for value in values:
        value = value.strip().rstrip("/").lower()
        if not valid_origin(value):
            raise ValueError(
                f"'{value}' is not a valid origin. Use https://example.com (no path, no wildcard)."
            )
        if value not in cleaned:
            cleaned.append(value)
    return cleaned


class InstallIn(BaseModel):
    name: str = ShortName
    project_id: uuid.UUID | None = None
    allowed_origins: list[str] = Field(default_factory=list, max_length=10)
    modules: list[Module] = Field(default_factory=lambda: ["guide"], max_length=3)  # type: ignore[arg-type]
    consent_text: str = Field(default="", max_length=1000)
    handoff_retention_days: int = Field(default=30, ge=1, le=365)
    acknowledge_child_safeguards: bool = False
    learning_consent_required: bool = True

    @field_validator("allowed_origins")
    @classmethod
    def origins(cls, value: list[str]) -> list[str]:
        return _origins(value)


class InstallPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    allowed_origins: list[str] | None = Field(default=None, max_length=10)
    modules: list[Module] | None = Field(default=None, max_length=3)
    enabled: bool | None = None
    consent_text: str | None = Field(default=None, max_length=1000)
    handoff_retention_days: int | None = Field(default=None, ge=1, le=365)
    acknowledge_child_safeguards: bool | None = None
    learning_consent_required: bool | None = None

    @field_validator("allowed_origins")
    @classmethod
    def origins(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _origins(value)


class InstallOut(BaseModel):
    id: uuid.UUID
    name: str
    project_id: uuid.UUID | None
    key_prefix: str
    allowed_origins: list[str]
    modules: list[str]
    enabled: bool
    revoked: bool
    consent_text: str
    handoff_retention_days: int
    child_safeguards_acknowledged: bool
    learning_consent_required: bool
    created_at: datetime
    snippet: str


class InstallCreatedOut(InstallOut):
    install_key: str  # shown once; only its hash is stored


class PageIn(BaseModel):
    kind: Literal["site", "learning"] = "site"
    title: str = Field(min_length=1, max_length=200)
    url: str | None = Field(default=None, max_length=500)
    content: str = Field(min_length=20, max_length=200_000)


class PageOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    url: str | None
    source: str
    chunk_count: int
    blocked_chunks: int
    updated_at: datetime


class IndexIn(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=10)


class IndexResultOut(BaseModel):
    path: str
    status: str
    detail: str | None = None
    page_id: uuid.UUID | None = None


class FormIn(BaseModel):
    name: str = ShortName
    definition: FormDefinition
    enabled: bool = True


class FormOut(BaseModel):
    id: uuid.UUID
    name: str
    category: str
    enabled: bool
    definition: dict[str, Any]
    updated_at: datetime


class HandoffOut(BaseModel):
    id: uuid.UUID
    install_id: uuid.UUID
    summary: str
    contact: str | None
    status: str
    consent_at: datetime
    expires_at: datetime
    created_at: datetime


class HandoffPatch(BaseModel):
    status: Literal["new", "resolved"]


# --------------------------------------------------------------------------- visitor side


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=300)


class PassageOut(BaseModel):
    text: str
    title: str
    source_url: str | None
    match: float


class GuideOut(BaseModel):
    found: bool
    answer: str | None
    passages: list[PassageOut]
    message: str
    ai_used: bool = False


class FormValues(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict, max_length=80)

    @field_validator("values")
    @classmethod
    def small(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key, item in value.items():
            if len(key) > 40:
                raise ValueError("a field key is too long")
            if isinstance(item, str) and len(item) > 1000:
                raise ValueError("a value is too long")
            if isinstance(item, dict) and len(item) > 8:
                raise ValueError("file details are too large")
            if not isinstance(item, str | int | float | bool | dict | type(None)):
                raise ValueError("unsupported value type")
        return value


class ConfirmIn(FormValues):
    action: Literal["declaration", "submission", "payment"]
    field_key: str | None = Field(default=None, max_length=40)
    phrase: str = Field(max_length=100)


class ConfirmVerifyIn(BaseModel):
    form_id: uuid.UUID
    action: Literal["declaration", "submission", "payment"]
    values_hash: str = Field(min_length=64, max_length=64)
    token: str = Field(min_length=10, max_length=2000)
    field_key: str | None = Field(default=None, max_length=40)


class HandoffPreviewIn(BaseModel):
    summary: str = Field(min_length=1, max_length=2000)
    contact: str | None = Field(default=None, max_length=200)


class HandoffSubmitIn(BaseModel):
    text: str = Field(min_length=1, max_length=2100)
    contact: str | None = Field(default=None, max_length=200)
    token: str = Field(min_length=10, max_length=200)
    consent: bool


class ConsentIn(BaseModel):
    # The installer's own opaque reference (a class code, enrolment number...). Not a name.
    label: str = Field(min_length=1, max_length=60)
    age_band: Literal["6-8", "9-11", "12-14", "15-17"]
    # How the installer collected the consent, in its own words (for example "signed form on file").
    consent_basis: str = Field(min_length=5, max_length=300)
    days: int = Field(default=180, ge=1, le=366)


class ConsentOut(BaseModel):
    id: uuid.UUID
    label: str
    age_band: str
    consent_basis: str
    expires_at: datetime
    revoked: bool
    created_at: datetime


class ConsentCreatedOut(ConsentOut):
    token: str  # shown once


class LearningIn(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    age_band: Literal["6-8", "9-11", "12-14", "15-17", "18+"]
    guardian_confirmed: bool = False
    language: Literal["auto", "en", "hi", "hinglish"] = "auto"


__all__ = [n for n in dir() if not n.startswith("_")] + ["AGE_BANDS", "CONFIRM_ACTIONS"]
