import json
import os
import stat
import threading
import urllib.error
import urllib.request

import pytest
from helpers.scene import FakeServer, scene, step

from kmr_desktop import config as config_module
from kmr_desktop.local_api import LocalApi
from kmr_desktop.session import Agent

SECRET = "s" * 40


@pytest.fixture
def api(tmp_path):  # type: ignore[no-untyped-def]
    platform = scene()
    server = FakeServer([step("press", "w1", "w1.n0", "button", "Reload")])
    agent = Agent(platform, server.client())
    cfg = config_module.Config(server_url="http://server.test", token="kmr_dt_secret_token_value")
    local = LocalApi(agent, SECRET, cfg, tmp_path / "config.json")
    httpd = local.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield local, httpd.server_address[1], platform, server, tmp_path
    httpd.shutdown()


def call(
    port: int,
    method: str,
    path: str,
    body=None,
    headers=None,
    secret: str | None = SECRET,
    host: str | None = None,
):  # type: ignore[no-untyped-def]
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
    )
    request.add_header("Host", host or f"127.0.0.1:{port}")
    if secret is not None:
        request.add_header("X-KMR-Local", secret)
    for k, v in (headers or {}).items():
        request.add_header(k, v)
    try:
        with urllib.request.urlopen(request, timeout=5) as r:  # noqa: S310 - http://127.0.0.1 only
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_only_the_app_with_the_secret_can_call_it(api) -> None:  # type: ignore[no-untyped-def]
    _, port, *_ = api
    assert call(port, "GET", "/windows")[0] == 200
    assert call(port, "GET", "/windows", secret=None)[0] == 403
    assert call(port, "GET", "/windows", secret="wrong" * 8)[0] == 403
    assert call(port, "GET", "/windows", secret=SECRET[:-1])[0] == 403


def test_a_web_page_or_a_rebinding_name_cannot_reach_it(api) -> None:  # type: ignore[no-untyped-def]
    _, port, *_ = api
    assert call(port, "GET", "/windows", headers={"Origin": "https://evil.example"})[0] == 403
    assert call(port, "GET", "/windows", headers={"Origin": "http://localhost:3000"})[0] == 403
    assert call(port, "GET", "/windows", host="evil.example:80")[0] == 403
    assert call(port, "GET", "/windows", headers={"Origin": "null"})[0] == 200  # the app's own window


def test_a_short_secret_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        LocalApi(Agent(scene(), None), "short", config_module.Config())


def test_the_whole_flow_through_the_api(api) -> None:  # type: ignore[no-untyped-def]
    _, port, platform, server, _ = api
    status, win = call(port, "GET", "/windows")
    assert status == 200 and win["hidden"] == 2
    _, prep = call(port, "POST", "/prepare", {"message": "stuck", "language": "auto", "window_ids": ["w1"]})
    assert prep["windows"] == 1 and "jane.doe" not in prep["preview"]
    assert call(port, "POST", "/send", {"prepared_id": prep["prepared_id"], "sha256": "bad"})[0] == 409
    _, reply = call(port, "POST", "/send", {"prepared_id": prep["prepared_id"], "sha256": prep["sha256"]})
    sid = reply["steps"][0]["id"]
    assert call(port, "POST", f"/steps/{sid}/highlight", {})[0] == 200
    assert call(port, "POST", f"/steps/{sid}/approve", {})[0] == 200
    assert platform.actions == [("w1", "w1.n0", "press", None)]
    assert call(port, "POST", f"/steps/{sid}/approve", {})[0] == 409


def test_status_shows_the_connection_honestly(api) -> None:  # type: ignore[no-untyped-def]
    _, port, *_ = api
    s = call(port, "GET", "/status")[1]
    assert s["connected"] is True and s["org"] == "Acme" and s["is_ai"] is False
    assert "kmr_dt_secret_token_value" not in json.dumps(
        s
    )  # has_token says it exists; the value never leaves


def test_bad_input_is_refused(api) -> None:  # type: ignore[no-untyped-def]
    _, port, *_ = api
    assert call(port, "GET", "/nothing")[0] == 404
    assert call(port, "POST", "/prepare", [1, 2])[0] == 400
    assert call(port, "POST", "/steps/x/approve", {})[0] == 404


def test_the_config_is_saved_privately_and_the_token_is_never_returned(api) -> None:  # type: ignore[no-untyped-def]
    local, port, _, _, tmp = api
    status, out = call(
        port,
        "POST",
        "/config",
        {
            "server_url": "http://localhost:8000",
            "token": "kmr_dt_new_token_value",
            "language": "hinglish",
            "extra_apps": ["notes", ""],
        },
    )
    assert (
        status == 200
        and out["has_token"] is True
        and out["language"] == "hinglish"
        and out["extra_apps"] == ["notes"]
    )
    assert "kmr_dt_new_token_value" not in json.dumps(out)
    saved = tmp / "config.json"
    assert json.loads(saved.read_text())["token"] == "kmr_dt_new_token_value"
    if os.name != "nt":
        assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert local.agent.extra_apps == ("notes",)
    assert call(port, "POST", "/config", {"server_url": "http://evil.example.com"})[0] == 400
    assert call(port, "POST", "/config", {"server_url": "ftp://x"})[0] == 400
    assert call(port, "POST", "/config", {"server_url": "https://kmr.example.com"})[0] == 200
