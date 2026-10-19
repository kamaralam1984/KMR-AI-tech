"""The Windows backend, exercised on any OS against a faithful fake of the `uiautomation` package.

This proves the logic: which windows and elements are listed, that values are never read, that policy
inputs (password, read-only) are right, that stale elements are refused, and that every UI Automation
call runs on the one thread that initialised it. It does NOT prove the real library behaves as the
fake does; that is what tests/test_platform_windows_live.py is for, on a Windows machine.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from helpers.fake_uia import Control, World

from kmr_desktop.platforms.windows_uia import WindowsUia, map_role


def make(world: World | None = None) -> tuple[World, WindowsUia]:
    world = world or World()
    return world, WindowsUia(
        world.module, process_name=lambda pid: {4242: "chrome", 5000: "notepad"}.get(pid, "")
    )


def shop(world: World) -> Control:
    return world.add_window(
        "Shop - Support",
        [
            Control(world, "TextControl", "Error: could not load the page"),
            Control(world, "ButtonControl", "Reload", patterns=("invoke",)),
            Control(world, "ButtonControl", "", patterns=("invoke",)),  # no name
            Control(world, "ButtonControl", "Submit order", patterns=("invoke",)),
            Control(world, "EditControl", "Search", patterns=("value",)),
            Control(world, "EditControl", "Password", patterns=("value",), password=True),
            Control(world, "EditControl", "Locked note", patterns=("value",), read_only=True),
            Control(world, "TabItemControl", "Settings", patterns=("select",)),
            Control(world, "ButtonControl", "Hidden", patterns=("invoke",), offscreen=True),
            Control(
                world,
                "PaneControl",
                "Layout",
                children=[Control(world, "HyperlinkControl", "Help", patterns=("invoke",))],
            ),
        ],
        foreground=True,
    )


def named(nodes):  # type: ignore[no-untyped-def]
    return {n.name: n for n in nodes if n.name}


def test_control_types_map_to_the_shared_roles() -> None:
    assert map_role("ButtonControl") == "button" and map_role("HyperlinkControl") == "link"
    assert map_role("EditControl") == "text_field" and map_role("EditControl", multiline=True) == "text_area"
    assert map_role("TextControl") == "label" and map_role("SomethingNew") == "other"


def test_it_lists_application_windows_and_skips_the_shell() -> None:
    world, uia = make()
    shop(world)
    world.add_window("Notes", [], pid=5000)
    world.add_window("Program Manager", [], class_name="Progman")
    world.add_window("", [])
    world.add_window("Off screen", [], offscreen=True)
    world.root._children.append(Control(world, "PaneControl", "Taskbar"))
    windows = uia.list_windows()
    assert [(w.app, w.title) for w in windows] == [("chrome", "Shop - Support"), ("notepad", "Notes")]
    assert windows[0].active and not windows[1].active
    uia.close()


def test_it_reads_names_and_label_text_flags_passwords_and_skips_hidden_elements() -> None:
    world, uia = make()
    shop(world)
    window = uia.list_windows()[0]
    nodes = uia.snapshot(window.window_id, 100)
    by = named(nodes)
    assert by["Reload"].role == "button" and by["Reload"].actionable
    assert (
        by["Error: could not load the page"].role == "label"
        and by["Error: could not load the page"].text == "Error: could not load the page"
    )
    assert by["Search"].role == "text_field" and by["Search"].actionable and by["Search"].text == ""
    assert by["Password"].password and "password" in by["Password"].states
    assert by["Locked note"].actionable is False  # read-only: it cannot be typed into
    assert by["Settings"].role == "tab" and by["Help"].role == "link"  # found inside a pane
    assert "Hidden" not in by  # off screen
    assert by["Layout"].role == "other"  # kept here, dropped later by look.py
    assert any(n.role == "button" and not n.name and n.actionable for n in nodes)
    assert all(n.bounds == (10, 20, 100, 40) for n in nodes)
    uia.close()


def test_a_field_value_is_never_read() -> None:
    """The fake raises if anyone touches ValuePattern.Value; reading names and asking read-only is fine."""
    world, uia = make()
    shop(world)
    window = uia.list_windows()[0]
    uia.snapshot(window.window_id, 100)  # would raise inside the fake if a value were read
    uia.close()


def test_press_uses_invoke_then_selection_and_reports_what_the_app_said() -> None:
    world, uia = make()
    shop(world)
    window = uia.list_windows()[0]
    by = named(uia.snapshot(window.window_id, 100))
    assert uia.perform(window.window_id, by["Reload"].node_id, "press", None).ok
    assert uia.perform(window.window_id, by["Settings"].node_id, "press", None).ok
    assert [e[:2] for e in world.log] == [("invoke", "Reload"), ("select", "Settings")]
    refusing = next(c for c in world.root._children[0]._children if c._name == "Reload")
    refusing.accepts = False
    result = uia.perform(window.window_id, by["Reload"].node_id, "press", None)
    assert result.ok is False and "did not accept" in result.detail  # the library's False is not hidden
    uia.close()


def test_type_sets_text_only_in_a_writable_non_password_field() -> None:
    world, uia = make()
    shop(world)
    window = uia.list_windows()[0]
    by = named(uia.snapshot(window.window_id, 100))
    assert uia.perform(window.window_id, by["Search"].node_id, "type", "slow loading fix").ok
    assert world.log[-1] == ("set_value", "Search", "slow loading fix")
    for field in ("Password", "Locked note"):
        before = len(world.log)
        assert uia.perform(window.window_id, by[field].node_id, "type", "x").ok is False
        assert len(world.log) == before  # nothing was set
    assert (
        uia.perform(window.window_id, by["Reload"].node_id, "type", "x").ok is False
    )  # a button is not a field
    uia.close()


def test_a_changed_or_missing_element_is_refused() -> None:
    world, uia = make()
    win = shop(world)
    window = uia.list_windows()[0]
    by = named(uia.snapshot(window.window_id, 100))
    assert uia.perform(window.window_id, "w9.n9", "press", None).ok is False
    next(
        c for c in win._children if c._name == "Reload"
    )._name = "Delete everything"  # the UI changed after the look
    assert "changed" in uia.perform(window.window_id, by["Reload"].node_id, "press", None).detail
    win._name = "Another window"
    assert uia.perform(window.window_id, by["Search"].node_id, "type", "x").ok is False
    assert world.log == []
    uia.close()


def test_a_disabled_or_vanished_element_is_refused_not_crashed() -> None:
    world, uia = make()
    win = shop(world)
    window = uia.list_windows()[0]
    by = named(uia.snapshot(window.window_id, 100))
    next(c for c in win._children if c._name == "Reload")._enabled = False
    assert "disabled" in uia.perform(window.window_id, by["Reload"].node_id, "press", None).detail
    next(c for c in win._children if c._name == "Search")._children = None  # type: ignore[assignment]
    broken = next(c for c in win._children if c._name == "Settings")
    broken.GetSelectionItemPattern = lambda: (_ for _ in ()).throw(OSError("gone"))  # type: ignore[method-assign]
    assert "did not respond" in uia.perform(window.window_id, by["Settings"].node_id, "press", None).detail
    uia.close()


def test_every_call_runs_on_the_one_thread_that_initialised_ui_automation() -> None:
    """Controls cannot cross threads. The local API serves requests on many threads."""
    world, uia = make()
    shop(world)
    window = uia.list_windows()[0]

    def work(_: int) -> str:
        nodes = uia.snapshot(window.window_id, 100)
        uia.list_windows()
        return threading.current_thread().name + str(len(nodes))

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(work, range(24)))
    assert world.init_calls == 1 and len(set(results)) >= 1
    assert threading.get_ident() != world.ui_thread  # tests really are on other threads
    uia.close()


def test_a_fake_that_is_used_on_the_wrong_thread_would_fail() -> None:
    """Guards the guard: the fake does reject another thread, so the test above means something."""
    world, uia = make()
    shop(world)
    uia.list_windows()
    with pytest.raises(RuntimeError, match="did not initialise"):
        world.root.GetChildren()  # called here, not on the UI thread
    uia.close()


def test_one_broken_window_does_not_hide_the_others() -> None:
    world, uia = make()
    bad = world.add_window("Bad", [])
    bad.__class__ = type(
        "Broken", (Control,), {"ClassName": property(lambda self: (_ for _ in ()).throw(OSError("boom")))}
    )
    world.add_window("Good", [], pid=5000)
    assert [w.title for w in uia.list_windows()] == ["Good"]
    uia.close()
