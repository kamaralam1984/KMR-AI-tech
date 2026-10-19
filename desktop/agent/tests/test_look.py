import json

from helpers.scene import FAKE_EMAIL, FAKE_PHONE, scene

from kmr_desktop.look import digest, prepare


def look(platform=None, windows=("w1", "w2", "w3"), message="my page is stuck", **kw):  # type: ignore[no-untyped-def]
    platform = platform or scene()
    return platform, prepare(platform, message=message, language="auto", window_ids=list(windows), **kw)


def test_sensitive_windows_are_never_read_not_even_a_snapshot() -> None:
    platform, p = look()
    assert platform.snapshots == ["w1"]  # the vault and the inbox were never opened
    assert p.blocked == 2 and p.windows == 1
    wire = json.dumps(p.payload)
    assert "hunter2" not in wire and "private words" not in wire and "Bitwarden" not in wire


def test_nothing_personal_survives_and_values_are_never_read() -> None:
    _, p = look()
    wire = json.dumps(p.payload)
    assert FAKE_EMAIL not in wire and FAKE_PHONE not in wire
    assert "[EMAIL]" in wire and "[PHONE]" in wire
    assert p.redacted_counts["email"] >= 1 and p.redacted_counts["phone"] >= 1
    assert not any(
        n["name"] == "" and n["text"] == "" and not n["actionable"]
        for w in p.payload["look"]["windows"]
        for n in w["nodes"]
    )
    fields = [n for w in p.payload["look"]["windows"] for n in w["nodes"] if n["role"] == "text_field"]
    assert {n["name"] for n in fields} == {"Search", "Password"} and all(n["text"] == "" for n in fields)


def test_the_message_and_history_are_redacted_too() -> None:
    _, p = look(message=f"email me at {FAKE_EMAIL}", history=[{"role": "user", "text": f"call {FAKE_PHONE}"}])
    wire = json.dumps(p.payload)
    assert FAKE_EMAIL not in wire and FAKE_PHONE not in wire


def test_the_preview_is_exactly_what_would_be_sent_and_the_hash_covers_it() -> None:
    _, p = look()
    assert json.loads(p.preview) == p.payload
    assert p.sha256 == digest(p.payload)
    p.payload["message"] = "something else"
    assert digest(p.payload) != p.sha256  # any change is detectable


def test_no_window_chosen_means_nothing_is_read() -> None:
    platform, p = look(windows=())
    assert platform.snapshots == [] and p.payload["look"] is None and p.nodes == 0


def test_limits_on_windows_and_nodes() -> None:
    from helpers.scene import node

    from kmr_desktop.model import WindowInfo
    from kmr_desktop.platforms.fake import FakePlatform, FakeWindow

    wins = [
        FakeWindow(
            WindowInfo(f"w{i}", "App", f"Doc {i}"),
            [node(f"w{i}.n{j}", "button", f"B{j}") for j in range(500)],
        )
        for i in range(8)
    ]
    platform = FakePlatform(wins)
    p = prepare(platform, message="x", language="en", window_ids=[w.info.window_id for w in wins])
    assert p.windows == 5 and all(len(w["nodes"]) == 300 for w in p.payload["look"]["windows"])


def test_unknown_window_ids_are_ignored() -> None:
    platform, p = look(windows=("nope", "w1"))
    assert platform.snapshots == ["w1"] and p.windows == 1


def test_the_windows_own_title_bar_buttons_are_left_out() -> None:
    from helpers.scene import node

    from kmr_desktop.model import WindowInfo
    from kmr_desktop.platforms.fake import FakePlatform, FakeWindow

    win = FakeWindow(
        WindowInfo("w1", "App", "Doc"),
        [node("n1", "button", "Minimize"), node("n2", "button", "Close"), node("n3", "button", "Save")],
    )
    p = prepare(FakePlatform([win]), message="x", language="en", window_ids=["w1"])
    assert [n["name"] for n in p.payload["look"]["windows"][0]["nodes"]] == ["Save"]
