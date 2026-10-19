"""Decides whether a fix is verified. Pure logic: it takes facts, returns one honest outcome.

`fixed` only when every criterion holds: the after-scan completed, the issue is gone, every page
where the issue was observed was loaded again (absence on a page nobody re-tested proves nothing),
and nothing material appeared that was not there before. Still present -> `not_fixed`. Anything
that cannot be established -> `needs_review`. A passing build or a generated patch is never an
input here.
"""

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

MATERIAL = ("critical", "high", "medium")


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or "/"
    return f"{parts.scheme}://{parts.netloc.lower()}{path}"


@dataclass
class AfterIssue:
    fingerprint: str
    severity: str
    title: str


@dataclass
class Verdict:
    outcome: str  # fixed | not_fixed | needs_review
    reasons: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def judge(
    *,
    issue_fingerprint: str,
    issue_pages: set[str],
    before_fingerprints: set[str],
    after_status: str,
    after_pages: dict[str, int | None],
    after_issues: list[AfterIssue],
    after_diagnosed: bool,
) -> Verdict:
    """`after_pages` maps normalized page URL -> HTTP status of the page in the after-scan."""
    details: dict[str, Any] = {}
    if after_status != "completed":
        return Verdict(
            "needs_review", ["The new scan did not complete, so nothing can be compared."]
        )
    if not after_diagnosed:
        return Verdict("needs_review", ["The new scan has no diagnosis to compare against."])

    present = [i for i in after_issues if i.fingerprint == issue_fingerprint]
    details["still_present"] = bool(present)
    if present:
        return Verdict(
            "not_fixed",
            ["The same issue was found again in the new scan."],
            details,
        )

    wanted = {normalize_url(u) for u in issue_pages}
    if not wanted:
        return Verdict(
            "needs_review",
            ["The issue has no recorded page, so it cannot be re-tested automatically."],
            details,
        )
    missing = sorted(u for u in wanted if u not in after_pages)
    broken = sorted(u for u in wanted if u in after_pages and (after_pages[u] or 0) >= 400)
    details["pages_checked"] = sorted(wanted - set(missing))
    details["pages_not_retested"] = missing
    details["pages_not_loading"] = broken
    if missing:
        return Verdict(
            "needs_review",
            [f"The new scan did not visit {len(missing)} page(s) where the issue was seen."],
            details,
        )
    if broken:
        return Verdict(
            "needs_review",
            [f"{len(broken)} page(s) where the issue was seen no longer load normally."],
            details,
        )

    fresh = [
        i
        for i in after_issues
        if i.fingerprint not in before_fingerprints and i.severity in MATERIAL
    ]
    details["new_issues"] = [{"title": i.title, "severity": i.severity} for i in fresh]
    if fresh:
        return Verdict(
            "needs_review",
            [
                "The issue is gone, but the new scan found "
                f"{len(fresh)} new problem(s) that were not there before. Review them first."
            ],
            details,
        )
    return Verdict(
        "fixed",
        ["Not found again on any page where it was seen, and nothing new appeared."],
        details,
    )
