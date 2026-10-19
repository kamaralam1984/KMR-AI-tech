"""Sends one finding to GitHub as an issue, only on an explicit, text-bound request.

Outward-facing, so it is off by default (KMR_GITHUB_ISSUES_ENABLED) and needs:
- an admin;
- the SHA-256 of the exact ticket text the user previewed, so what is sent is what was shown;
- `confirm: true`.
A finding is sent at most once. Only the already-redacted ticket leaves KMR AI, as an issue's title
and body; the issue's URL and the text hash are kept in the audit trail.
"""

import hashlib
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.deps import OrgAccess, get_engine, org_access
from app.config import Settings, get_settings
from app.db.audit import record_audit
from app.db.models import Issue, IssueExport
from app.db.tenant import tenant_session
from app.repair.github import GitHubRepo
from app.repair.repo import RepoError
from app.routes.issues import github_status, ticket_for
from app.routes.repairs import client_for, connection_for, repo_http_error, token_of

router = APIRouter(tags=["issues"])


class GithubIssueIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    confirm: Literal[True]


class GithubIssueOut(BaseModel):
    url: str
    number: int
    repository: str


@router.post(
    "/v1/organizations/{org_id}/issues/{issue_id}/github-issue",
    status_code=201,
    response_model=GithubIssueOut,
)
async def create_github_issue(
    issue_id: uuid.UUID,
    body: GithubIssueIn,
    request: Request,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> GithubIssueOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        issue = await db.scalar(
            select(Issue).where(Issue.id == issue_id, Issue.org_id == access.org_id)
        )
        if issue is None:
            raise HTTPException(404, "Issue not found.")
        status = await github_status(db, access.org_id, issue, settings)
        if status.existing_url:
            raise HTTPException(
                409, f"This finding was already sent to GitHub: {status.existing_url}"
            )
        if not status.available:
            code = 403 if not settings.github_issues_enabled else 409
            raise HTTPException(code, status.reason or "Cannot create a GitHub issue.")
        ticket = await ticket_for(db, access.org_id, issue)
        digest = hashlib.sha256(ticket["content"].encode()).hexdigest()
        if digest != body.content_sha256:
            raise HTTPException(
                409, "The ticket text changed since you previewed it. Preview it again."
            )
        conn = await connection_for(db, access.org_id, issue.project_id)
        if conn is None:  # github_status just found it; a race would land here
            raise HTTPException(409, "Connect a GitHub repository to this project first.")
        token = token_of(settings, conn)
        client = client_for(request, settings, conn, token)
        project_id, location = issue.project_id, conn.location

    # GitHub is contacted outside the database transaction.
    if not isinstance(client, GitHubRepo):
        raise HTTPException(409, "Only GitHub repositories can receive issues.")
    try:
        url, number = await client.create_issue(
            title=ticket["title"],
            body=ticket["content"],
        )
    except RepoError as exc:
        async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
            await record_audit(
                db,
                action="issue.github_failed",
                outcome="failure",
                actor_user_id=access.user.user_id,
                org_id=access.org_id,
                target_type="issue",
                target_id=issue_id,
                details={"code": exc.code},
            )
        raise repo_http_error(exc) from exc

    try:
        async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
            db.add(
                IssueExport(
                    org_id=access.org_id,
                    issue_id=issue_id,
                    kind="github_issue",
                    external_url=url,
                    external_number=number,
                    content_sha256=digest,
                    created_by=access.user.user_id,
                )
            )
            await record_audit(
                db,
                action="issue.github_created",
                outcome="success",
                actor_user_id=access.user.user_id,
                org_id=access.org_id,
                target_type="issue",
                target_id=issue_id,
                details={
                    "repository": location,
                    "number": number,
                    "content_sha256": digest,
                    "project_id": str(project_id),
                },
            )
    except IntegrityError:
        # Another request sent it first. The issue exists on GitHub either way.
        raise HTTPException(409, f"This finding was already sent to GitHub: {url}") from None
    return GithubIssueOut(url=url, number=number, repository=location)
