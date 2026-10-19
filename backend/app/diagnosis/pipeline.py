"""Runs one diagnosis: gather evidence, ask a provider, validate, store.

Nothing a provider returns is stored without passing `validate_output`. A failed run is recorded
(with a stable error code, never a provider message) and changes no issues.
"""

import time
import uuid
from typing import Protocol

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.db.audit import record_audit
from app.db.models import DiagnosisRun, Issue
from app.db.tenant import tenant_session
from app.diagnosis.anthropic_provider import ProviderError
from app.diagnosis.evidence import build_input
from app.diagnosis.schemas import DiagnosisInput, ProviderResult
from app.diagnosis.usage import cost_micros
from app.diagnosis.validate import fingerprint, validate_output
from app.scanning.evidence_loader import load_scan_evidence


class DiagnosisProvider(Protocol):
    name: str
    model: str
    config_version: str

    async def diagnose(self, inp: DiagnosisInput) -> ProviderResult: ...


async def run_diagnosis(
    engine: AsyncEngine,
    settings: Settings,
    provider: DiagnosisProvider,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    scan_id: uuid.UUID,
) -> tuple[DiagnosisRun, list[Issue]]:
    """Returns the stored run and issues. Raises ProviderError after recording a failed run."""
    async with tenant_session(engine, user_id=user_id, org_id=org_id) as db:
        evidence = await load_scan_evidence(db, scan_id, org_id)
        project_id = evidence.scan.project_id
        inp = build_input(evidence)

    started = time.perf_counter()
    result: ProviderResult | None = None
    error: str | None = None
    try:
        result = await provider.diagnose(inp)
    except ProviderError as exc:
        error = exc.code
    latency_ms = int((time.perf_counter() - started) * 1000)

    async with tenant_session(engine, user_id=user_id, org_id=org_id) as db:
        run = DiagnosisRun(
            org_id=org_id,
            scan_id=scan_id,
            requested_by=user_id,
            provider=provider.name,
            model=result.model if result else provider.model,
            config_version=provider.config_version,
            status="failed" if result is None else "completed",
            error_code=error,
            input_items=len(inp.evidence),
            latency_ms=latency_ms,
        )
        issues: list[Issue] = []
        if result is not None:
            report = validate_output(result.output, inp)
            run.input_tokens = result.usage.input_tokens
            run.output_tokens = result.usage.output_tokens
            run.cost_micros = cost_micros(
                settings, result.usage.input_tokens, result.usage.output_tokens
            )
            run.issues_count = len(report.issues)
            run.dropped_issues = report.dropped_issues
            run.flags = report.flags
            db.add(run)
            await db.flush()

            produced: list[str] = []
            for draft in report.issues:
                key = fingerprint(draft.category, draft.component, draft.title)
                produced.append(key)
                values = {
                    "org_id": org_id,
                    "project_id": project_id,
                    "scan_id": scan_id,
                    "run_id": run.id,
                    "fingerprint": key,
                    "title": draft.title,
                    "category": draft.category,
                    "severity": draft.severity,
                    "component": draft.component,
                    "summary": draft.summary,
                    "classification": draft.classification,
                    "confidence": draft.confidence,
                    "needs_review_reason": draft.needs_review_reason,
                    "facts": [f.model_dump() for f in draft.facts],
                    "hypotheses": [h.model_dump() for h in draft.hypotheses],
                    "unverified": draft.unverified,
                    "reproduction_steps": draft.reproduction_steps,
                    "impact": draft.impact,
                    "proposed_fix": draft.proposed_fix,
                    "verification_plan": draft.verification_plan,
                    "evidence": _evidence_refs(draft.evidence_ids, inp),
                    "state": "needs_review" if draft.classification == "needs_review" else "open",
                }
                # Same scan, same fingerprint: update the row so anything linked to it survives.
                stmt = pg_insert(Issue).values(id=uuid.uuid4(), **values)
                keep = ("org_id", "project_id", "scan_id", "fingerprint")
                update_cols = {k: v for k, v in values.items() if k not in keep}
                update_cols["updated_at"] = func.now()
                # A verified outcome (fixed / not fixed) is a decision on record: diagnosing the
                # same scan again must not silently reopen it.
                update_cols["state"] = case(
                    (Issue.state.in_(("fixed", "not_fixed")), Issue.state),
                    else_=stmt.excluded.state,
                )
                await db.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["scan_id", "fingerprint"], set_=update_cols
                    )
                )
            # Issues this run no longer finds are marked stale instead of silently kept as open.
            await db.execute(
                update(Issue)
                .where(
                    Issue.scan_id == scan_id,
                    Issue.org_id == org_id,
                    Issue.fingerprint.not_in(produced) if produced else Issue.id.is_not(None),
                    Issue.state.in_(("open", "needs_review")),
                )
                .values(state="stale")
            )
            await db.flush()
            issues = list(
                (
                    await db.scalars(
                        select(Issue)
                        .where(Issue.scan_id == scan_id, Issue.org_id == org_id)
                        .execution_options(populate_existing=True)
                        .order_by(Issue.created_at, Issue.id)
                    )
                ).all()
            )
        else:
            db.add(run)
            await db.flush()
        await record_audit(
            db,
            action="diagnosis.run",
            outcome="success" if result else "failure",
            actor_user_id=user_id,
            org_id=org_id,
            target_type="scan",
            target_id=scan_id,
            details={
                "provider": provider.name,
                "status": run.status,
                "error": error,
                "issues": run.issues_count,
                "flags": run.flags or [],
            },
        )
        await db.refresh(run)

    if error is not None:
        raise ProviderError(error)
    return run, issues


def _evidence_refs(ids: list[str], inp: DiagnosisInput) -> list[dict[str, str]]:
    by_id = {e.id: e for e in inp.evidence}
    return [
        {"id": i, "type": by_id[i].type, "source": by_id[i].source, "kind": by_id[i].kind}
        for i in ids
        if i in by_id
    ]
