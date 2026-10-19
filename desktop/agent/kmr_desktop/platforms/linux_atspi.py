"""Linux backend: the AT-SPI accessibility bus (works on Wayland and X11).

It reads window and element NAMES and the text of labels, never what is typed into a field, and it
acts through accessibility actions on one element (press, set text), not by moving a pointer or
sending keys. Before acting it checks that the element is still the same one it listed.
"""

import time
from typing import Any, Literal

from kmr_desktop.model import ActionResult, RawNode, WindowInfo

SKIP_APPS = {
    "gnome-shell", "gjs", "xdg-desktop-portal-gtk", "xdg-desktop-portal-gnome", "ibus-extension-gtk3",
    "update-notifier", "evolution-alarm-notify", "gsd-media-keys", "gnome-shell-calendar-server",
}  # fmt: skip
PRESS_NAMES = ("click", "press", "activate", "open", "jump", "toggle")
TRAVERSAL_SECONDS = 4.0
MAX_VISITED = 2500
MAX_DEPTH = 30


class LinuxAtspi:
    name: Literal["linux", "windows"] = "linux"

    def __init__(self) -> None:
        import gi  # system PyGObject; not available everywhere, so imported here

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi

        self._a = Atspi
        Atspi.init()
        self._windows: dict[str, tuple[Any, str, str]] = {}  # window_id -> (accessible, app, title)
        self._nodes: dict[tuple[str, str], tuple[Any, str, str]] = {}  # -> (accessible, role, name)
        self._role_map = self._build_role_map()

    # ------------------------------------------------------------------------ helpers

    def _build_role_map(self) -> dict[Any, str]:
        r = self._a.Role
        pairs = {
            "PUSH_BUTTON": "button", "TOGGLE_BUTTON": "button", "MENU_ITEM": "menu_item",
            "CHECK_MENU_ITEM": "menu_item", "RADIO_MENU_ITEM": "menu_item", "LINK": "link",
            "PAGE_TAB": "tab", "ENTRY": "text_field", "PASSWORD_TEXT": "text_field",
            "SPIN_BUTTON": "text_field", "CHECK_BOX": "checkbox", "RADIO_BUTTON": "radio",
            "COMBO_BOX": "combo", "HEADING": "heading", "LABEL": "label", "STATIC": "label",
            "PARAGRAPH": "label", "LIST_ITEM": "list_item", "IMAGE": "image", "ICON": "image",
            "FRAME": "window", "WINDOW": "window", "DIALOG": "window", "TEXT": "text_field",
            "TERMINAL": "other", "DOCUMENT_WEB": "other",
        }  # fmt: skip
        return {getattr(r, k): v for k, v in pairs.items() if hasattr(r, k)}

    def _has(self, acc: Any, state: str) -> bool:
        try:
            return bool(acc.get_state_set().contains(getattr(self._a.StateType, state)))
        except Exception:  # noqa: BLE001 - a broken element must not stop the scan
            return False

    def _safe(self, fn: Any, default: Any = None) -> Any:
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return default

    # ----------------------------------------------------------------------- windows

    def list_windows(self) -> list[WindowInfo]:
        desktop = self._a.get_desktop(0)
        found: list[WindowInfo] = []
        self._windows = {}
        for ai in range(self._safe(desktop.get_child_count, 0)):
            app = self._safe(lambda ai=ai: desktop.get_child_at_index(ai))
            app_name = self._safe(lambda app=app: app.get_name(), "") or ""
            if app is None or not app_name or app_name in SKIP_APPS:
                continue
            for wi in range(self._safe(app.get_child_count, 0)):
                win = self._safe(lambda app=app, wi=wi: app.get_child_at_index(wi))
                if win is None:
                    continue
                title = self._safe(win.get_name, "") or ""
                if not title:
                    continue
                window_id = f"a{ai}w{wi}"
                self._windows[window_id] = (win, app_name, title)
                found.append(WindowInfo(window_id, app_name, title, self._has(win, "ACTIVE")))
        return found

    # --------------------------------------------------------------------- snapshot

    def snapshot(self, window_id: str, max_nodes: int) -> list[RawNode]:
        held = self._windows.get(window_id)
        if held is None:
            return []
        window = held[0]
        out: list[RawNode] = []
        for key in [k for k in self._nodes if k[0] == window_id]:
            del self._nodes[key]
        deadline = time.monotonic() + TRAVERSAL_SECONDS
        visited = 0
        stack: list[tuple[Any, int]] = [(window, 0)]
        counter = 0
        while stack and len(out) < max_nodes and visited < MAX_VISITED and time.monotonic() < deadline:
            acc, depth = stack.pop()
            visited += 1
            node = self._describe(acc, f"{window_id}.n{counter}")
            if node is not None:
                counter += 1
                out.append(node)
                self._nodes[(window_id, node.node_id)] = (acc, node.role, node.name)
            if depth < MAX_DEPTH:
                count = self._safe(acc.get_child_count, 0)
                children = [
                    self._safe(lambda acc=acc, i=i: acc.get_child_at_index(i)) for i in range(min(count, 200))
                ]
                stack.extend((c, depth + 1) for c in reversed(children) if c is not None)
        return out

    def _describe(self, acc: Any, node_id: str) -> RawNode | None:
        role_enum = self._safe(acc.get_role)
        role = self._role_map.get(role_enum, "other")
        if role in ("other", "window", "image") and not self._safe(acc.get_name):
            return None
        name = (self._safe(acc.get_name, "") or "")[:300]
        password = role_enum == getattr(self._a.Role, "PASSWORD_TEXT", None)
        editable = self._has(acc, "EDITABLE")
        if role == "text_field" and self._has(acc, "MULTI_LINE"):
            role = "text_area"
        if role == "text_field" and not editable and not password:
            role = "label"  # read-only text is just text
        text = ""
        if role in ("label", "heading", "list_item") and not editable:
            text = name
        if role in ("text_field", "text_area"):
            name = name or (self._safe(acc.get_description, "") or "")[:120]
            text = ""  # never the value
        if password and not name:
            name = "Password"
        enabled = self._has(acc, "ENABLED") or self._has(acc, "SENSITIVE")
        states = [
            s
            for s, label in (("FOCUSED", "focused"), ("CHECKED", "checked"), ("REQUIRED", "required"))
            if self._has(acc, s)
            for s in [label]
        ]
        if password:
            states.append("password")
        if not enabled:
            states.append("disabled")
        actionable = enabled and (
            (role in ("text_field", "text_area") and editable)
            or (
                role not in ("text_field", "text_area", "label", "heading", "image")
                and self._n_actions(acc) > 0
            )
        )
        bounds = None
        comp = self._safe(acc.get_component_iface)
        if comp is not None:
            rect = self._safe(lambda: comp.get_extents(self._a.CoordType.SCREEN))
            if rect is not None:
                bounds = (int(rect.x), int(rect.y), int(rect.width), int(rect.height))
        return RawNode(node_id, role, name, text, states, actionable, password, bounds)  # type: ignore[arg-type]

    def _n_actions(self, acc: Any) -> int:
        action = self._safe(acc.get_action_iface)
        return int(self._safe(lambda: action.get_n_actions(), 0) or 0) if action is not None else 0

    # ------------------------------------------------------------------------ actions

    def perform(
        self, window_id: str, node_id: str, kind: Literal["press", "type"], text: str | None
    ) -> ActionResult:
        held = self._nodes.get((window_id, node_id))
        window = self._windows.get(window_id)
        if held is None or window is None:
            return ActionResult(False, "The element is not available. Look again.")
        acc, role, name = held
        # The element must still be the one that was listed (same role and name).
        if (self._role_map.get(self._safe(acc.get_role)) is None) or (self._safe(acc.get_name, "") or "")[
            :300
        ] != name:
            return ActionResult(False, "The window changed since you looked. Look again.")
        if self._safe(window[0].get_name, "") != window[2]:
            return ActionResult(False, "The window changed since you looked. Look again.")
        if kind == "press":
            action = self._safe(acc.get_action_iface)
            if action is None:
                return ActionResult(False, "This element cannot be pressed.")
            count = int(self._safe(action.get_n_actions, 0) or 0)
            names = [
                (i, (self._safe(lambda i=i: action.get_action_name(i), "") or "").lower())
                for i in range(count)
            ]
            pick = next(
                (i for want in PRESS_NAMES for i, n in names if n == want),
                names[0][0] if len(names) == 1 else None,
            )
            if pick is None:
                return ActionResult(False, "This element has no simple press action.")
            ok = bool(self._safe(lambda: action.do_action(pick), False))
            return ActionResult(ok, "Pressed." if ok else "The app did not accept the press.")
        editable = self._safe(acc.get_editable_text_iface)
        if editable is None or text is None:
            return ActionResult(False, "This field cannot be typed into.")
        ok = bool(self._safe(lambda: editable.set_text_contents(text), False))
        return ActionResult(ok, "Typed." if ok else "The app did not accept the text.")
