"""A fake of the `uiautomation` package, faithful to the parts of its API the Windows backend uses.

It enforces the rule that matters most and that the real library documents: a Control or Pattern can
only be used on the thread that initialised UI Automation. Any other thread raises, exactly where the
real library would misbehave. It also fails the test if anything reads a field's value.
"""

import itertools
import threading
from typing import Any

_ids = itertools.count(100)


class Rect:
    def __init__(self, left: int = 0, top: int = 0, right: int = 0, bottom: int = 0) -> None:
        self.left, self.top, self.right, self.bottom = left, top, right, bottom

    def width(self) -> int:
        return self.right - self.left

    def height(self) -> int:
        return self.bottom - self.top


class InvokePattern:
    def __init__(self, owner: "Control") -> None:
        self.owner = owner

    def Invoke(self, waitTime: float = 0.5) -> bool:  # noqa: N802, N803
        self.owner.world.check_thread()
        self.owner.world.log.append(("invoke", self.owner.Name))
        return self.owner.accepts


class SelectionItemPattern:
    def __init__(self, owner: "Control") -> None:
        self.owner = owner

    def Select(self, waitTime: float = 0.5) -> bool:  # noqa: N802, N803
        self.owner.world.check_thread()
        self.owner.world.log.append(("select", self.owner.Name))
        return self.owner.accepts


class ValuePattern:
    def __init__(self, owner: "Control") -> None:
        self.owner = owner

    @property
    def IsReadOnly(self) -> bool:  # noqa: N802
        self.owner.world.check_thread()
        return self.owner.read_only

    @property
    def Value(self) -> str:  # noqa: N802
        raise AssertionError("the agent must never read a field's value")

    def SetValue(self, value: str, waitTime: float = 0.5) -> bool:  # noqa: N802, N803
        self.owner.world.check_thread()
        self.owner.world.log.append(("set_value", self.owner.Name, value))
        return self.owner.accepts


class Control:
    def __init__(
        self,
        world: "World",
        type_name: str,
        name: str = "",
        *,
        children: list["Control"] | None = None,
        enabled: bool = True,
        offscreen: bool = False,
        password: bool = False,
        patterns: tuple[str, ...] = (),
        read_only: bool = False,
        accepts: bool = True,
        class_name: str = "",
        pid: int = 4242,
        rect: tuple[int, int, int, int] = (10, 20, 110, 60),
    ) -> None:
        self.world = world
        self._type, self._name, self._children = type_name, name, children or []
        self._enabled, self._offscreen, self._password = enabled, offscreen, password
        self.patterns, self.read_only, self.accepts = patterns, read_only, accepts
        self._class, self._pid, self._rect = class_name, pid, rect
        self.handle = next(_ids)

    def _t(self) -> None:
        self.world.check_thread()

    @property
    def Name(self) -> str:  # noqa: N802
        self._t()
        return self._name

    @Name.setter
    def Name(self, value: str) -> None:  # noqa: N802
        self._name = value

    @property
    def ControlTypeName(self) -> str:  # noqa: N802
        self._t()
        return self._type

    @property
    def IsEnabled(self) -> bool:  # noqa: N802
        self._t()
        return self._enabled

    @property
    def IsOffscreen(self) -> bool:  # noqa: N802
        self._t()
        return self._offscreen

    @property
    def IsPassword(self) -> bool:  # noqa: N802
        self._t()
        return self._password

    @property
    def ClassName(self) -> str:  # noqa: N802
        self._t()
        return self._class

    @property
    def ProcessId(self) -> int:  # noqa: N802
        self._t()
        return self._pid

    @property
    def NativeWindowHandle(self) -> int:  # noqa: N802
        self._t()
        return self.handle

    @property
    def BoundingRectangle(self) -> Rect:  # noqa: N802
        self._t()
        return Rect(*self._rect)

    def GetChildren(self) -> list["Control"]:  # noqa: N802
        self._t()
        return list(self._children)

    def GetInvokePattern(self) -> InvokePattern | None:  # noqa: N802
        self._t()
        return InvokePattern(self) if "invoke" in self.patterns else None

    def GetSelectionItemPattern(self) -> SelectionItemPattern | None:  # noqa: N802
        self._t()
        return SelectionItemPattern(self) if "select" in self.patterns else None

    def GetValuePattern(self) -> ValuePattern | None:  # noqa: N802
        self._t()
        return ValuePattern(self) if "value" in self.patterns else None


class World:
    """One fake desktop. `module` is what is passed in place of `import uiautomation`."""

    def __init__(self) -> None:
        self.log: list[tuple[Any, ...]] = []
        self.ui_thread: int | None = None
        self.init_calls = 0
        self.root = Control(self, "PaneControl", "Desktop")
        self.foreground: Control | None = None
        self.module = self._module()

    def check_thread(self) -> None:
        if self.ui_thread is None or threading.get_ident() != self.ui_thread:
            raise RuntimeError("UI Automation used on a thread that did not initialise it")

    def add_window(
        self, title: str, children: list[Control], *, pid: int = 4242, foreground: bool = False, **kw: Any
    ) -> Control:
        win = Control(self, "WindowControl", title, children=children, pid=pid, **kw)
        self.root._children.append(win)
        if foreground:
            self.foreground = win
        return win

    def _module(self) -> Any:
        world = self

        class Module:
            @staticmethod
            def InitializeUIAutomationInCurrentThread() -> None:  # noqa: N802
                world.ui_thread = threading.get_ident()
                world.init_calls += 1

            @staticmethod
            def UninitializeUIAutomationInCurrentThread() -> None:  # noqa: N802
                world.ui_thread = None

            @staticmethod
            def GetRootControl() -> Control:  # noqa: N802
                world.check_thread()
                return world.root

            @staticmethod
            def GetForegroundControl() -> Control:  # noqa: N802
                world.check_thread()
                return world.foreground or world.root

        return Module
