"""The Python redaction must behave like the JavaScript one the browser extension uses: same labeled
sets, same ceilings. Sets 1 and 2 were used to extend the patterns (regression gates); set 3 was
measured once and is the only unbiased number."""

import pytest
from conftest import labeled

from kmr_desktop.redact import redact_text, redact_url


def leaked(case: dict) -> list[str]:  # type: ignore[type-arg]
    out = redact_text(case["text"], None, 2000)
    return [m for m in case["must_not_contain"] if m in out]


def test_the_dev_set_catches_what_it_covers_and_the_documented_limits_still_leak() -> None:
    cases = labeled("redaction-cases.json")
    covered = [c for c in cases if c["expect"] == "caught"]
    limits = [c for c in cases if c["expect"] == "known_miss"]
    assert [c["text"] for c in covered if leaked(c)] == []
    assert len(limits) == 2 and all(leaked(c) for c in limits)  # never claim zero
    assert [
        c["text"] for c in cases if c["expect"] == "unchanged" and redact_text(c["text"]) != c["text"]
    ] == []


@pytest.mark.parametrize(
    ("file", "ceiling"),
    [("redaction-heldout.json", 0), ("redaction-heldout-2.json", 1), ("redaction-heldout-3.json", 7)],
)
def test_the_held_out_sets_match_the_extensions_results(file: str, ceiling: int) -> None:
    cases = labeled(file)
    missed = [c["text"] for c in cases if c["expect"] == "leak" and leaked(c)]
    changed = [c["text"] for c in cases if c["expect"] == "unchanged" and redact_text(c["text"]) != c["text"]]
    print(f"{file}: {len(missed)} leaks missed, {len(changed)} false positives")
    assert changed == []
    assert len(missed) <= ceiling


def test_counts_say_what_was_masked() -> None:
    counts: dict[str, int] = {}
    redact_text("a@b.com and c@d.org, call 9876543210", counts)
    assert counts["email"] == 2 and counts["phone"] == 1


def test_urls_lose_credentials_fragments_query_values_and_long_ids() -> None:
    out = redact_url("https://user:pw@shop.example.com/orders/1234567890/view?token=abc&email=a@b.com#frag")
    assert out == "https://shop.example.com/orders/:id/view?token=[V]&email=[V]"
    assert redact_url("not a url") == "[URL]" and redact_url("javascript:alert(1)") == "javascript:"


def test_a_huge_text_stays_fast() -> None:
    import time

    start = time.monotonic()
    redact_text("lorem ipsum dolor sit amet " * 4000, None, 10000)
    assert time.monotonic() - start < 2
