"""Plain data the agent passes around. Nothing here is persisted."""

from dataclasses import dataclass, field
from typing import Literal

Role = Literal[
    "button", "link", "menu_item", "tab", "text_field", "search_box", "text_area", "checkbox",
    "radio", "combo", "heading", "label", "image", "list_item", "window", "other",
]  # fmt: skip


@dataclass(frozen=True)
class WindowInfo:
    window_id: str
    app: str
    title: str
    active: bool = False


@dataclass
class RawNode:
    """One element as the operating system reports it. `name` and `text` are NOT yet redacted.
    The value typed into a field is never read, only its name."""

    node_id: str
    role: Role
    name: str = ""
    text: str = ""
    states: list[str] = field(default_factory=list)
    actionable: bool = False
    password: bool = False
    bounds: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    detail: str
