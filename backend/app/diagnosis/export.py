"""Turns a stored finding into a developer-ready ticket (Markdown).

Everything is built from the stored finding only, and every piece of free text goes through the
same redaction as scan evidence, so a ticket never carries more than KMR AI already shows. Facts,
hypotheses and unverified statements keep their labels. Screenshots are referenced by evidence ID
and never embedded. Nothing is sent anywhere: the caller shows this text to the user first.
"""

import re
from typing import Any

from app.diagnosis.impact import Impact
from app.scanning.sanitize import sanitize_text

MAX_FIELD = 1200


def _clean(text: str | None, limit: int = MAX_FIELD) -> str:
    return sanitize_text(text or "", limit).replace("\n", " ").strip()


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:50] or "finding"


def build_ticket(issue: Any, impact: Impact, *, site_host: str | None = None) -> dict[str, str]:
    """`issue` is a stored Issue row. Returns {title, filename, content}."""
    title = _clean(issue.title, 200)
    lines = [f"# {title}", ""]
    status = "Needs review" if issue.classification == "needs_review" else "Diagnosed"
    meta = [
        f"- **Severity:** {issue.severity}",
        f"- **Category:** {_clean(issue.category, 60)}",
        f"- **Confidence:** {issue.confidence}",
        f"- **Status:** {status}",
    ]
    if issue.component:
        meta.append(f"- **Component:** {_clean(issue.component, 200)}")
    if site_host:
        meta.append(f"- **Site:** {_clean(site_host, 200)}")
    if impact.page:
        meta.append(f"- **Page:** {_clean(impact.page, 300)}")
    lines += [*meta, ""]

    if issue.summary:
        lines += ["## Summary", _clean(issue.summary), ""]
    if issue.needs_review_reason:
        lines += ["## Why this needs review", _clean(issue.needs_review_reason), ""]

    if issue.reproduction_steps:
        lines += ["## Steps to reproduce"]
        lines += [f"{n}. {_clean(step, 400)}" for n, step in enumerate(issue.reproduction_steps, 1)]
        lines.append("")

    if issue.facts:
        lines += ["## Observed (facts from stored evidence)"]
        for fact in issue.facts:
            refs = ", ".join(fact.get("evidence_ids", [])[:6])
            lines.append(
                f"- {_clean(fact.get('text'))}" + (f" _(evidence: {refs})_" if refs else "")
            )
        lines.append("")
    if issue.hypotheses:
        lines += ["## Inferred (hypotheses, not proven)"]
        for hyp in issue.hypotheses:
            lines.append(f"- [{hyp.get('confidence', 'low')} confidence] {_clean(hyp.get('text'))}")
        lines.append("")
    if issue.unverified:
        lines += ["## Unverified"]
        lines += [f"- {_clean(item)}" for item in issue.unverified]
        lines.append("")

    if issue.impact:
        lines += ["## Impact", _clean(issue.impact), ""]
    lines += [
        "## Business-impact ranking (estimate, not measured)",
        f"- Journey guessed from the page address: {impact.journey_label}",
        f"- Score: {impact.score}",
    ]
    if impact.traffic:
        lines.append(
            f"- Customer-supplied traffic ({impact.traffic['source_label']}, "
            f"{impact.traffic['period']}): {impact.traffic['views']:,} views, "
            f"{impact.traffic['share_percent']}% of the imported total. KMR AI did not measure "
            "or verify this."
        )
    else:
        lines.append("- Based only on severity and the kind of page; no traffic data was used.")
    lines += ["- No revenue or user-loss numbers are available or implied.", ""]
    if issue.proposed_fix:
        lines += ["## Suggested fix (a proposal, not applied)", _clean(issue.proposed_fix), ""]
    if issue.verification_plan:
        lines += ["## How to verify", _clean(issue.verification_plan), ""]
    if issue.evidence:
        lines += ["## Evidence references (stored in KMR AI; screenshots are not embedded)"]
        for ev in issue.evidence[:30]:
            lines.append(f"- `{ev.get('id')}` {ev.get('source')} / {ev.get('kind')}")
        lines.append("")
    lines += [
        "---",
        f"_Exported from KMR AI. Finding ID: {issue.id}. Personal data and secrets are masked._",
    ]
    return {"title": title, "filename": f"{_slug(issue.title)}.md", "content": "\n".join(lines)}
