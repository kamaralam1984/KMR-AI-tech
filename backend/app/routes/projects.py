import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.deps import OrgAccess, get_engine, org_access
from app.db.audit import record_audit
from app.db.models import Project
from app.db.tenant import tenant_session
from app.scanning.site import describe_site
from app.schemas import ProjectIn, ProjectOut

router = APIRouter(prefix="/v1/organizations/{org_id}/projects", tags=["projects"])

MAX_PROJECTS_PER_ORG = 200


def _out(project: Project) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        created_by=project.created_by,
        created_at=project.created_at,
        updated_at=project.updated_at,
        site=describe_site(project),
    )


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> list[ProjectOut]:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        projects = await db.scalars(
            select(Project)
            .where(Project.org_id == access.org_id)
            .order_by(Project.created_at.desc())
            .limit(MAX_PROJECTS_PER_ORG)
        )
        return [_out(p) for p in projects]


@router.post("", status_code=201, response_model=ProjectOut)
async def create_project(
    body: ProjectIn,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> ProjectOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        count = await db.scalar(
            select(func.count()).select_from(Project).where(Project.org_id == access.org_id)
        )
        if (count or 0) >= MAX_PROJECTS_PER_ORG:
            raise HTTPException(
                409, f"An organization can have at most {MAX_PROJECTS_PER_ORG} projects."
            )
        project = Project(org_id=access.org_id, name=body.name, created_by=access.user.user_id)
        db.add(project)
        await db.flush()
        await db.refresh(project)
        await record_audit(
            db,
            action="project.create",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="project",
            target_id=project.id,
        )
        return _out(project)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> ProjectOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        project = await db.scalar(
            select(Project).where(Project.id == project_id, Project.org_id == access.org_id)
        )
        if project is None:
            raise HTTPException(404, "Project not found.")
        return _out(project)


@router.patch("/{project_id}", response_model=ProjectOut)
async def rename_project(
    project_id: uuid.UUID,
    body: ProjectIn,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> ProjectOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        project = await db.scalar(
            update(Project)
            .where(Project.id == project_id, Project.org_id == access.org_id)
            .values(name=body.name, updated_at=func.now())
            .returning(Project)
            .execution_options(populate_existing=True)
        )
        if project is None:
            raise HTTPException(404, "Project not found.")
        await record_audit(
            db,
            action="project.rename",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="project",
            target_id=project_id,
        )
        return _out(project)
