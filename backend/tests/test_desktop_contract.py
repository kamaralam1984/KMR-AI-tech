"""The desktop agent and the server must agree: what the agent builds is accepted, and what the
server's policy refuses the agent's policy refuses too."""

import sys
from pathlib import Path

import pytest

from app.desktop.schemas import ChatIn

AGENT = Path(__file__).resolve().parents[2] / "desktop" / "agent"


@pytest.fixture(scope="module")
def agent():  # type: ignore[no-untyped-def]
    if not AGENT.exists():
        pytest.skip("Unavailable: desktop agent sources not present")
    sys.path.insert(0, str(AGENT))
    sys.path.insert(0, str(AGENT / "tests"))
    try:
        from helpers.scene import scene  # type: ignore[import-not-found]
        from kmr_desktop import look  # type: ignore[import-not-found]
    except ImportError as exc:
        pytest.skip(f"Unavailable: {exc}")
    return scene, look


def test_what_the_agent_builds_passes_the_servers_schema_and_privacy_checks(agent) -> None:  # type: ignore[no-untyped-def]
    from app.desktop.policy import sensitive_window
    from app.extension.policy import find_violations

    scene, look = agent
    prepared = look.prepare(
        scene(), message="my page is stuck", language="auto", window_ids=["w1", "w2", "w3"]
    )
    parsed = ChatIn.model_validate(prepared.payload)  # extra="forbid": any unknown field fails
    assert parsed.look is not None and len(parsed.look.windows) == 1
    for w in parsed.look.windows:
        assert sensitive_window(w.app, w.title) is None
    assert find_violations(prepared.payload) == []  # what is left would not be rejected


def test_every_role_the_agent_can_send_is_one_the_server_accepts(agent) -> None:  # type: ignore[no-untyped-def]
    from typing import get_args

    from kmr_desktop.model import Role as AgentRole  # type: ignore[import-not-found]

    from app.desktop.schemas import Role as ServerRole

    assert set(get_args(AgentRole)) == set(get_args(ServerRole))
