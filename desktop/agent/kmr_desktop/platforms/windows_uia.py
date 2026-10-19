"""Windows backend: UI Automation (through the `uiautomation` package).

NOT RUN ON WINDOWS. It was written against the same interface as the Linux backend and its pure
parts (role mapping) are tested, but no Windows machine was available. Treat it as untested until it
has passed tests/test_platform_contract.py on Windows.

Same rules as Linux: names and label text only, never typed values; act on one element with an
accessibility pattern (Invoke, Value), not by moving the mouse or sending keys.
"""

from typing import Any, Literal

from kmr_desktop.model import ActionResult, RawNode, WindowInfo

ROLE_MAP = {
    "ButtonControl": "button", "HyperlinkControl": "link", "MenuItemControl": "menu_item",
    "TabItemControl": "tab", "EditControl": "text_field", "CheckBoxControl": "checkbox",
    "RadioButtonControl": "radio", "ComboBoxControl": "combo", "TextControl": "label",
    "ListItemControl": "list_item", "ImageControl": "image", "WindowControl": "window",
}  # fmt: skip
MAX_DEPTH = 25


def map_role(control_type_name: str, multiline: bool = False) -> str:
    role = ROLE_MAP.get(control_type_name, "other")
    return "text_area" if role == "text_field" and multiline else role


class WindowsUia:
    name: Literal["linux", "windows"] = "windows"

    def __init__(self) -> None:
        import uiautomation as uia  # type: ignore[import-not-found]  # Windows only

        self._uia = uia
        self._windows: dict[str, tuple[Any, str, str]] = {}
        self._nodes: dict[tuple[str, str], tuple[Any, str, str]] = {}

    def list_windows(self) -> list[WindowInfo]:
        found: list[WindowInfo] = []
        self._windows = {}
        for i, win in enumerate(self._uia.GetRootControl().GetChildren()):
            title = (win.Name or "").strip()
            if not title:
                continue
            app = (win.ProcessName if hasattr(win, "ProcessName") else "") or "application"
            window_id = f"w{i}"
            self._windows[window_id] = (win, app, title)
            found.append(WindowInfo(window_id, app, title, False))
        return found

    def snapshot(self, window_id: str, max_nodes: int) -> list[RawNode]:
        held = self._windows.get(window_id)
        if held is None:
            return []
        out: list[RawNode] = []
        counter = 0
        stack: list[tuple[Any, int]] = [(held[0], 0)]
        while stack and len(out) < max_nodes:
            ctrl, depth = stack.pop()
            role = map_role(ctrl.ControlTypeName)
            name = (ctrl.Name or "")[:300]
            password = bool(getattr(ctrl, "IsPassword", False))
            if role != "other" and (name or role in ("text_field", "text_area")):
                node_id = f"{window_id}.n{counter}"
                counter += 1
                enabled = bool(ctrl.IsEnabled)
                text = name if role in ("label", "list_item") else ""
                actionable = enabled and role in (
                    "button",
                    "link",
                    "menu_item",
                    "tab",
                    "text_field",
                    "text_area",
                    "checkbox",
                    "radio",
                )
                states = (["password"] if password else []) + ([] if enabled else ["disabled"])
                rect = ctrl.BoundingRectangle
                bounds = (rect.left, rect.top, rect.width(), rect.height())
                out.append(RawNode(node_id, role, name, text, states, actionable, password, bounds))  # type: ignore[arg-type]
                self._nodes[(window_id, node_id)] = (ctrl, role, name)
            if depth < MAX_DEPTH:
                stack.extend((c, depth + 1) for c in reversed(ctrl.GetChildren()))
        return out

    def perform(
        self, window_id: str, node_id: str, kind: Literal["press", "type"], text: str | None
    ) -> ActionResult:
        held = self._nodes.get((window_id, node_id))
        if held is None:
            return ActionResult(False, "The element is not available. Look again.")
        ctrl, _role, name = held
        if (ctrl.Name or "")[:300] != name:
            return ActionResult(False, "The window changed since you looked. Look again.")
        if kind == "press":
            pattern = ctrl.GetInvokePattern()
            if pattern is None:
                return ActionResult(False, "This element cannot be pressed.")
            pattern.Invoke()
            return ActionResult(True, "Pressed.")
        pattern = ctrl.GetValuePattern()
        if pattern is None or text is None:
            return ActionResult(False, "This field cannot be typed into.")
        pattern.SetValue(text)
        return ActionResult(True, "Typed.")
