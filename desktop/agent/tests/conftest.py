import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "desktop" / "agent"))

MARKERS = {
    "@@SLACK_TOKEN@@": "xo" + "xb-123456789012-abcdefghijklmnop",
    "@@GITHUB_PAT@@": "gh" + "p_abcdefghijklmnopqrstuvwxyz0123456789",
}


def expand(text: str) -> str:
    for key, value in MARKERS.items():
        text = text.replace(key, value)
    return text


def labeled(name: str) -> list[dict]:  # type: ignore[type-arg]
    data = json.loads((ROOT / "extension" / "test" / name).read_text())
    return [
        {**c, "text": expand(c["text"]), "must_not_contain": [expand(m) for m in c["must_not_contain"]]}
        for c in data["cases"]
    ]


@pytest.fixture
def root() -> Path:
    return ROOT
