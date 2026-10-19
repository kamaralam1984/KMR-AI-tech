"""The agent's brain: prepare a look, send it, hold the proposed steps, and perform ONE approved step.

Every rule that matters is checked here again, against the LOCAL raw element, whatever the server
said. An approval is one step, one element, one text, 2 minutes, once.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kmr_desktop import redact
from kmr_desktop.client import DeskClient, DeskError
from kmr_desktop.look import Prepared, digest, prepare
from kmr_desktop.platforms.base import Platform
from kmr_desktop.policy import SECRET_FIELD, sensitive_window, step_refusal

logger = logging.getLogger(__name__)
STEP_TTL = 120.0
PREPARED_TTL = 600.0
MAX_TYPED = 120


class AgentError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class StepState:
    step: dict[str, Any]
    prepared_id: str
    expires_at: float
    state: str = "offered"  # offered -> executed | failed | refused | rejected | expired


class Agent:
    def __init__(
        self,
        platform: Platform,
        client: DeskClient | None,
        *,
        extra_apps: tuple[str, ...] = (),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.platform = platform
        self.client = client
        self.extra_apps = extra_apps
        self.clock = clock
        self._prepared: dict[str, tuple[Prepared, float]] = {}
        self._steps: dict[str, StepState] = {}
        self._history: list[dict[str, str]] = []

    # ------------------------------------------------------------------------- windows

    def windows(self) -> dict[str, Any]:
        """Windows the person may choose from. Sensitive ones are counted, never listed."""
        shown, hidden = [], 0
        for w in self.platform.list_windows():
            if sensitive_window(w.app, w.title, self.extra_apps):
                hidden += 1
            else:
                shown.append({"window_id": w.window_id, "app": w.app, "title": w.title, "active": w.active})
        return {"windows": shown, "hidden": hidden}

    # ------------------------------------------------------------------- prepare and send

    def prepare(self, message: str, language: str, window_ids: list[str]) -> Prepared:
        self._forget_old()
        prepared = prepare(
            self.platform,
            message=message,
            language=language,
            window_ids=window_ids,
            history=self._history,
            extra_apps=self.extra_apps,
        )
        self._prepared[prepared.prepared_id] = (prepared, self.clock() + PREPARED_TTL)
        return prepared

    def send(self, prepared_id: str, sha256: str) -> dict[str, Any]:
        """Sends exactly what was previewed. Refuses if the previewed content is not what is held."""
        held = self._prepared.get(prepared_id)
        if held is None:
            raise AgentError("That preview has expired. Prepare it again.", 410)
        prepared, _ = held
        if sha256 != prepared.sha256 or digest(prepared.payload) != prepared.sha256:
            raise AgentError("What you saw is not what would be sent. Prepare it again.", 409)
        if self.client is None:
            raise AgentError("Connect this app to KMR AI first (server address and token).", 409)
        try:
            reply = self.client.chat(prepared.payload)
        except DeskError as exc:
            raise AgentError(exc.message, exc.status or 502) from exc
        now = self.clock()
        for step in reply.get("steps", []):
            self._steps[step["id"]] = StepState(step, prepared_id, now + STEP_TTL)
        self._history = [
            *self._history[-8:],
            {"role": "user", "text": prepared.payload["message"]},
            {"role": "assistant", "text": reply.get("reply", "")[:1500]},
        ]
        del self._prepared[prepared_id]
        # Keep the raw elements for the steps just offered, nothing else.
        self._prepared[prepared_id] = (prepared, now + STEP_TTL)
        return reply  # type: ignore[no-any-return]

    # ------------------------------------------------------------------------ steps

    def _step(self, step_id: str) -> StepState:
        st = self._steps.get(step_id)
        if st is None:
            raise AgentError("That step is not known (it may have expired).", 404)
        if st.state != "offered":
            raise AgentError(f"That step is already {st.state}.", 409)
        if self.clock() > st.expires_at:
            self._finish(step_id, st, "expired")
            raise AgentError("That step expired. Ask again, or look again.", 410)
        return st

    def _finish(self, step_id: str, st: StepState, outcome: str) -> None:
        st.state = outcome
        if self.client is not None:
            try:
                self.client.report(step_id, outcome)
            except DeskError:
                logger.warning("could not report step outcome")  # the audit trail is best effort

    def highlight(self, step_id: str) -> dict[str, Any]:
        """Where to draw the highlight for a step. Needs no permission: it changes nothing."""
        st = self._steps.get(step_id)
        if st is None:
            raise AgentError("That step is not known.", 404)
        node = self._raw(st)
        return {"bounds": node.bounds if node else None, "name": st.step["name"], "role": st.step["role"]}

    def _raw(self, st: StepState):  # type: ignore[no-untyped-def]
        held = self._prepared.get(st.prepared_id)
        if held is None:
            return None
        return held[0].raw.get((st.step["window_id"], st.step["node_id"]))

    def reject(self, step_id: str) -> dict[str, str]:
        st = self._step(step_id)
        self._finish(step_id, st, "rejected")
        return {"state": "rejected"}

    def approve(self, step_id: str, text: str | None = None) -> dict[str, Any]:
        """The person allowed this one step. Re-checks everything, does it once, reports the outcome."""
        st = self._step(step_id)
        step = st.step
        if step["kind"] == "highlight":
            raise AgentError("A highlight needs no permission.", 400)
        node = self._raw(st)
        if node is None:
            self._finish(step_id, st, "refused")
            raise AgentError("The window changed since you looked. Look again.", 409)
        window = next((w for w in self.platform.list_windows() if w.window_id == step["window_id"]), None)
        if window is None or sensitive_window(window.app, window.title, self.extra_apps):
            self._finish(step_id, st, "refused")
            raise AgentError("That window is not available any more.", 409)
        refusal = step_refusal(step["kind"], node.role, node.name, node.actionable, node.password)
        if refusal:
            self._finish(step_id, st, "refused")
            raise AgentError(f"Not done: {refusal}.", 403)
        typed: str | None = None
        if step["kind"] == "type":
            typed = " ".join((text or "").split())
            problem = self._text_problem(typed)
            if problem:
                raise AgentError(problem, 422)  # the step stays offered: the person can fix the text
        result = self.platform.perform(step["window_id"], step["node_id"], step["kind"], typed)
        self._finish(step_id, st, "executed" if result.ok else "failed")
        if not result.ok:
            raise AgentError(result.detail, 409)
        return {"state": "executed", "detail": result.detail}

    @staticmethod
    def _text_problem(text: str) -> str | None:
        if not text:
            return "Write the text to type."
        if len(text) > MAX_TYPED:
            return f"Keep it to {MAX_TYPED} characters."
        if SECRET_FIELD.search(text) or redact.redact_text(text, None, 400) != text:
            return "That looks like personal data or a secret. The agent does not type those."
        return None

    # -------------------------------------------------------------------------- upkeep

    def _forget_old(self) -> None:
        now = self.clock()
        self._prepared = {k: v for k, v in self._prepared.items() if v[1] > now}
        for sid, st in list(self._steps.items()):
            if st.state == "offered" and now > st.expires_at:
                self._finish(sid, st, "expired")
