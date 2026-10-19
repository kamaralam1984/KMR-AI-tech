import hashlib
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.deps import CurrentUser, OrgAccess, current_user, get_engine, org_access
from app.config import Settings, get_settings
from app.db.audit import record_audit
from app.db.models import (
    DiagnosisRun,
    Issue,
    IssueExport,
    Observation,
    Project,
    RepositoryConnection,
    Scan,
)
from app.db.tenant import tenant_session
from app.diagnosis.anthropic_provider import ProviderError
from app.diagnosis.export import build_ticket
from app.diagnosis.impact import Impact, rank_issue
from app.diagnosis.pipeline import DiagnosisProvider, run_diagnosis
from app.diagnosis.usage import month_runs, month_spend_micros, runs_last_day
from app.routes.traffic import load_traffic
from app.schemas import (
    AiStatusOut,
    AiUsageOut,
    DiagnoseOut,
    DiagnosisRunOut,
    FactOut,
    GithubInfo,
    HypothesisOut,
    ImpactOut,
    IssueEvidenceOut,
    IssueOut,
    IssueSummaryOut,
    RankedIssueOut,
    TicketOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["issues"])

_PROVIDER_ERRORS = {
    "no_api_key": (503, "The AI provider is not configured on this server."),
    "provider_unavailable": (
        502,
        "The diagnosis service is temporarily unavailable. Try again later.",
    ),
    "provider_timeout": (502, "The diagnosis service took too long to answer. Try again later."),
    "provider_rejected": (502, "The diagnosis service rejected the request."),
    "invalid_output": (
        502,
        "The diagnosis service returned an answer that could not be used, so nothing was saved.",
    ),
}


def get_provider(request: Request) -> DiagnosisProvider:
    provider: DiagnosisProvider | None = getattr(request.app.state, "diagnosis_provider", None)
    if provider is None:
        raise HTTPException(503, "Diagnosis is not available.")
    return provider


def _label(provider: str, model: str) -> str:
    return "Built-in rules (not AI)" if provider == "rules" else f"AI model {model}"


def _summary(issue: Issue, run: DiagnosisRun) -> IssueSummaryOut:
    return IssueSummaryOut(
        id=issue.id,
        project_id=issue.project_id,
        scan_id=issue.scan_id,
        title=issue.title,
        category=issue.category,
        severity=issue.severity,
        component=issue.component,
        state=issue.state,
        classification=issue.classification,
        confidence=issue.confidence,
        provider=run.provider,
        is_ai=run.provider != "rules",
        created_at=issue.created_at,
        updated_at=issue.updated_at,
    )


def _full(issue: Issue, run: DiagnosisRun) -> IssueOut:
    return IssueOut(
        **_summary(issue, run).model_dump(),
        summary=issue.summary,
        needs_review_reason=issue.needs_review_reason,
        facts=[FactOut(**f) for f in issue.facts],
        hypotheses=[HypothesisOut(**h) for h in issue.hypotheses],
        unverified=list(issue.unverified),
        reproduction_steps=list(issue.reproduction_steps),
        impact=issue.impact,
        proposed_fix=issue.proposed_fix,
        verification_plan=issue.verification_plan,
        evidence=[IssueEvidenceOut(**e) for e in issue.evidence],
        model=run.model,
    )


def _run_out(run: DiagnosisRun) -> DiagnosisRunOut:
    return DiagnosisRunOut(
        id=run.id,
        scan_id=run.scan_id,
        provider=run.provider,
        model=run.model,
        is_ai=run.provider != "rules",
        status=run.status,
        error_code=run.error_code,
        issues_count=run.issues_count,
        dropped_issues=run.dropped_issues,
        flags=list(run.flags or []),
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        cost_micros=run.cost_micros,
        latency_ms=run.latency_ms,
        created_at=run.created_at,
    )


@router.get("/v1/ai/status", response_model=AiStatusOut)
async def ai_status(
    _user: CurrentUser = Depends(current_user),  # noqa: B008
    provider: DiagnosisProvider = Depends(get_provider),  # noqa: B008
) -> AiStatusOut:
    return AiStatusOut(
        provider=provider.name,
        model=provider.model,
        is_ai=provider.name != "rules",
        label=_label(provider.name, provider.model),
    )


@router.post("/v1/organizations/{org_id}/scans/{scan_id}/diagnose", response_model=DiagnoseOut)
async def diagnose_scan(
    scan_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    provider: DiagnosisProvider = Depends(get_provider),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> DiagnoseOut:
    over_ceiling = False
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        scan = await db.scalar(select(Scan).where(Scan.id == scan_id, Scan.org_id == access.org_id))
        if scan is None:
            raise HTTPException(404, "Scan not found.")
        if scan.status != "completed":
            raise HTTPException(409, "Only a completed scan can be diagnosed.")
        if await runs_last_day(db, access.org_id) >= settings.ai_daily_runs_per_org:
            raise HTTPException(429, "Your organization reached its daily diagnosis limit.")
        ceiling = settings.ai_monthly_cost_ceiling_usd
        if ceiling > 0 and await month_spend_micros(db, access.org_id) >= ceiling * 1_000_000:
            over_ceiling = True
            # Recorded here, and the error raised after this block: raising inside it would roll
            # the audit row back.
            await record_audit(
                db,
                action="ai.cost_ceiling_reached",
                outcome="denied",
                actor_user_id=access.user.user_id,
                org_id=access.org_id,
                details={"ceiling_usd": ceiling},
            )
    if over_ceiling:
        logger.warning("ai_cost_ceiling_reached", extra={"org_id": str(access.org_id)})
        raise HTTPException(429, "Your organization reached its monthly AI spending limit.")

    try:
        run, issues = await run_diagnosis(
            engine,
            settings,
            provider,
            org_id=access.org_id,
            user_id=access.user.user_id,
            scan_id=scan_id,
        )
    except ProviderError as exc:
        status, message = _PROVIDER_ERRORS.get(exc.code, (502, "Diagnosis failed."))
        raise HTTPException(status, message) from exc
    return DiagnoseOut(run=_run_out(run), issues=[_summary(i, run) for i in issues])


async def _runs_by_id(db, ids: set[uuid.UUID]) -> dict[uuid.UUID, DiagnosisRun]:  # type: ignore[no-untyped-def]
    if not ids:
        return {}
    rows = await db.scalars(select(DiagnosisRun).where(DiagnosisRun.id.in_(ids)))
    return {r.id: r for r in rows}


@router.get(
    "/v1/organizations/{org_id}/scans/{scan_id}/issues", response_model=list[IssueSummaryOut]
)
async def scan_issues(
    scan_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[IssueSummaryOut]:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        scan = await db.scalar(select(Scan).where(Scan.id == scan_id, Scan.org_id == access.org_id))
        if scan is None:
            raise HTTPException(404, "Scan not found.")
        issues = (
            await db.scalars(
                select(Issue)
                .where(Issue.scan_id == scan_id, Issue.org_id == access.org_id)
                .order_by(Issue.created_at, Issue.id)
            )
        ).all()
        runs = await _runs_by_id(db, {i.run_id for i in issues})
        return [_summary(i, runs[i.run_id]) for i in issues]


@router.get("/v1/organizations/{org_id}/issues", response_model=list[IssueSummaryOut])
async def list_issues(
    severity: Annotated[str | None, Query(max_length=10)] = None,
    state: Annotated[str | None, Query(max_length=20)] = None,
    project_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[IssueSummaryOut]:
    filters = [Issue.org_id == access.org_id]
    if severity:
        filters.append(Issue.severity == severity)
    if state:
        filters.append(Issue.state == state)
    if project_id:
        filters.append(Issue.project_id == project_id)
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        issues = (
            await db.scalars(
                select(Issue)
                .where(*filters)
                .order_by(Issue.created_at.desc(), Issue.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        runs = await _runs_by_id(db, {i.run_id for i in issues})
        return [_summary(i, runs[i.run_id]) for i in issues]


def _impact_out(impact: Impact) -> ImpactOut:
    return ImpactOut(
        basis=impact.basis,
        traffic=impact.traffic,
        score=impact.score,
        journey=impact.journey_key,
        journey_label=impact.journey_label,
        page=impact.page,
        reasons=impact.reasons,
    )


async def _page_urls(db, org_id: uuid.UUID, issues: list[Issue]) -> dict[uuid.UUID, list[str]]:  # type: ignore[no-untyped-def]
    """The pages each issue's cited observations were seen on (stored, tenant-scoped records)."""
    wanted: dict[uuid.UUID, list[uuid.UUID]] = {}
    for issue in issues:
        ids = []
        for ev in issue.evidence:
            if ev.get("type") == "observation":
                try:
                    ids.append(uuid.UUID(str(ev.get("id"))))
                except ValueError:
                    continue
        wanted[issue.id] = ids
    all_ids = {i for ids in wanted.values() for i in ids}
    urls: dict[uuid.UUID, str] = {}
    if all_ids:
        rows = await db.execute(
            select(Observation.id, Observation.page_url).where(
                Observation.id.in_(all_ids), Observation.org_id == org_id
            )
        )
        urls = {oid: url for oid, url in rows if url}
    return {iid: [urls[o] for o in ids if o in urls] for iid, ids in wanted.items()}


@router.get("/v1/organizations/{org_id}/issues/ranked", response_model=list[RankedIssueOut])
async def ranked_issues(
    project_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[RankedIssueOut]:
    """Open issues ordered by a labeled heuristic of business impact (never a measurement)."""
    filters = [Issue.org_id == access.org_id, Issue.state == "open"]
    if project_id:
        filters.append(Issue.project_id == project_id)
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        issues = list(
            (
                await db.scalars(
                    select(Issue).where(*filters).order_by(Issue.created_at.desc()).limit(500)
                )
            ).all()
        )
        runs = await _runs_by_id(db, {i.run_id for i in issues})
        pages = await _page_urls(db, access.org_id, issues)
        traffic = await load_traffic(db, access.org_id, {i.project_id for i in issues})
        ranked = [
            (i, rank_issue(i.severity, pages.get(i.id, []), traffic.get(i.project_id)))
            for i in issues
        ]
    ranked.sort(key=lambda pair: (-pair[1].score, pair[0].created_at.timestamp() * -1))
    return [
        RankedIssueOut(issue=_summary(i, runs[i.run_id]), impact=_impact_out(imp))
        for i, imp in ranked[:limit]
    ]


async def ticket_for(db, org_id: uuid.UUID, issue: Issue) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """The redacted ticket for a stored issue. One builder: a preview and a send match exactly."""
    pages = await _page_urls(db, org_id, [issue])
    project = await db.scalar(
        select(Project).where(Project.id == issue.project_id, Project.org_id == org_id)
    )
    traffic = await load_traffic(db, org_id, {issue.project_id})
    return build_ticket(
        issue,
        rank_issue(issue.severity, pages.get(issue.id, []), traffic.get(issue.project_id)),
        site_host=project.site_host if project else None,
    )


@router.get("/v1/organizations/{org_id}/issues/{issue_id}/ticket", response_model=TicketOut)
async def issue_ticket(
    issue_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> TicketOut:
    """A redacted, developer-ready ticket for one finding. Nothing leaves KMR AI here."""
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        issue = await db.scalar(
            select(Issue).where(Issue.id == issue_id, Issue.org_id == access.org_id)
        )
        if issue is None:
            raise HTTPException(404, "Issue not found.")
        ticket = await ticket_for(db, access.org_id, issue)
        github = await github_status(db, access.org_id, issue, settings)
        await record_audit(
            db,
            action="issue.ticket_preview",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="issue",
            target_id=issue.id,
            details={"format": "markdown"},
        )
    return TicketOut(
        **ticket,
        content_sha256=hashlib.sha256(ticket["content"].encode()).hexdigest(),
        github=github,
        notice="Review this text before you share it. Secrets and personal data are masked, but "
        "masking is pattern-based and can miss things. KMR AI sends nothing anywhere.",
    )


async def github_status(db, org_id: uuid.UUID, issue: Issue, settings: Settings) -> GithubInfo:  # type: ignore[no-untyped-def]
    """Whether this finding can be sent to GitHub as an issue, and why not if it cannot."""
    existing = await db.scalar(
        select(IssueExport).where(
            IssueExport.issue_id == issue.id, IssueExport.kind == "github_issue"
        )
    )
    if existing is not None:
        return GithubInfo(
            available=False, reason="Already sent to GitHub.", existing_url=existing.external_url
        )
    if not settings.github_issues_enabled:
        return GithubInfo(
            available=False,
            reason="Creating GitHub issues is turned off on this server. An administrator can "
            "enable it (KMR_GITHUB_ISSUES_ENABLED) after giving the GitHub App the Issues "
            "write permission.",
        )
    conn = await db.scalar(
        select(RepositoryConnection).where(
            RepositoryConnection.project_id == issue.project_id,
            RepositoryConnection.org_id == org_id,
        )
    )
    if conn is None or conn.provider != "github":
        return GithubInfo(
            available=False, reason="Connect a GitHub repository to this project first."
        )
    return GithubInfo(available=True, repository=conn.location)


@router.get("/v1/organizations/{org_id}/issues/{issue_id}", response_model=IssueOut)
async def get_issue(
    issue_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> IssueOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        issue = await db.scalar(
            select(Issue).where(Issue.id == issue_id, Issue.org_id == access.org_id)
        )
        if issue is None:
            raise HTTPException(404, "Issue not found.")
        run = await db.scalar(select(DiagnosisRun).where(DiagnosisRun.id == issue.run_id))
        if run is None:
            raise HTTPException(404, "Issue not found.")
        return _full(issue, run)


@router.get("/v1/organizations/{org_id}/ai-usage", response_model=AiUsageOut)
async def ai_usage(
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    provider: DiagnosisProvider = Depends(get_provider),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> AiUsageOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        day = await runs_last_day(db, access.org_id)
        runs = await month_runs(db, access.org_id)
        spend = await month_spend_micros(db, access.org_id)
    priced = bool(settings.ai_input_price_per_mtok or settings.ai_output_price_per_mtok)
    ceiling = settings.ai_monthly_cost_ceiling_usd
    return AiUsageOut(
        provider=provider.name,
        model=provider.model,
        is_ai=provider.name != "rules",
        label=_label(provider.name, provider.model),
        daily_runs_used=day,
        daily_runs_limit=settings.ai_daily_runs_per_org,
        month_runs=runs,
        month_cost_usd=round(spend / 1_000_000, 4) if priced else None,
        monthly_cost_ceiling_usd=ceiling if ceiling > 0 else None,
    )
