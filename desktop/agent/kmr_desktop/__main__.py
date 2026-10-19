"""`python -m kmr_desktop run` starts the agent for the desktop app; `windows` lists app names."""

import argparse
import json
import os
import secrets
import sys

from kmr_desktop import config as config_module
from kmr_desktop.client import DeskClient
from kmr_desktop.local_api import LocalApi
from kmr_desktop.session import Agent


def make_platform():  # type: ignore[no-untyped-def]
    if sys.platform == "win32":
        from kmr_desktop.platforms.windows_uia import WindowsUia

        return WindowsUia()
    from kmr_desktop.platforms.linux_atspi import LinuxAtspi

    return LinuxAtspi()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # Windows consoles default to a legacy code page
    parser = argparse.ArgumentParser(prog="kmr_desktop")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="start the agent (used by the desktop app)")
    run.add_argument("--port", type=int, default=0)
    sub.add_parser("windows", help="list application names (no titles, no content)")
    args = parser.parse_args(argv)

    platform = make_platform()
    if args.cmd == "windows":
        for app in sorted({w.app for w in platform.list_windows()}):
            print(app)
        return 0

    secret = os.environ.get("KMR_LOCAL_SECRET") or secrets.token_urlsafe(32)
    cfg = config_module.load()
    client = DeskClient(cfg.server_url, cfg.token) if cfg.connected else None
    agent = Agent(platform, client, extra_apps=tuple(cfg.extra_apps))
    server = LocalApi(agent, secret, cfg).serve(args.port)
    # One line for the app that started us. The secret is only ever given to that app.
    print(
        json.dumps(
            {
                "event": "ready",
                "port": server.server_address[1],
                "secret": secret if "KMR_LOCAL_SECRET" not in os.environ else None,
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
