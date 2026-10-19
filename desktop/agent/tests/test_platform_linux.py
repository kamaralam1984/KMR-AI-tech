"""The Linux backend against REAL windows: a small GTK window the test opens, read and driven through
the accessibility bus. Skipped where there is no accessibility bus or GTK."""

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

HELPER = Path(__file__).parent / "helpers" / "gtk_test_app.py"
CLEAN = {
    k: v
    for k, v in os.environ.items()
    if k not in ("GSETTINGS_SCHEMA_DIR", "GTK_PATH", "GTK_EXE_PREFIX", "GIO_MODULE_DIR")
}
CLEAN["XDG_DATA_DIRS"] = "/usr/local/share:/usr/share"


@pytest.fixture(scope="module")
def window():  # type: ignore[no-untyped-def]
    if sys.platform != "linux":
        pytest.skip("Unavailable: not Linux")
    try:
        from kmr_desktop.platforms.linux_atspi import LinuxAtspi

        platform = LinuxAtspi()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Unavailable: no accessibility bus ({type(exc).__name__})")
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        pytest.skip("Unavailable: no display for the test window")
    proc = subprocess.Popen(
        ["/usr/bin/python3", str(HELPER), "60"],
        env=CLEAN,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )  # noqa: S603
    try:
        if proc.stdout.readline().strip() != "READY":  # type: ignore[union-attr]
            pytest.skip("Unavailable: GTK test window did not start")
        proc.lines = queue.Queue()  # type: ignore[attr-defined]
        threading.Thread(
            target=lambda: [proc.lines.put(line.strip()) for line in proc.stdout],  # type: ignore[attr-defined,union-attr]
            daemon=True,
        ).start()
        found = None
        for _ in range(20):
            found = next((w for w in platform.list_windows() if w.title == "KMR Test Window"), None)
            if found:
                break
            time.sleep(0.25)
        if found is None:
            pytest.skip("Unavailable: the test window is not on the accessibility bus")
        yield platform, found, proc
    finally:
        proc.terminate()


def events(proc) -> list[str]:  # type: ignore[no-untyped-def]
    """What the test window reported since the last call (its output is read by a thread)."""
    time.sleep(1.2)  # GTK reports a moment after the action
    out = []
    while True:
        try:
            line = proc.lines.get_nowait()
        except queue.Empty:
            return out
        if line.startswith("EVENT"):
            out.append(line)


def named(platform, found):  # type: ignore[no-untyped-def]
    return {n.name: n for n in platform.snapshot(found.window_id, 200) if n.name}


def test_it_sees_the_elements_names_and_flags_the_password_field(window) -> None:  # type: ignore[no-untyped-def]
    platform, found, proc = window
    nodes = platform.snapshot(found.window_id, 200)
    by_name = {n.name: n for n in nodes if n.name}
    assert by_name["Open settings"].role == "button" and by_name["Open settings"].actionable
    assert by_name["Search"].role == "text_field" and by_name["Search"].actionable
    assert by_name["Password"].password is True and "password" in by_name["Password"].states
    assert any(n.role == "button" and not n.name and n.actionable for n in nodes)  # the unnamed button
    assert all(n.text == "" for n in nodes if n.role == "text_field")  # values are never read
    assert any(n.role == "label" and n.text == "Welcome to the test window" for n in nodes)
    assert events(proc) == []  # looking pressed and typed nothing


def test_it_presses_and_types_through_accessibility_actions(window) -> None:  # type: ignore[no-untyped-def]
    platform, found, proc = window
    nodes = named(platform, found)
    events(proc)
    assert platform.perform(found.window_id, nodes["Open settings"].node_id, "press", None).ok
    assert platform.perform(found.window_id, nodes["Search"].node_id, "type", "slow loading fix").ok
    seen = events(proc)
    assert "EVENT pressed:open-settings" in seen and "EVENT text:search:slow loading fix" in seen


def test_a_stale_or_changed_element_is_refused(window) -> None:  # type: ignore[no-untyped-def]
    platform, found, _ = window
    nodes = named(platform, found)
    assert platform.perform(found.window_id, "a99w9.n9", "press", None).ok is False
    assert platform.perform(found.window_id, nodes["Open settings"].node_id + "x", "press", None).ok is False
    assert (
        platform.perform(found.window_id, nodes["Open settings"].node_id, "type", "x").ok is False
    )  # a button is not a field


def test_the_agent_policy_keeps_it_away_from_the_submit_button_and_the_password_field(window) -> None:  # type: ignore[no-untyped-def]
    """The platform CAN press anything; the policy in the session is what says no."""
    from helpers.scene import FakeServer, step

    from kmr_desktop.session import Agent, AgentError

    platform, found, proc = window
    nodes = named(platform, found)
    server = FakeServer(
        [
            step("press", found.window_id, nodes["Submit order"].node_id, "button", "Submit order"),
            step("type", found.window_id, nodes["Password"].node_id, "text_field", "Password", "x"),
            step("press", found.window_id, nodes["Open settings"].node_id, "button", "Open settings"),
        ]
    )
    agent = Agent(platform, server.client())
    prepared = agent.prepare("help", "auto", [found.window_id])
    reply = agent.send(prepared.prepared_id, prepared.sha256)
    events(proc)
    outcomes = []
    for s in reply["steps"]:
        try:
            agent.approve(s["id"], "x")
            outcomes.append("done")
        except AgentError:
            outcomes.append("refused")
    assert outcomes == ["refused", "refused", "done"]
    assert events(proc) == [
        "EVENT pressed:open-settings"
    ]  # the submit button and the password field were never touched
