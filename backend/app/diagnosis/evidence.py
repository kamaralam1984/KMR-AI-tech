"""Turns what is stored about a scan into the minimal evidence a provider is given.

Only sanitized, already-trimmed fields go in. Items keep the ids of the stored records they came
from, so every claim a provider makes can be traced back to something real, and anything it cites
that is not in this list is rejected.
"""

from typing import Any

from app.diagnosis.schemas import CorrelationItem, DiagnosisInput, EvidenceItem
from app.scanning.evidence_loader import ScanEvidence
from app.scanning.sanitize import sanitize_text

MAX_ITEMS = 120
_KINDS = (
    "console_error",
    "page_error",
    "failed_request",
    "http_error",
    "a11y_issue",
    "api_check",
    "page",
)


def _trim(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        return sanitize_text(value, 300)
    if isinstance(value, bool | int | float) or value is None:
        return value
    if depth >= 2:
        return None
    if isinstance(value, list):
        return [_trim(v, depth + 1) for v in value[:10]]
    if isinstance(value, dict):
        return {str(k)[:40]: _trim(v, depth + 1) for k, v in list(value.items())[:20]}
    return None


def build_input(evidence: ScanEvidence) -> DiagnosisInput:
    items: list[EvidenceItem] = []
    for o in evidence.observations:
        if o.kind not in _KINDS:
            continue
        items.append(
            EvidenceItem(
                id=str(o.id),
                type="observation",
                source="api_observed" if o.kind == "api_check" else "browser_observed",
                kind=o.kind,
                severity=o.severity,
                page_url=sanitize_text(o.page_url, 300) if o.page_url else None,
                data=_trim(o.data or {}),
            )
        )
    items = items[:MAX_ITEMS]

    cited = {e.ref_id for c in evidence.correlations for e in c.evidence}
    for run, name, path in evidence.runs:
        if str(run.id) in cited:
            items.append(
                EvidenceItem(
                    id=str(run.id),
                    type="api_check_run",
                    source="api_observed",
                    kind="api_check_run",
                    severity="info" if run.status == "passed" else "error",
                    data=_trim(
                        {
                            "name": name,
                            "path": path,
                            "status": run.status,
                            "http_status": run.http_status,
                            "reasons": run.reasons or [],
                        }
                    ),
                )
            )
    for entry in evidence.logs:
        if str(entry.id) in cited:
            items.append(
                EvidenceItem(
                    id=str(entry.id),
                    type="log_entry",
                    source="customer_supplied",
                    kind="log_entry",
                    severity="error" if (entry.http_status or 0) >= 500 else "info",
                    data=_trim(
                        {
                            "request_id": entry.request_id,
                            "http_status": entry.http_status,
                            "level": entry.level,
                            "path": entry.path,
                        }
                    ),
                )
            )
    return DiagnosisInput(
        site_host=evidence.scan.allowed_host,
        telemetry_available=bool(evidence.runs or evidence.logs),
        evidence=items,
        correlations=[
            CorrelationItem(
                observation_id=c.observation_id,
                classification=c.classification,
                needs_review=c.needs_review,
                statement=c.statement,
                evidence_ids=[e.ref_id for e in c.evidence],
            )
            for c in evidence.correlations
        ],
    )
