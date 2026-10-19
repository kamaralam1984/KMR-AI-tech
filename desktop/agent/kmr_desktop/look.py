"""'Look now': read the chosen windows, redact, and build exactly what would be sent.

Nothing is read until the person presses the button and chooses windows. Sensitive windows are never
read. What the preview shows is the same dictionary that is sent, and the send step checks its hash.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, field

from kmr_desktop import VERSION, redact
from kmr_desktop.model import RawNode
from kmr_desktop.platforms.base import Platform
from kmr_desktop.policy import sensitive_window

MAX_WINDOWS = 5
MAX_NODES = 300
# The window's own title-bar buttons: noise for the assistant, and "Close" is not something to offer.
WINDOW_CONTROLS = {"minimize", "maximize", "restore", "close"}
KEEP_ROLES = {"button", "link", "menu_item", "tab", "text_field", "search_box", "text_area", "checkbox", "radio", "combo", "heading", "label", "list_item"}  # fmt: skip


@dataclass
class Prepared:
    prepared_id: str
    payload: dict  # type: ignore[type-arg]
    preview: str
    sha256: str
    windows: int
    nodes: int
    redacted_counts: dict[str, int]
    blocked: int
    # Local only, never sent: the raw elements, to re-check policy and act on them later.
    raw: dict[tuple[str, str], RawNode] = field(default_factory=dict)


def digest(payload: dict) -> str:  # type: ignore[type-arg]
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def prepare(
    platform: Platform,
    *,
    message: str,
    language: str,
    window_ids: list[str],
    history: list[dict[str, str]] | None = None,
    extra_apps: tuple[str, ...] = (),
) -> Prepared:
    counts: dict[str, int] = {}
    known = {w.window_id: w for w in platform.list_windows()}
    windows: list[dict] = []  # type: ignore[type-arg]
    raw: dict[tuple[str, str], RawNode] = {}
    blocked = 0
    for window_id in window_ids[:MAX_WINDOWS]:
        info = known.get(window_id)
        if info is None:
            continue
        if sensitive_window(info.app, info.title, extra_apps):
            blocked += 1  # never read: not even a snapshot is taken
            continue
        nodes: list[dict] = []  # type: ignore[type-arg]
        for node in platform.snapshot(window_id, MAX_NODES * 3):
            if node.role not in KEEP_ROLES or not (node.name or node.text or node.actionable):
                continue
            if node.role == "button" and node.name.strip().lower() in WINDOW_CONTROLS:
                continue
            nodes.append(
                {
                    "node_id": node.node_id,
                    "role": node.role,
                    "name": redact.redact_text(node.name, counts, 120),
                    "text": redact.redact_text(node.text, counts, 200),
                    "states": [s[:20] for s in node.states][:8],
                    "actionable": node.actionable,
                }
            )
            raw[(window_id, node.node_id)] = node
            if len(nodes) >= MAX_NODES:
                break
        windows.append(
            {
                "window_id": window_id,
                "app": redact.redact_text(info.app, counts, 60),
                "title": redact.redact_text(info.title, counts, 160),
                "nodes": nodes,
            }
        )
    payload = {
        "message": redact.redact_text(message, counts, 2000) or "(no message)",
        "language": language,
        "history": [
            {"role": t["role"], "text": redact.redact_text(t["text"], counts, 1500)}
            for t in (history or [])[-10:]
        ],
        "look": {"windows": windows} if windows else None,
        "client": {
            "version": VERSION,
            "platform": platform.name,
            "redaction_version": redact.VERSION,
            "redacted_counts": counts,
            "blocked_windows": blocked,
        },
    }
    return Prepared(
        prepared_id=uuid.uuid4().hex,
        payload=payload,
        preview=json.dumps(payload, indent=2, ensure_ascii=False),
        sha256=digest(payload),
        windows=len(windows),
        nodes=sum(len(w["nodes"]) for w in windows),
        redacted_counts=counts,
        blocked=blocked,
        raw=raw,
    )
