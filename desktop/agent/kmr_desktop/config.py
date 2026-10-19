"""The app's settings: server address, device token, language, extra blocked apps.

Stored in the user's config folder with permissions that only the user can read. The token is a
bearer secret that acts as the user; it is never printed or logged.
"""

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def config_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "kmr-desktop" / "config.json"


@dataclass
class Config:
    server_url: str = ""
    token: str = ""
    language: str = "auto"
    extra_apps: list[str] = field(default_factory=list)

    @property
    def connected(self) -> bool:
        return bool(self.server_url and self.token)

    def public(self) -> dict[str, object]:
        """What may be shown or sent to the UI. Never the token."""
        return {
            "server_url": self.server_url,
            "has_token": bool(self.token),
            "language": self.language,
            "extra_apps": self.extra_apps,
        }


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return Config()
    return Config(
        server_url=str(data.get("server_url", ""))[:300],
        token=str(data.get("token", ""))[:300],
        language=data.get("language") if data.get("language") in ("auto", "en", "hinglish", "hi") else "auto",
        extra_apps=[str(a)[:60] for a in data.get("extra_apps", []) if str(a).strip()][:50],
    )


def save(config: Config, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(asdict(config), handle)
    temporary.replace(path)
    if sys.platform != "win32":
        path.chmod(0o600)
