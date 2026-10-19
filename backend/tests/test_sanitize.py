import pytest

from app.scanning.sanitize import REDACTED, sanitize_text, sanitize_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://user:pw@example.com/a?x=1#frag", "https://example.com/a?x=1"),
        (
            "https://example.com/cb?code=abc123&state=ok",
            "https://example.com/cb?code=%5BREDACTED%5D&state=ok",
        ),
        (
            "https://example.com/?access_token=zzz&page=2",
            "https://example.com/?access_token=%5BREDACTED%5D&page=2",
        ),
        (
            "https://example.com/?ref=ABCDEFGHIJKLMNOPQRSTUVWX12",
            "https://example.com/?ref=%5BREDACTED%5D",
        ),
        ("https://example.com:8080/p", "https://example.com:8080/p"),
        ("data:text/html,<script>alert(1)</script>", "data:"),
        ("blob:https://example.com/uuid", "blob:"),
        ("javascript:alert(document.cookie)", "javascript:"),
    ],
)
def test_urls_lose_credentials_fragments_and_secret_values(raw: str, expected: str) -> None:
    assert sanitize_url(raw) == expected


def test_urls_are_length_capped() -> None:
    assert len(sanitize_url("https://example.com/" + "a" * 5000)) <= 500


@pytest.mark.parametrize(
    ("raw", "must_not_contain"),
    [
        ("Failed with Authorization: Bearer abcdef1234567890abcdef", "abcdef1234567890"),
        ("token=" + "sk_" + "live_abcdefghijklmnopqrstuvwxyz123456", "sk_live"),
        ("password: hunter2hunter2", "hunter2"),
        ("Cookie: session=abc123def456; theme=dark", "abc123def456"),
        (
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
            "SflKxw",
        ),
        ("contact jane.doe@example.com for access", "jane.doe"),
        ("card 4111 1111 1111 1111 declined", "4111"),
        ("GET https://api.example.com/v1/me?api_key=abc123 failed", "abc123"),
        ("id=0123456789abcdef0123456789abcdef0123", "0123456789abcdef"),
        ('api_key="AKIAIOSFODNN7EXAMPLE"', "AKIAIOSFODNN7EXAMPLE"),
    ],
)
def test_text_masks_secrets_and_personal_data(raw: str, must_not_contain: str) -> None:
    cleaned = sanitize_text(raw)
    assert must_not_contain not in cleaned, cleaned


def test_useful_error_text_survives() -> None:
    cleaned = sanitize_text(
        "Uncaught TypeError: Cannot read properties of undefined (reading 'pincode')"
    )
    assert "TypeError" in cleaned and "pincode" in cleaned


def test_text_is_trimmed_and_control_characters_removed() -> None:
    cleaned = sanitize_text("a\x00b\x1b[31m " + "word " * 500)
    assert len(cleaned) <= 500 and "\x00" not in cleaned and "\x1b" not in cleaned


def test_redaction_marker_is_stable() -> None:
    assert REDACTED in sanitize_text("secret=topsecretvalue")
