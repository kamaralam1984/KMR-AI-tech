"""An in-memory platform for tests: windows with elements, and a record of every action done."""

from dataclasses import dataclass, field
from typing import Literal

from kmr_desktop.model import ActionResult, RawNode, WindowInfo


@dataclass
class FakeWindow:
    info: WindowInfo
    nodes: list[RawNode] = field(default_factory=list)


class FakePlatform:
    name: Literal["linux", "windows"] = "linux"

    def __init__(self, windows: list[FakeWindow]) -> None:
        self.windows = {w.info.window_id: w for w in windows}
        self.actions: list[tuple[str, str, str, str | None]] = []
        self.snapshots: list[str] = []

    def list_windows(self) -> list[WindowInfo]:
        return [w.info for w in self.windows.values()]

    def snapshot(self, window_id: str, max_nodes: int) -> list[RawNode]:
        self.snapshots.append(window_id)
        return [n for n in self.windows[window_id].nodes][:max_nodes]

    def perform(
        self, window_id: str, node_id: str, kind: Literal["press", "type"], text: str | None
    ) -> ActionResult:
        window = self.windows.get(window_id)
        node = next((n for n in (window.nodes if window else []) if n.node_id == node_id), None)
        if node is None:
            return ActionResult(False, "The element is no longer there.")
        self.actions.append((window_id, node_id, kind, text))
        return ActionResult(True, "done")
