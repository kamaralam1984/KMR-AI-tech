import pytest
from helpers.scene import FakeServer, scene, step

from kmr_desktop.session import Agent, AgentError


def make(steps=None, **kw):  # type: ignore[no-untyped-def]
    platform = scene()
    server = FakeServer(steps)
    agent = Agent(platform, server.client(), **kw)
    return platform, server, agent


def sent(agent: Agent, message: str = "stuck", windows=("w1",)):  # type: ignore[no-untyped-def]
    p = agent.prepare(message, "auto", list(windows))
    return agent.send(p.prepared_id, p.sha256)


def test_the_window_list_hides_sensitive_windows_and_only_counts_them() -> None:
    _, _, agent = make()
    listing = agent.windows()
    assert [w["app"] for w in listing["windows"]] == ["Chromium"] and listing["hidden"] == 2


def test_nothing_is_sent_without_a_matching_preview() -> None:
    _, server, agent = make()
    p = agent.prepare("stuck", "auto", ["w1"])
    with pytest.raises(AgentError, match="not what would be sent"):
        agent.send(p.prepared_id, "0" * 64)
    p.payload["message"] = "tampered after the preview"
    with pytest.raises(AgentError, match="not what would be sent"):
        agent.send(p.prepared_id, p.sha256)
    with pytest.raises(AgentError, match="expired"):
        agent.send("nope", p.sha256)
    assert [r for r in server.requests if r[1] == "/desk/v1/chat"] == []  # nothing left the computer


def test_a_preview_expires() -> None:
    now = [0.0]
    _, _, agent = make(clock=lambda: now[0])
    p = agent.prepare("stuck", "auto", ["w1"])
    now[0] = 601
    agent.prepare("again", "auto", ["w1"])  # housekeeping drops the old one
    with pytest.raises(AgentError, match="expired"):
        agent.send(p.prepared_id, p.sha256)


def test_without_a_connection_it_says_so() -> None:
    agent = Agent(scene(), None)
    p = agent.prepare("x", "auto", ["w1"])
    with pytest.raises(AgentError, match="Connect this app"):
        agent.send(p.prepared_id, p.sha256)


def test_server_errors_are_shown_in_plain_words() -> None:
    _, server, agent = make()
    server.fail_chat = (429, "Your organization reached its daily assistant limit.")
    p = agent.prepare("x", "auto", ["w1"])
    with pytest.raises(AgentError, match="daily assistant limit"):
        agent.send(p.prepared_id, p.sha256)


def test_an_approved_press_is_done_once_and_reported() -> None:
    platform, server, agent = make([step("press", "w1", "w1.n0", "button", "Reload")])
    reply = sent(agent)
    sid = reply["steps"][0]["id"]
    assert platform.actions == []  # offering a step does nothing
    assert agent.approve(sid)["state"] == "executed"
    assert platform.actions == [("w1", "w1.n0", "press", None)]
    assert server.reports == [(sid, "executed")]
    with pytest.raises(AgentError, match="already executed"):
        agent.approve(sid)  # one approval, one action
    assert len(platform.actions) == 1


def test_a_rejected_step_does_nothing_and_cannot_be_approved_later() -> None:
    platform, server, agent = make([step("press", "w1", "w1.n0", "button", "Reload")])
    sid = sent(agent)["steps"][0]["id"]
    assert agent.reject(sid)["state"] == "rejected" and server.reports == [(sid, "rejected")]
    with pytest.raises(AgentError, match="already rejected"):
        agent.approve(sid)
    assert platform.actions == []


def test_an_approval_expires_after_two_minutes() -> None:
    now = [0.0]
    platform, server, agent = make([step("press", "w1", "w1.n0", "button", "Reload")], clock=lambda: now[0])
    sid = sent(agent)["steps"][0]["id"]
    now[0] = 121
    with pytest.raises(AgentError, match="expired"):
        agent.approve(sid)
    assert platform.actions == [] and server.reports == [(sid, "expired")]


def test_the_agent_refuses_what_the_server_should_never_have_offered() -> None:
    """A step the server proposes is checked again here, against the LOCAL element."""
    cases = [
        step("press", "w1", "w1.n5", "button", "Submit order"),  # consequential
        step("type", "w1", "w1.n4", "text_field", "Password", "hunter2"),  # a password field
        step("press", "w1", "w1.n2", "button", "Could not save"),  # really a label
        step("press", "w1", "w1.n99", "button", "Reload"),  # an element that was never listed
    ]
    platform, server, agent = make(cases)
    reply = sent(agent)
    for s in reply["steps"]:
        with pytest.raises(AgentError):
            agent.approve(s["id"], "x")
    assert platform.actions == []
    assert {o for _, o in server.reports} == {"refused"}


def test_typing_needs_the_persons_text_and_refuses_personal_data_or_secrets() -> None:
    platform, _, agent = make([step("type", "w1", "w1.n3", "text_field", "Search", "slow loading fix")])
    sid = sent(agent)["steps"][0]["id"]
    for bad in ("", "   ", "write to jane.doe@example.com", "call 9876543210", "my password is x", "x" * 121):
        with pytest.raises(AgentError):
            agent.approve(sid, bad)
    assert platform.actions == []  # the step stays offered so the person can correct the text
    assert agent.approve(sid, "  slow   loading fix ")["state"] == "executed"
    assert platform.actions == [("w1", "w1.n3", "type", "slow loading fix")]


def test_highlights_need_no_permission_and_change_nothing() -> None:
    platform, _, agent = make([step("highlight", "w1", "w1.n1", "button", "")])
    sid = sent(agent)["steps"][0]["id"]
    assert "bounds" in agent.highlight(sid)
    with pytest.raises(AgentError, match="needs no permission"):
        agent.approve(sid)
    assert platform.actions == []


def test_a_window_that_became_sensitive_is_refused() -> None:
    platform, server, agent = make([step("press", "w1", "w1.n0", "button", "Reload")])
    sid = sent(agent)["steps"][0]["id"]
    platform.windows["w1"].info = type(platform.windows["w1"].info)(
        "w1", "Chromium", "SBI NetBanking - Login", True
    )
    with pytest.raises(AgentError, match="not available"):
        agent.approve(sid)
    assert platform.actions == [] and server.reports == [(sid, "refused")]


def test_a_failed_action_is_reported_as_failed() -> None:
    platform, server, agent = make([step("press", "w1", "w1.n0", "button", "Reload")])
    sid = sent(agent)["steps"][0]["id"]
    del platform.windows["w1"].nodes[0]  # the element disappeared before the press
    with pytest.raises(AgentError):
        agent.approve(sid)
    assert server.reports[-1][1] in ("refused", "failed")


def test_unknown_steps_are_refused_and_history_is_redacted() -> None:
    _, server, agent = make()
    with pytest.raises(AgentError, match="not known"):
        agent.approve("123")
    sent(agent, "my email is jane.doe@example.com")
    sent(agent, "second question")
    second = [r for r in server.requests if r[1] == "/desk/v1/chat"][1][2]
    assert "jane.doe" not in str(second["history"]) and second["history"][0]["role"] == "user"
