"""Windows backend: UI Automation, through the `uiautomation` package (v2.0.x).

It reads window and element NAMES and the text of labels, never the value of a field, and it acts
through UI Automation patterns on ONE element (Invoke, SelectionItem, Value.SetValue), not by moving
the mouse or sending keys. Before acting it checks that the element is still the one it listed.

Threading: the library says a Control or Pattern cannot be used in a thread other than the one that
created it, and the agent's local API serves each request on its own thread. So every call here runs
on ONE dedicated UI Automation thread, which is initialised once and owns all controls.

Verification status: written against the library's source and exercised on Linux with a faithful fake
of the library (tests/helpers/fake_uia.py). A live test (tests/test_platform_windows_live.py) drives a
real Windows Forms window and runs on a Windows machine (see .github/workflows/desktop.yml). It has not
been run on Windows by the author.
"""

import queue
import threading
import time
from collections.abc import Callable
from typing import Any, Literal, TypeVar

from kmr_desktop.model import ActionResult, RawNode, WindowInfo

T = TypeVar("T")
ROLE_MAP = {
    "ButtonControl": "button", "SplitButtonControl": "button", "HyperlinkControl": "link",
    "MenuItemControl": "menu_item", "TabItemControl": "tab", "EditControl": "text_field",
    "CheckBoxControl": "checkbox", "RadioButtonControl": "radio", "ComboBoxControl": "combo",
    "TextControl": "label", "ListItemControl": "list_item", "ImageControl": "image",
    "WindowControl": "window",
}  # fmt: skip
# Windows that are the desktop shell, not applications the person is working in.
SKIP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow"}
SKIP_TITLES = {"Program Manager", "Task Switching", "Windows Input Experience"}
MAX_DEPTH = 25
MAX_VISITED = 2500
TRAVERSAL_SECONDS = 4.0
MAX_CHILDREN = 200


def map_role(control_type_name: str, multiline: bool = False) -> str:
    role = ROLE_MAP.get(control_type_name, "other")
    return "text_area" if role == "text_field" and multiline else role


def default_process_name(pid: int) -> str:
    """The program's file name without .exe, using only the Windows API (no extra package)."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(520)
        buffer = ctypes.create_unicode_buffer(520)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return (
                buffer.value.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".exe").removesuffix(".EXE")
            )
        return ""
    finally:
        kernel32.CloseHandle(handle)


class _UiaThread:
    """Runs every call on one thread that initialised UI Automation, and returns the result."""

    def __init__(self, uia: Any) -> None:
        self._uia = uia
        self._jobs: queue.Queue[tuple[Callable[[], Any], queue.Queue[Any]] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="kmr-uia", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self._uia.InitializeUIAutomationInCurrentThread()
        try:
            while True:
                job = self._jobs.get()
                if job is None:
                    return
                fn, box = job
                try:
                    box.put((True, fn()))
                except BaseException as exc:  # noqa: BLE001 - handed back to the caller
                    box.put((False, exc))
        finally:
            self._uia.UninitializeUIAutomationInCurrentThread()

    def call(self, fn: Callable[[], T]) -> T:
        if threading.current_thread() is self._thread:
            return fn()
        box: queue.Queue[Any] = queue.Queue(maxsize=1)
        self._jobs.put((fn, box))
        ok, value = box.get()
        if not ok:
            raise value
        return value  # type: ignore[no-any-return]

    def stop(self) -> None:
        self._jobs.put(None)


class WindowsUia:
    name: Literal["linux", "windows"] = "windows"

    def __init__(self, uia: Any = None, process_name: Callable[[int], str] | None = None) -> None:
        if uia is None:
            import uiautomation as uia  # type: ignore[import-not-found,no-redef]  # Windows only
        self._uia = uia
        self._process_name = process_name or default_process_name
        self._thread = _UiaThread(uia)
        # Only touched on the UIA thread:
        self._windows: dict[str, tuple[Any, str, str]] = {}  # window_id -> (control, app, title)
        self._nodes: dict[tuple[str, str], tuple[Any, str, str]] = {}  # -> (control, control type, name)

    def close(self) -> None:
        self._thread.stop()

    # ----------------------------------------------------------------------- windows

    def list_windows(self) -> list[WindowInfo]:
        return self._thread.call(self._list_windows)

    def _list_windows(self) -> list[WindowInfo]:
        uia = self._uia
        found: list[WindowInfo] = []
        self._windows = {}
        try:
            foreground = uia.GetForegroundControl().NativeWindowHandle
        except Exception:  # noqa: BLE001
            foreground = None
        for i, win in enumerate(uia.GetRootControl().GetChildren()):
            try:
                if win.ControlTypeName != "WindowControl" or win.IsOffscreen:
                    continue
                title = (win.Name or "").strip()
                if not title or title in SKIP_TITLES or win.ClassName in SKIP_CLASSES:
                    continue
                app = self._process_name(win.ProcessId) or "application"
                window_id = f"w{i}"
                self._windows[window_id] = (win, app, title)
                found.append(WindowInfo(window_id, app, title, foreground == win.NativeWindowHandle))
            except Exception:  # noqa: BLE001, S112 - one broken window must not hide the rest
                continue
        return found

    # --------------------------------------------------------------------- snapshot

    def snapshot(self, window_id: str, max_nodes: int) -> list[RawNode]:
        return self._thread.call(lambda: self._snapshot(window_id, max_nodes))

    def _snapshot(self, window_id: str, max_nodes: int) -> list[RawNode]:
        held = self._windows.get(window_id)
        if held is None:
            return []
        for key in [k for k in self._nodes if k[0] == window_id]:
            del self._nodes[key]
        out: list[RawNode] = []
        deadline = time.monotonic() + TRAVERSAL_SECONDS
        visited = 0
        counter = 0
        stack: list[tuple[Any, int]] = [(held[0], 0)]
        while stack and len(out) < max_nodes and visited < MAX_VISITED and time.monotonic() < deadline:
            ctrl, depth = stack.pop()
            visited += 1
            node = self._describe(ctrl, f"{window_id}.n{counter}")
            if node is not None:
                counter += 1
                out.append(node)
                self._nodes[(window_id, node.node_id)] = (ctrl, ctrl.ControlTypeName, node.name)
            if depth < MAX_DEPTH:
                try:
                    children = ctrl.GetChildren()[:MAX_CHILDREN]
                except Exception:  # noqa: BLE001
                    children = []
                stack.extend((c, depth + 1) for c in reversed(children))
        return out

    def _describe(self, ctrl: Any, node_id: str) -> RawNode | None:
        try:
            if ctrl.IsOffscreen:
                return None
            type_name = ctrl.ControlTypeName
            role = map_role(type_name, multiline="RICHEDIT" in (ctrl.ClassName or "").upper())
            name = (ctrl.Name or "")[:300]
            if role in ("other", "window", "image") and not name:
                return None
            enabled = bool(ctrl.IsEnabled)
            password = False
            actionable = False
            text = ""
            if role in ("text_field", "text_area"):
                password = bool(ctrl.IsPassword)
                # Only whether it is read-only is asked. The value itself is never read.
                pattern = ctrl.GetValuePattern()
                writable = pattern is not None and not pattern.IsReadOnly
                actionable = enabled and (writable or password)  # policy refuses password fields
                if password and not name:
                    name = "Password"
            elif role in ("label", "list_item"):
                text = name
            elif role in ("button", "link", "menu_item", "tab", "checkbox", "radio", "combo"):
                actionable = enabled
            states = (["password"] if password else []) + ([] if enabled else ["disabled"])
            rect = ctrl.BoundingRectangle
            bounds = (int(rect.left), int(rect.top), int(rect.width()), int(rect.height()))
            return RawNode(node_id, role, name, text, states, actionable, password, bounds)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - a broken element must not stop the scan
            return None

    # ----------------------------------------------------------------------- actions

    def perform(
        self, window_id: str, node_id: str, kind: Literal["press", "type"], text: str | None
    ) -> ActionResult:
        return self._thread.call(lambda: self._perform(window_id, node_id, kind, text))

    def _perform(
        self, window_id: str, node_id: str, kind: Literal["press", "type"], text: str | None
    ) -> ActionResult:
        held = self._nodes.get((window_id, node_id))
        window = self._windows.get(window_id)
        if held is None or window is None:
            return ActionResult(False, "The element is not available. Look again.")
        ctrl, type_name, name = held
        try:
            # It must still be the element that was listed: same type and name, in the same window.
            if ctrl.ControlTypeName != type_name or (ctrl.Name or "")[:300] != name:
                return ActionResult(False, "The window changed since you looked. Look again.")
            if (window[0].Name or "").strip() != window[2]:
                return ActionResult(False, "The window changed since you looked. Look again.")
            if not ctrl.IsEnabled:
                return ActionResult(False, "This element is disabled.")
            if kind == "press":
                invoke = ctrl.GetInvokePattern()
                if invoke is not None:
                    ok = bool(invoke.Invoke())
                    return ActionResult(ok, "Pressed." if ok else "The app did not accept the press.")
                select = ctrl.GetSelectionItemPattern()
                if select is not None:
                    ok = bool(select.Select())
                    return ActionResult(ok, "Pressed." if ok else "The app did not accept the press.")
                return ActionResult(False, "This element has no simple press action.")
            if ctrl.IsPassword:
                return ActionResult(False, "This field cannot be typed into.")  # belt and braces
            value = ctrl.GetValuePattern()
            if value is None or text is None or value.IsReadOnly:
                return ActionResult(False, "This field cannot be typed into.")
            ok = bool(value.SetValue(text))
            return ActionResult(ok, "Typed." if ok else "The app did not accept the text.")
        except Exception:  # noqa: BLE001 - the app went away or refused: say so, change nothing more
            return ActionResult(False, "The app did not respond. Look again.")
