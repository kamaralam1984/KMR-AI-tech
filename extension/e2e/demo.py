"""Sets up everything needed to try the extension by hand in your own Chrome, then serves a test page.

Run:  make ext-demo   (needs `make up`, `make db-init` and the API on :8000)

It creates a throwaway account (e2e-owner-*@example.com, removed by `make clean-test-data`), an
organization, a project whose website is http://localhost:PORT/ (marked verified directly in the
local database, because a real DNS or file check cannot prove a localhost page), and an extension
token. It then serves a test page that has an image without alt text, an unlabeled form and fake
personal data, plus /login and /pw pages the agent must refuse. Press Ctrl-C to stop.
"""

import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_e2e import API, EXT, Handler, api_session  # noqa: E402

from app.config import get_settings  # noqa: E402

PORT = 8765
HOST = "localhost"


def main() -> int:
    settings = get_settings()
    if settings.app_env != "local" or settings.migration_database_url is None:
        print("Refusing to run: needs KMR_APP_ENV=local and KMR_MIGRATION_DATABASE_URL.")
        return 2
    try:
        httpx.get(f"{API}/ready", timeout=5).raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"The API is not reachable on {API} ({exc}). Start it with `make dev-api`.")
        return 2
    dsn = settings.migration_database_url.get_secret_value().replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    _client, _org, project, token = api_session()
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "UPDATE projects SET site_url = %s, site_host = %s, verified_at = now(), "
            "verified_host = %s, verified_method = 'file' WHERE id = %s",
            (f"http://{HOST}:{PORT}/", HOST, HOST, project),
        )
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as exc:
        print(f"Port {PORT} is busy ({exc}). Stop the other demo first.")
        return 2
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(
        f"""
Ready. Keep this running while you try the extension.

1. Chrome: open chrome://extensions, turn on Developer mode, click "Load unpacked" and choose:
     {EXT}
2. Click the extension's Settings (or right-click the icon, Options) and enter:
     KMR AI server address (API):  http://localhost:8000
     KMR AI web address:           http://localhost:3000
     Extension token:              {token}
   Click "Load my projects" and allow the permission Chrome asks for, pick the project
   "Fixture", then Save.
3. Open  http://localhost:{PORT}/  in a tab. Click the extension icon, "Turn on for this tab",
   then "Analyze this page". Check "what will be sent" before pressing Send.
4. Also try  http://localhost:{PORT}/login  and  http://localhost:{PORT}/pw : the agent must
   say it is not reading them.
Project id: {project}
""",
        flush=True,
    )
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
