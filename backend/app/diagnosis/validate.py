"""Checks provider output against the evidence that was actually supplied.

A provider can be wrong or manipulated (text on a scanned page may try to steer an AI model), so
nothing it says is stored unless it points at real evidence. Facts and hypotheses without valid
citations are dropped, issues with no valid citation at all are dropped, text is sanitized again,
and anything the evidence cannot support about the backend is forced to "needs review".
"""

import hashlib
import re
from dataclasses import dataclass, field

from app.diagnosis.schemas import (
    DiagnosisInput,
    DiagnosisOutput,
    Fact,
    Hypothesis,
    IssueDraft,
)
from app.scanning.sanitize import sanitize_text

_DIGITS = re.compile(r"\d+")
_SPACE = re.compile(r"\s+")


def fingerprint(category: str, component: str, title: str) -> str:
    """Stable per (category, component, title shape), so a repeat diagnosis updates the same row."""
    shape = _SPACE.sub(" ", _DIGITS.sub("N", title.lower())).strip()
    raw = f"{category}|{component.lower()}|{shape}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


@dataclass
class ValidationReport:
    issues: list[IssueDraft] = field(default_factory=list)
    dropped_issues: int = 0
    flags: list[str] = field(default_factory=list)


def _clean(text: str, limit: int = 600) -> str:
    return sanitize_text(text, limit)


def _keep(ids: list[str], known: set[str]) -> tuple[list[str], int]:
    valid = [i for i in dict.fromkeys(ids) if i in known]
    return valid, len(ids) - len(valid)


def validate_output(output: DiagnosisOutput, inp: DiagnosisInput) -> ValidationReport:
    known = {item.id for item in inp.evidence}
    # Correlations that say the backend was not confirmed (or not covered at all).
    unconfirmed_obs = {
        c.observation_id
        for c in inp.correlations
        if c.classification in ("uncertain", "no_backend_data")
    }
    report = ValidationReport()
    unknown_total = 0
    unsupported_total = 0
    merged: dict[str, IssueDraft] = {}

    for draft in output.issues:
        evidence_ids, unknown = _keep(draft.evidence_ids, known)
        unknown_total += unknown
        facts: list[Fact] = []
        for fact in draft.facts:
            ids, bad = _keep(fact.evidence_ids, known)
            unknown_total += bad
            if ids:
                facts.append(Fact(text=_clean(fact.text), evidence_ids=ids))
            else:
                unsupported_total += 1
        hypotheses: list[Hypothesis] = []
        for hyp in draft.hypotheses:
            ids, bad = _keep(hyp.evidence_ids, known)
            unknown_total += bad
            if ids:
                hypotheses.append(
                    Hypothesis(text=_clean(hyp.text), confidence=hyp.confidence, evidence_ids=ids)
                )
            else:
                unsupported_total += 1
        if not evidence_ids:
            report.dropped_issues += 1
            continue

        classification = draft.classification
        confidence = draft.confidence
        reason = _clean(draft.needs_review_reason, 300) if draft.needs_review_reason else None
        # A backend problem cannot be diagnosed from frontend symptoms alone.
        if draft.category == "backend_api" and unconfirmed_obs & set(evidence_ids):
            classification, confidence = "needs_review", "low"
            reason = reason or (
                "The browser saw a failure, but no backend evidence confirms or explains it."
            )
        if classification == "needs_review" and not reason:
            reason = "The evidence is incomplete or points in more than one direction."

        clean = IssueDraft(
            title=_clean(draft.title, 120),
            category=draft.category,
            severity=draft.severity,
            component=_clean(draft.component, 200) if draft.component else "",
            summary=_clean(draft.summary, 600) if draft.summary else "",
            facts=facts,
            hypotheses=hypotheses,
            unverified=[_clean(u, 300) for u in draft.unverified],
            reproduction_steps=[_clean(s, 300) for s in draft.reproduction_steps],
            impact=_clean(draft.impact, 400) if draft.impact else "",
            proposed_fix=_clean(draft.proposed_fix, 600) if draft.proposed_fix else "",
            verification_plan=_clean(draft.verification_plan, 400)
            if draft.verification_plan
            else "",
            confidence=confidence,
            classification=classification,
            needs_review_reason=reason,
            evidence_ids=evidence_ids,
        )
        key = fingerprint(clean.category, clean.component, clean.title)
        if key in merged:
            merged[key] = _merge(merged[key], clean)
        else:
            merged[key] = clean

    report.issues = list(merged.values())
    if unknown_total:
        report.flags.append("invalid_citations")
    if unsupported_total:
        report.flags.append("unsupported_claims_dropped")
    if report.dropped_issues:
        report.flags.append("issues_without_evidence_dropped")
    return report


_ORDER = ["info", "low", "medium", "high", "critical"]


def _merge(a: IssueDraft, b: IssueDraft) -> IssueDraft:
    """Same cause reported twice: keep one issue, with the evidence of both."""
    severity = max(a.severity, b.severity, key=_ORDER.index)
    ids = list(dict.fromkeys([*a.evidence_ids, *b.evidence_ids]))[:50]
    return a.model_copy(
        update={
            "severity": severity,
            "facts": [*a.facts, *b.facts][:12],
            "hypotheses": [*a.hypotheses, *b.hypotheses][:6],
            "evidence_ids": ids,
            "classification": "needs_review"
            if "needs_review" in (a.classification, b.classification)
            else "diagnosed",
        }
    )
