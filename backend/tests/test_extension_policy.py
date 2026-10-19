"""The server-side checks on extension payloads, and parity with the extension's own lists."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.extension import policy

BLOCKLIST_JS = Path(__file__).resolve().parents[2] / "extension" / "src" / "blocklist.js"


def test_server_and_extension_block_the_same_sites() -> None:
    node = shutil.which("node")
    if node is None or not BLOCKLIST_JS.exists():
        pytest.skip("Unavailable: node or the extension sources are not present")
    out = subprocess.run(  # noqa: S603 - fixed arguments, a repository file
        [node, "-e", f"console.log(JSON.stringify(require({json.dumps(str(BLOCKLIST_JS))})))"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    extension = json.loads(out)
    assert extension["hosts"] == list(policy.BLOCKED_HOSTS)
    assert extension["hostPatterns"] == list(policy.BLOCKED_HOST_PATTERNS)
    assert extension["pathPattern"] == policy.SENSITIVE_PATH


@pytest.mark.parametrize(
    "url",
    [
        "https://shop.example.com/login",
        "https://shop.example.com/Checkout",
        "https://www.paypal.com/x",
        "https://mail.google.com/",
        "https://secure.somebank.com/",
        "https://tax.example.gov.in/",
        "ftp://example.com/",
    ],
)
def test_sensitive_addresses(url: str) -> None:
    assert policy.sensitive_reason(url) is not None


def test_an_ordinary_address_is_fine() -> None:
    assert policy.sensitive_reason("https://shop.example.com/products/shoes") is None


def test_violations_name_the_field_and_never_include_the_value() -> None:
    found = policy.find_violations({"a": [{"message": "mail me: bob@example.com"}]})
    assert found == [("$.a[0].message", "email address")]
    assert all("bob" not in part for pair in found for part in pair)


def test_redacted_text_and_selectors_are_not_violations() -> None:
    clean = {
        "message": "failed for [EMAIL] token=[TOKEN] call [PHONE] id [ID] code [CODE]",
        "selectors": ["body > main > img:nth-of-type(1234567890123)"],
        "url": "https://x.com/api/:id?page=[V]",
        "date": "2024-01-15 10:30:00",
    }
    assert policy.find_violations(clean) == []
