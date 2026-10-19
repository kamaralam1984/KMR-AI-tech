"""Turns a provider's draft into a reply the client may show, and enforces the policy on it."""

import re
import uuid

from app.assistant.learning import _HINGLISH_MARKERS
from app.desktop.policy import step_refusal
from app.desktop.schemas import DraftReply, LookContext, Step
from app.extension.policy import find_violations
from app.scanning.sanitize import sanitize_text

MAX_STEPS = 5
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def detect_language(text: str) -> str:
    if _DEVANAGARI.search(text):
        return "hi"
    words = set(re.findall(r"[a-z]+", text.lower()))
    return "hinglish" if len(words & _HINGLISH_MARKERS) >= 2 else "en"


def resolve_language(requested: str, message: str) -> str:
    return detect_language(message) if requested == "auto" else requested


def to_steps(draft: DraftReply, look: LookContext | None) -> list[Step]:
    """Keeps only steps that point at a real element of the context, and downgrades any action the
    policy refuses to a plain highlight. Suggested text that looks personal is dropped."""
    if look is None:
        return []
    index = {(w.window_id, n.node_id): n for w in look.windows for n in w.nodes}
    steps: list[Step] = []
    seen: set[tuple[str, str, str]] = set()
    for d in draft.steps:
        node = index.get((d.window_id, d.node_id))
        if node is None:
            continue  # the model named an element that was never shown to it
        kind = d.kind
        why = sanitize_text(d.why, 200)
        refusal = step_refusal(kind, node.role, node.name, node.actionable)
        if refusal:
            kind = "highlight"
            why = f"{why} ({refusal})".strip()[:200]
        key = (d.window_id, d.node_id, kind)
        if key in seen:
            continue
        seen.add(key)
        suggested: str | None = None
        if kind == "type" and d.suggested_text:
            candidate = " ".join(d.suggested_text.split())[:120]
            # Anything the sanitizer would mask means the text looked personal: drop it entirely.
            if sanitize_text(candidate, 400) == candidate and not find_violations({"t": candidate}):
                suggested = candidate
        steps.append(
            Step(
                id=uuid.uuid4(),
                kind=kind,
                window_id=d.window_id,
                node_id=d.node_id,
                role=node.role,
                name=sanitize_text(node.name, 120),
                why=why,
                suggested_text=suggested,
            )
        )
        if len(steps) >= MAX_STEPS:
            break
    return steps
