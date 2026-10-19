"""What a platform backend must provide. Linux (AT-SPI), Windows (UI Automation) and a fake for tests."""

from typing import Literal, Protocol

from kmr_desktop.model import ActionResult, RawNode, WindowInfo


class Platform(Protocol):
    name: Literal["linux", "windows"]

    def list_windows(self) -> list[WindowInfo]:
        """Open windows. Names only; nothing inside them is read."""

    def snapshot(self, window_id: str, max_nodes: int) -> list[RawNode]:
        """The elements of one window. Never reads what is typed into a field."""

    def perform(
        self, window_id: str, node_id: str, kind: Literal["press", "type"], text: str | None
    ) -> ActionResult:
        """Does ONE action on ONE element from the latest snapshot, after checking it is still the
        same element (same role and name). Refuses if the window has changed."""
