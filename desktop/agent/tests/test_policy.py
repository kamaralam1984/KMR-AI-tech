import importlib.util

import pytest

from kmr_desktop import policy


def server_policy(root):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "server_policy", root / "backend" / "app" / "desktop" / "policy.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_the_agent_and_the_server_refuse_exactly_the_same_things(root) -> None:  # type: ignore[no-untyped-def]
    server = server_policy(root)
    assert policy.SENSITIVE_APPS == server.SENSITIVE_APPS
    assert policy.SENSITIVE_TITLE == server.SENSITIVE_TITLE
    assert policy.CONSEQUENTIAL.pattern == server.CONSEQUENTIAL.pattern
    assert policy.SECRET_FIELD.pattern == server.SECRET_FIELD.pattern
    assert policy.PRESS_ROLES == server.PRESS_ROLES and policy.TYPE_ROLES == server.TYPE_ROLES


@pytest.mark.parametrize(
    "name",
    [
        "Submit",
        "Send message",
        "Delete account",
        "Sign out",
        "I agree",
        "Place order",
        "Confirm payment",
        "Close",
        "Quit",
        "Shut down",
        "Apply",
        "Install",
    ],
)
def test_consequential_buttons_are_left_to_the_person(name: str) -> None:
    assert policy.step_refusal("press", "button", name) is not None
    assert policy.step_refusal("highlight", "button", name) is None


@pytest.mark.parametrize("name", ["Open settings", "Reload", "Next", "Search", "Help", "Maximize"])
def test_ordinary_buttons_can_be_pressed(name: str) -> None:
    assert policy.step_refusal("press", "button", name) is None


def test_secret_fields_are_never_typed_into_even_when_the_name_is_innocent() -> None:
    assert policy.step_refusal("type", "text_field", "Search") is None
    for name in (
        "Password",
        "Enter OTP",
        "Card number",
        "CVV",
        "Aadhaar",
        "Account number",
        "API key",
        "PIN",
    ):
        assert policy.step_refusal("type", "text_field", name) is not None
    assert policy.step_refusal("type", "text_field", "Notes", password=True) is not None
    assert policy.step_refusal("type", "button", "Search") is not None
    assert policy.step_refusal("type", "text_field", "Search", actionable=False) is not None


def test_the_person_can_add_apps_to_the_block_list_but_not_remove_defaults() -> None:
    assert policy.sensitive_window("Notes", "Todo", ("notes",)) is not None
    assert policy.sensitive_window("Notes", "Todo") is None
    assert policy.sensitive_window("Bitwarden", "x", ()) is not None
    assert policy.sensitive_window("Chromium", "SBI NetBanking - Login") is not None
