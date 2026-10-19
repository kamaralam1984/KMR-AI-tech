"""The Windows backend against a REAL Windows window: a Windows Forms form the test opens, read and
driven through the real `uiautomation` library. It only runs on Windows (see .github/workflows/desktop.yml).
This is the test that decides whether the backend really works; the fake-based tests only check the logic."""

import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

HELPER = Path(__file__).parent / "helpers" / "winforms_test_app.ps1"


@pytest.fixture(scope="module")
def window():  # type: ignore[no-untyped-def]
    if sys.platform != "win32":
        pytest.skip("Unavailable: not Windows")
    try:
        from kmr_desktop.platforms.windows_uia import WindowsUia

        platform = WindowsUia()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Unavailable: UI Automation not usable ({type(exc).__name__}: {exc})")
    proc = subprocess.Popen(  # noqa: S603 - a fixed helper script
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HELPER), "-Seconds", "120"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: [lines.put(x.strip()) for x in proc.stdout], daemon=True).start()  # type: ignore[union-attr]
    try:
        if lines.get(timeout=60) != "READY":
            pytest.skip("Unavailable: the test window did not start")
        found = None
        for _ in range(40):
            found = next((w for w in platform.list_windows() if w.title == "KMR Test Window"), None)
            if found:
                break
            time.sleep(0.25)
        assert found is not None, "the test window is not visible to UI Automation"
        proc.lines = lines  # type: ignore[attr-defined]
        yield platform, found, proc
    finally:
        proc.terminate()
        platform.close()


def events(proc) -> list[str]:  # type: ignore[no-untyped-def]
    time.sleep(1.2)
    out = []
    while True:
        try:
            line = proc.lines.get_nowait()
        except queue.Empty:
            return out
        if line.startswith("EVENT"):
            out.append(line)


def named(platform, found):  # type: ignore[no-untyped-def]
    return {n.name: n for n in platform.snapshot(found.window_id, 300) if n.name}


def test_it_sees_names_and_flags_the_password_field(window) -> None:  # type: ignore[no-untyped-def]
    platform, found, proc = window
    nodes = platform.snapshot(found.window_id, 300)
    by = {n.name: n for n in nodes if n.name}
    assert by["Open settings"].role == "button" and by["Open settings"].actionable
    assert by["Search"].role == "text_field" and by["Search"].actionable
    assert by["Password"].password is True
    assert any(n.role == "button" and not n.name and n.actionable for n in nodes)
    assert any(n.role == "label" and "Welcome to the test window" in n.text for n in nodes)
    assert all(n.text == "" for n in nodes if n.role == "text_field")
    assert events(proc) == []  # looking pressed and typed nothing


def test_it_presses_and_types_through_ui_automation(window) -> None:  # type: ignore[no-untyped-def]
    platform, found, proc = window
    nodes = named(platform, found)
    events(proc)
    assert platform.perform(found.window_id, nodes["Open settings"].node_id, "press", None).ok
    assert platform.perform(found.window_id, nodes["Search"].node_id, "type", "slow loading fix").ok
    seen = events(proc)
    assert "EVENT pressed:open-settings" in seen and "EVENT text:search:slow loading fix" in seen


def test_a_stale_element_and_a_password_field_are_refused(window) -> None:  # type: ignore[no-untyped-def]
    platform, found, proc = window
    nodes = named(platform, found)
    events(proc)
    assert platform.perform(found.window_id, "w9.n9", "press", None).ok is False
    assert platform.perform(found.window_id, nodes["Password"].node_id, "type", "x").ok is False
    assert platform.perform(found.window_id, nodes["Open settings"].node_id, "type", "x").ok is False
    assert events(proc) == []


def test_the_agent_policy_keeps_it_from_submit_and_the_password_field(window) -> None:  # type: ignore[no-untyped-def]
    from helpers.scene import FakeServer, step

    from kmr_desktop.session import Agent, AgentError

    platform, found, proc = window
    nodes = named(platform, found)
    server = FakeServer(
        [
            step("press", found.window_id, nodes["Submit order"].node_id, "button", "Submit order"),
            step("type", found.window_id, nodes["Password"].node_id, "text_field", "Password", "x"),
            step("press", found.window_id, nodes["Reload"].node_id, "button", "Reload"),
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
    assert events(proc) == ["EVENT pressed:reload"]
