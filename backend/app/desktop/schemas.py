"""Request and response models for the desktop assistant API. Every model forbids unknown fields."""

import re
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Language = Literal["auto", "en", "hinglish", "hi"]
ID = re.compile(r"^[A-Za-z0-9_.:-]{1,60}$")
Role = Literal[
    "button", "link", "menu_item", "tab", "text_field", "search_box", "text_area", "checkbox",
    "radio", "combo", "heading", "label", "image", "list_item", "window", "other",
]  # fmt: skip


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _id(value: str) -> str:
    if not ID.match(value):
        raise ValueError("an id has unsupported characters")
    return value


class Node(Strict):
    node_id: str
    role: Role
    name: Annotated[str, StringConstraints(max_length=120)] = ""
    text: Annotated[str, StringConstraints(max_length=200)] = ""
    states: list[Annotated[str, StringConstraints(max_length=20)]] = Field(
        default_factory=list, max_length=8
    )
    actionable: bool = False

    _check = field_validator("node_id")(_id)


class WindowCtx(Strict):
    window_id: str
    app: Annotated[str, StringConstraints(max_length=60)]
    title: Annotated[str, StringConstraints(max_length=160)] = ""
    nodes: list[Node] = Field(default_factory=list, max_length=300)

    _check = field_validator("window_id")(_id)


class LookContext(Strict):
    windows: list[WindowCtx] = Field(max_length=5)


class ClientInfo(Strict):
    version: Annotated[str, StringConstraints(max_length=20)]
    platform: Literal["linux", "windows"]
    redaction_version: Annotated[str, StringConstraints(max_length=20)]
    redacted_counts: dict[str, int] = Field(default_factory=dict, max_length=20)
    blocked_windows: int = Field(default=0, ge=0, le=500)


class Turn(Strict):
    role: Literal["user", "assistant"]
    text: Annotated[str, StringConstraints(max_length=1500)]


class ChatIn(Strict):
    message: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    language: Language = "auto"
    history: list[Turn] = Field(default_factory=list, max_length=10)
    look: LookContext | None = None
    client: ClientInfo


class Step(Strict):
    id: uuid.UUID
    kind: Literal["highlight", "press", "type"]
    window_id: str
    node_id: str
    role: str
    name: str
    why: Annotated[str, StringConstraints(max_length=200)]
    suggested_text: Annotated[str, StringConstraints(max_length=120)] | None = None


class ChatOut(Strict):
    reply: str
    language: Literal["en", "hinglish", "hi"]
    steps: list[Step]
    provider_label: str
    is_ai: bool
    notice: str


class MeOut(Strict):
    org_name: str
    user_name: str
    provider_label: str
    is_ai: bool
    daily_chat_limit: int
    daily_chat_used: int


class ReportIn(Strict):
    outcome: Literal["executed", "refused", "rejected", "expired", "failed"]


class DraftStep(Strict):
    """What a provider proposes. The server checks it against the context first."""

    kind: Literal["highlight", "press", "type"]
    window_id: str
    node_id: str
    why: str = Field(default="", max_length=300)
    suggested_text: str | None = Field(default=None, max_length=300)


class DraftReply(Strict):
    reply: str = Field(max_length=4000)
    steps: list[DraftStep] = Field(default_factory=list, max_length=8)


class Usage(Strict):
    input_tokens: int = 0
    output_tokens: int = 0


class TokenInfo(Strict):
    created_at: datetime | None = None
