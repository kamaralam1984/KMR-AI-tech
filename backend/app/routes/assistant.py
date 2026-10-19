"""Owner-side management of the visitor assistant: installs, knowledge base, forms, hand-offs."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.assistant.schemas import (
    ConsentCreatedOut,
    ConsentIn,
    ConsentOut,
    FormIn,
    FormOut,
    HandoffOut,
    HandoffPatch,
    IndexIn,
    IndexResultOut,
    InstallCreatedOut,
    InstallIn,
    InstallOut,
    InstallPatch,
    PageIn,
    PageOut,
)
from app.assistant.service import PageLimit, consent_token, html_to_text, new_key, store_page
from app.auth.deps import (
    OrgAccess,
    allow,
    get_engine,
    get_rate_limiter,
    org_access,
    secret_key,
)
from app.config import Settings, get_settings
from app.db.audit import record_audit
from app.db.models import (
    FormDefinitionRow,
    Handoff,
    KbPage,
    LearningConsent,
    Project,
    WidgetInstall,
)
from app.db.tenant import tenant_session
from app.scanning.api_checks import ApiTransport, InvalidCheck, validate_path
from app.scanning.net_policy import TargetRejected, validate_url
from app.scanning.site import net_policy
from app.security.rate_limit import RateLimiter

router = APIRouter(prefix="/v1/organizations/{org_id}/assistant", tags=["assistant"])
MAX_INSTALLS = 20
MAX_FORMS_PER_INSTALL = 20
CHILD_SAFEGUARDS_DOC = "docs/CHILD-LEARNING-SAFEGUARDS.md"


def get_transport(request: Request) -> ApiTransport:
    transport = getattr(request.app.state, "api_transport", None)
    if transport is None:
        raise HTTPException(503, "Fetching pages is not available.")
    return transport  # type: ignore[no-any-return]


def _snippet(settings: Settings) -> str:
    base = settings.public_base_url.rstrip("/")
    return (
        f'<script src="{base}/widget/kmr-widget.js" data-api="{base}/widget-api" '
        'data-install-key="PASTE_YOUR_INSTALL_KEY" defer></script>'
    )


def _out(w: WidgetInstall, settings: Settings) -> InstallOut:
    return InstallOut(
        id=w.id,
        name=w.name,
        project_id=w.project_id,
        key_prefix=w.key_prefix,
        allowed_origins=list(w.allowed_origins),
        modules=list(w.modules),
        enabled=w.enabled and w.revoked_at is None,
        revoked=w.revoked_at is not None,
        consent_text=w.consent_text,
        handoff_retention_days=w.handoff_retention_days,
        child_safeguards_acknowledged=w.learning_ack_at is not None,
        learning_consent_required=w.learning_consent_required,
        created_at=w.created_at,
        snippet=_snippet(settings),
    )


async def _install(db, org_id: uuid.UUID, install_id: uuid.UUID) -> WidgetInstall:  # type: ignore[no-untyped-def]
    install = await db.scalar(
        select(WidgetInstall).where(WidgetInstall.id == install_id, WidgetInstall.org_id == org_id)
    )
    if install is None:
        raise HTTPException(404, "Assistant install not found.")
    return install  # type: ignore[no-any-return]


def _check_learning(modules: Sequence[str], acknowledged: bool) -> None:
    if "learning" in modules and not acknowledged:
        raise HTTPException(
            422,
            "The learning module needs you to acknowledge the child-data safeguards "
            f"({CHILD_SAFEGUARDS_DOC}) first. A legal review of child-data handling is still "
            "required before real learners use it.",
        )


# ----------------------------------------------------------------------------- installs


@router.get("/installs", response_model=list[InstallOut])
async def list_installs(
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> list[InstallOut]:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        rows = await db.scalars(
            select(WidgetInstall)
            .where(WidgetInstall.org_id == access.org_id)
            .order_by(WidgetInstall.created_at.desc())
        )
        return [_out(w, settings) for w in rows]


@router.post("/installs", status_code=201, response_model=InstallCreatedOut)
async def create_install(
    body: InstallIn,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> InstallCreatedOut:
    modules = list(dict.fromkeys(body.modules))
    _check_learning(modules, body.acknowledge_child_safeguards)
    raw, digest, prefix = new_key()
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        existing = await db.scalars(
            select(WidgetInstall.id).where(WidgetInstall.org_id == access.org_id)
        )
        if len(existing.all()) >= MAX_INSTALLS:
            raise HTTPException(409, f"An organization can have at most {MAX_INSTALLS} installs.")
        if body.project_id is not None:
            project = await db.scalar(
                select(Project.id).where(
                    Project.id == body.project_id, Project.org_id == access.org_id
                )
            )
            if project is None:
                raise HTTPException(404, "Project not found.")
        install = WidgetInstall(
            org_id=access.org_id,
            project_id=body.project_id,
            name=body.name,
            key_hash=digest,
            key_prefix=prefix,
            allowed_origins=body.allowed_origins,
            modules=modules,
            consent_text=body.consent_text,
            handoff_retention_days=body.handoff_retention_days,
            learning_ack_at=datetime.now(UTC) if "learning" in modules else None,
            learning_ack_by=access.user.user_id if "learning" in modules else None,
            learning_consent_required=body.learning_consent_required,
            created_by=access.user.user_id,
        )
        db.add(install)
        await db.flush()
        await record_audit(
            db,
            action="assistant.install_create",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="widget_install",
            target_id=install.id,
            details={"modules": modules, "origins": len(body.allowed_origins)},
        )
        return InstallCreatedOut(**_out(install, settings).model_dump(), install_key=raw)


@router.patch("/installs/{install_id}", response_model=InstallOut)
async def update_install(
    install_id: uuid.UUID,
    body: InstallPatch,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> InstallOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        install = await _install(db, access.org_id, install_id)
        if install.revoked_at is not None:
            raise HTTPException(409, "This install was revoked. Create a new one.")
        changes: dict[str, Any] = {}
        if body.modules is not None:
            modules = list(dict.fromkeys(body.modules))
            acknowledged = install.learning_ack_at is not None or bool(
                body.acknowledge_child_safeguards
            )
            _check_learning(modules, acknowledged)
            changes["modules"] = modules
            if "learning" in modules and install.learning_ack_at is None:
                changes["learning_ack_at"] = datetime.now(UTC)
                changes["learning_ack_by"] = access.user.user_id
        for field in (
            "name",
            "allowed_origins",
            "enabled",
            "consent_text",
            "handoff_retention_days",
            "learning_consent_required",
        ):
            value = getattr(body, field)
            if value is not None:
                changes[field] = value
        if changes:
            changes["updated_at"] = datetime.now(UTC)
            await db.execute(
                update(WidgetInstall).where(WidgetInstall.id == install_id).values(**changes)
            )
            await db.refresh(install)
        await record_audit(
            db,
            action="assistant.install_update",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="widget_install",
            target_id=install_id,
            details={"fields": sorted(k for k in changes if k != "updated_at")},
        )
        return _out(install, settings)


@router.post("/installs/{install_id}/rotate-key", response_model=InstallCreatedOut)
async def rotate_key(
    install_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> InstallCreatedOut:
    raw, digest, prefix = new_key()
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        install = await _install(db, access.org_id, install_id)
        if install.revoked_at is not None:
            raise HTTPException(409, "This install was revoked. Create a new one.")
        await db.execute(
            update(WidgetInstall)
            .where(WidgetInstall.id == install_id)
            .values(key_hash=digest, key_prefix=prefix, updated_at=datetime.now(UTC))
        )
        await db.refresh(install)
        await record_audit(
            db,
            action="assistant.key_rotate",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="widget_install",
            target_id=install_id,
        )
        return InstallCreatedOut(**_out(install, settings).model_dump(), install_key=raw)


@router.post("/installs/{install_id}/revoke", response_model=InstallOut)
async def revoke_install(
    install_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> InstallOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        install = await _install(db, access.org_id, install_id)
        await db.execute(
            update(WidgetInstall)
            .where(WidgetInstall.id == install_id)
            .values(revoked_at=datetime.now(UTC), enabled=False, updated_at=datetime.now(UTC))
        )
        await db.refresh(install)
        await record_audit(
            db,
            action="assistant.install_revoke",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="widget_install",
            target_id=install_id,
        )
        return _out(install, settings)


# ----------------------------------------------------------------------------- knowledge base


def _page_out(p: KbPage) -> PageOut:
    return PageOut(
        id=p.id,
        kind=p.kind,
        title=p.title,
        url=p.url,
        source=p.source,
        chunk_count=p.chunk_count,
        blocked_chunks=p.blocked_chunks,
        updated_at=p.updated_at,
    )


@router.get("/installs/{install_id}/pages", response_model=list[PageOut])
async def list_pages(
    install_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[PageOut]:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await _install(db, access.org_id, install_id)
        rows = await db.scalars(
            select(KbPage)
            .where(KbPage.install_id == install_id, KbPage.org_id == access.org_id)
            .order_by(KbPage.kind, KbPage.title)
        )
        return [_page_out(p) for p in rows]


@router.post("/installs/{install_id}/pages", status_code=201, response_model=PageOut)
async def add_page(
    install_id: uuid.UUID,
    body: PageIn,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> PageOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        install = await _install(db, access.org_id, install_id)
        if body.kind == "learning" and "learning" not in install.modules:
            raise HTTPException(409, "Turn on the learning module for this install first.")
        try:
            page = await store_page(
                db,
                org_id=access.org_id,
                install_id=install_id,
                user_id=access.user.user_id,
                kind=body.kind,
                title=body.title.strip(),
                url=body.url,
                content=body.content,
                source="manual",
            )
        except PageLimit:
            raise HTTPException(409, "This install has reached its page limit.") from None
        await record_audit(
            db,
            action="assistant.page_add",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="kb_page",
            target_id=page.id,
            details={"kind": body.kind, "blocked_chunks": page.blocked_chunks},
        )
        return _page_out(page)


@router.delete("/installs/{install_id}/pages/{page_id}", status_code=204)
async def delete_page(
    install_id: uuid.UUID,
    page_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> None:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        result = await db.execute(
            delete(KbPage).where(
                KbPage.id == page_id,
                KbPage.install_id == install_id,
                KbPage.org_id == access.org_id,
            )
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            raise HTTPException(404, "Page not found.")
        await record_audit(
            db,
            action="assistant.page_delete",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="kb_page",
            target_id=page_id,
        )


@router.post("/installs/{install_id}/index", response_model=list[IndexResultOut])
async def index_site_pages(
    install_id: uuid.UUID,
    body: IndexIn,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    limiter: RateLimiter = Depends(get_rate_limiter),  # noqa: B008
    transport: ApiTransport = Depends(get_transport),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> list[IndexResultOut]:
    """Fetches public pages of the project's ownership-verified site, through the egress proxy."""
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        install = await _install(db, access.org_id, install_id)
        if install.project_id is None:
            raise HTTPException(409, "Link this install to a project to index its website.")
        project = await db.scalar(
            select(Project).where(Project.id == install.project_id, Project.org_id == access.org_id)
        )
        if project is None or not project.site_url or not project.site_host:
            raise HTTPException(409, "The linked project has no website address.")
        if not project.is_verified:
            raise HTTPException(409, "Verify that you own the website before indexing it.")
        site_url, host = project.site_url, project.site_host
    if not await allow(
        limiter, f"assistant-index:{access.org_id}", settings.assistant_index_per_hour, 3600
    ):
        raise HTTPException(429, "Too many indexing runs. Try again later.")

    policy = net_policy(settings)
    try:
        origin = validate_url(site_url, policy).origin
    except TargetRejected as exc:
        raise HTTPException(409, "The website address is no longer allowed.") from exc

    results: list[IndexResultOut] = []
    for path in dict.fromkeys(body.paths):
        try:
            clean = validate_path(path)
            target = validate_url(f"{origin}{clean}", policy)
        except (InvalidCheck, TargetRejected):
            results.append(IndexResultOut(path=path, status="rejected", detail="invalid_path"))
            continue
        if target.host != host:
            results.append(IndexResultOut(path=path, status="rejected", detail="other_host"))
            continue
        response = await transport.request(
            "GET",
            target.url,
            {"User-Agent": "KMRAI-Indexer/1.0", "Accept": "text/html,text/plain;q=0.8"},
            settings.api_check_timeout_seconds,
        )
        if response.failure or response.headers.get("x-kmr-blocked"):
            results.append(
                IndexResultOut(
                    path=path, status="unavailable", detail=response.failure or "blocked"
                )
            )
            continue
        if response.status != 200:
            results.append(
                IndexResultOut(path=path, status="unavailable", detail=f"http_{response.status}")
            )
            continue
        ctype = response.headers.get("content-type", "")
        if "html" not in ctype and "text/plain" not in ctype:
            results.append(IndexResultOut(path=path, status="rejected", detail="not_text"))
            continue
        text_body = response.body.decode("utf-8", errors="replace")
        title, content = html_to_text(text_body) if "html" in ctype else (clean, text_body)
        if len(content) < 20:
            results.append(IndexResultOut(path=path, status="rejected", detail="too_little_text"))
            continue
        async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
            try:
                page = await store_page(
                    db,
                    org_id=access.org_id,
                    install_id=install_id,
                    user_id=access.user.user_id,
                    kind="site",
                    title=(title or clean)[:200],
                    url=target.url,
                    content=content[:200_000],
                    source="indexed",
                )
            except PageLimit:
                results.append(IndexResultOut(path=path, status="rejected", detail="page_limit"))
                continue
            await record_audit(
                db,
                action="assistant.page_index",
                outcome="success",
                actor_user_id=access.user.user_id,
                org_id=access.org_id,
                target_type="kb_page",
                target_id=page.id,
            )
            results.append(IndexResultOut(path=path, status="indexed", page_id=page.id))
    return results


# ----------------------------------------------------------------------------- forms


def _form_out(f: FormDefinitionRow) -> FormOut:
    return FormOut(
        id=f.id,
        name=f.name,
        category=f.category,
        enabled=f.enabled,
        definition=f.definition,
        updated_at=f.updated_at,
    )


@router.get("/installs/{install_id}/forms", response_model=list[FormOut])
async def list_forms(
    install_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[FormOut]:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await _install(db, access.org_id, install_id)
        rows = await db.scalars(
            select(FormDefinitionRow)
            .where(FormDefinitionRow.install_id == install_id)
            .order_by(FormDefinitionRow.name)
        )
        return [_form_out(f) for f in rows]


@router.post("/installs/{install_id}/forms", status_code=201, response_model=FormOut)
async def create_form(
    install_id: uuid.UUID,
    body: FormIn,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> FormOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await _install(db, access.org_id, install_id)
        count = await db.scalars(
            select(FormDefinitionRow.id).where(FormDefinitionRow.install_id == install_id)
        )
        if len(count.all()) >= MAX_FORMS_PER_INSTALL:
            raise HTTPException(409, f"At most {MAX_FORMS_PER_INSTALL} forms per install.")
        row = FormDefinitionRow(
            org_id=access.org_id,
            install_id=install_id,
            name=body.name,
            category=body.definition.category,
            definition=body.definition.model_dump(mode="json"),
            enabled=body.enabled,
            created_by=access.user.user_id,
        )
        db.add(row)
        await db.flush()
        await record_audit(
            db,
            action="assistant.form_create",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="form_definition",
            target_id=row.id,
            details={"category": row.category, "fields": len(body.definition.fields)},
        )
        return _form_out(row)


@router.put("/installs/{install_id}/forms/{form_id}", response_model=FormOut)
async def replace_form(
    install_id: uuid.UUID,
    form_id: uuid.UUID,
    body: FormIn,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> FormOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        row = await db.scalar(
            select(FormDefinitionRow).where(
                FormDefinitionRow.id == form_id,
                FormDefinitionRow.install_id == install_id,
                FormDefinitionRow.org_id == access.org_id,
            )
        )
        if row is None:
            raise HTTPException(404, "Form not found.")
        row.name = body.name
        row.category = body.definition.category
        row.definition = body.definition.model_dump(mode="json")
        row.enabled = body.enabled
        row.updated_at = datetime.now(UTC)
        await record_audit(
            db,
            action="assistant.form_update",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="form_definition",
            target_id=form_id,
        )
        return _form_out(row)


@router.delete("/installs/{install_id}/forms/{form_id}", status_code=204)
async def delete_form(
    install_id: uuid.UUID,
    form_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> None:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        result = await db.execute(
            delete(FormDefinitionRow).where(
                FormDefinitionRow.id == form_id,
                FormDefinitionRow.install_id == install_id,
                FormDefinitionRow.org_id == access.org_id,
            )
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            raise HTTPException(404, "Form not found.")
        await record_audit(
            db,
            action="assistant.form_delete",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="form_definition",
            target_id=form_id,
        )


# ----------------------------------------------------------------------------- hand-offs


def _handoff_out(h: Handoff) -> HandoffOut:
    return HandoffOut(
        id=h.id,
        install_id=h.install_id,
        summary=h.summary,
        contact=h.contact,
        status=h.status,
        consent_at=h.consent_at,
        expires_at=h.expires_at,
        created_at=h.created_at,
    )


@router.get("/installs/{install_id}/handoffs", response_model=list[HandoffOut])
async def list_handoffs(
    install_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[HandoffOut]:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await _install(db, access.org_id, install_id)
        rows = await db.scalars(
            select(Handoff)
            .where(Handoff.install_id == install_id, Handoff.expires_at > datetime.now(UTC))
            .order_by(Handoff.created_at.desc())
            .limit(200)
        )
        return [_handoff_out(h) for h in rows]


@router.patch("/installs/{install_id}/handoffs/{handoff_id}", response_model=HandoffOut)
async def update_handoff(
    install_id: uuid.UUID,
    handoff_id: uuid.UUID,
    body: HandoffPatch,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> HandoffOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        handoff = await db.scalar(
            select(Handoff).where(
                Handoff.id == handoff_id,
                Handoff.install_id == install_id,
                Handoff.org_id == access.org_id,
            )
        )
        if handoff is None:
            raise HTTPException(404, "Hand-off not found.")
        handoff.status = body.status
        return _handoff_out(handoff)


@router.delete("/installs/{install_id}/handoffs/{handoff_id}", status_code=204)
async def delete_handoff(
    install_id: uuid.UUID,
    handoff_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> None:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        result = await db.execute(
            delete(Handoff).where(
                Handoff.id == handoff_id,
                Handoff.install_id == install_id,
                Handoff.org_id == access.org_id,
            )
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            raise HTTPException(404, "Hand-off not found.")
        await record_audit(
            db,
            action="assistant.handoff_delete",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="handoff",
            target_id=handoff_id,
        )


# ------------------------------------------------------------- learning guardian consents


def _consent_out(c: LearningConsent) -> ConsentOut:
    return ConsentOut(
        id=c.id,
        label=c.label,
        age_band=c.age_band,
        consent_basis=c.consent_basis,
        expires_at=c.expires_at,
        revoked=c.revoked_at is not None,
        created_at=c.created_at,
    )


@router.get("/installs/{install_id}/consents", response_model=list[ConsentOut])
async def list_consents(
    install_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[ConsentOut]:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await _install(db, access.org_id, install_id)
        rows = (
            await db.scalars(
                select(LearningConsent)
                .where(
                    LearningConsent.install_id == install_id,
                    LearningConsent.org_id == access.org_id,
                )
                .order_by(LearningConsent.created_at.desc())
                .limit(500)
            )
        ).all()
    return [_consent_out(c) for c in rows]


@router.post("/installs/{install_id}/consents", status_code=201, response_model=ConsentCreatedOut)
async def create_consent(
    install_id: uuid.UUID,
    body: ConsentIn,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ConsentCreatedOut:
    """Records that the installer collected a guardian's consent, and returns the signed token.

    KMR AI cannot check that the consent really was collected. The installer is responsible for
    that; this record, the expiry and the revocation are what the learner's session is tied to.
    """
    now = datetime.now(UTC)
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        install = await _install(db, access.org_id, install_id)
        if install.revoked_at is not None or "learning" not in install.modules:
            raise HTTPException(409, "Turn the learning module on for this install first.")
        consent = LearningConsent(
            org_id=access.org_id,
            install_id=install_id,
            label=body.label.strip(),
            age_band=body.age_band,
            consent_basis=body.consent_basis.strip(),
            expires_at=now + timedelta(days=body.days),
            created_by=access.user.user_id,
            created_at=now,
        )
        db.add(consent)
        await db.flush()
        token = consent_token(secret_key(settings), consent.id, install_id)
        await record_audit(
            db,
            action="learning.consent_issue",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="learning_consent",
            target_id=consent.id,
            details={"age_band": body.age_band, "days": body.days},
        )
        out = _consent_out(consent)
    return ConsentCreatedOut(**out.model_dump(), token=token)


@router.post("/installs/{install_id}/consents/{consent_id}/revoke", response_model=ConsentOut)
async def revoke_consent(
    install_id: uuid.UUID,
    consent_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("admin")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> ConsentOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await _install(db, access.org_id, install_id)
        consent = await db.scalar(
            select(LearningConsent).where(
                LearningConsent.id == consent_id,
                LearningConsent.install_id == install_id,
                LearningConsent.org_id == access.org_id,
            )
        )
        if consent is None:
            raise HTTPException(404, "Consent not found.")
        if consent.revoked_at is None:
            now = datetime.now(UTC)
            await db.execute(
                update(LearningConsent)
                .where(LearningConsent.id == consent_id)
                .values(revoked_at=now)
            )
            consent.revoked_at = now
            await record_audit(
                db,
                action="learning.consent_revoke",
                outcome="success",
                actor_user_id=access.user.user_id,
                org_id=access.org_id,
                target_type="learning_consent",
                target_id=consent_id,
            )
        return _consent_out(consent)
