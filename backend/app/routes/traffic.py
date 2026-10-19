"""Customer-supplied page-view counts per path, used only to weight the impact ranking (12A).

KMR AI does not measure or verify these numbers and labels them as the customer's wherever they
appear. Only page views are read; there is nowhere to store revenue or conversion data.
"""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.auth.deps import OrgAccess, get_engine, org_access
from app.db.audit import record_audit
from app.db.models import Project, TrafficImport
from app.db.tenant import tenant_session
from app.diagnosis.traffic import TrafficFormatError, TrafficIndex, parse_csv

router = APIRouter(
    prefix="/v1/organizations/{org_id}/projects/{project_id}/traffic", tags=["traffic"]
)
MAX_PERIOD_DAYS = 366


class TrafficIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_label: str = Field(min_length=1, max_length=100)
    period_start: date
    period_end: date
    csv: str = Field(min_length=3, max_length=450_000)

    @model_validator(mode="after")
    def period(self) -> "TrafficIn":
        if self.period_end < self.period_start:
            raise ValueError("The end of the period is before its start.")
        if (self.period_end - self.period_start).days > MAX_PERIOD_DAYS:
            raise ValueError("The period can be at most a year.")
        if self.period_end > date.today():
            raise ValueError("The period cannot end in the future.")
        return self


class TrafficPage(BaseModel):
    path: str
    views: int
    share_percent: float


class TrafficOut(BaseModel):
    present: bool
    source: str = "customer_supplied"
    source_label: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    total_views: int = 0
    pages: int = 0
    rows_rejected: int = 0
    top_pages: list[TrafficPage] = []
    notice: str = (
        "These numbers come from your own upload. KMR AI did not measure or verify them, and "
        "uses only page views. No revenue or conversion figure is calculated or shown."
    )


async def load_traffic(
    db: AsyncSession, org_id: uuid.UUID, project_ids: set[uuid.UUID]
) -> dict[uuid.UUID, TrafficIndex]:
    """The traffic index of each project that has one. Tenant-scoped like every other read."""
    if not project_ids:
        return {}
    rows = (
        await db.scalars(
            select(TrafficImport).where(
                TrafficImport.org_id == org_id, TrafficImport.project_id.in_(project_ids)
            )
        )
    ).all()
    grouped: dict[uuid.UUID, list[TrafficImport]] = {}
    for row in rows:
        grouped.setdefault(row.project_id, []).append(row)
    out = {}
    for pid, items in grouped.items():
        views = {r.path: r.views for r in items}
        first = items[0]
        out[pid] = TrafficIndex(
            views=views,
            total=sum(views.values()),
            period_start=first.period_start.isoformat(),
            period_end=first.period_end.isoformat(),
            source_label=first.source_label,
        )
    return out


def _out(index: TrafficIndex | None, rejected: int = 0) -> TrafficOut:
    if index is None:
        return TrafficOut(present=False)
    top = sorted(index.views.items(), key=lambda kv: -kv[1])[:10]
    return TrafficOut(
        present=True,
        source_label=index.source_label,
        period_start=date.fromisoformat(index.period_start),
        period_end=date.fromisoformat(index.period_end),
        total_views=index.total,
        pages=len(index.views),
        rows_rejected=rejected,
        top_pages=[
            TrafficPage(path=p, views=v, share_percent=round(100 * v / index.total, 2))
            for p, v in top
        ],
    )


async def _project(db: AsyncSession, org_id: uuid.UUID, project_id: uuid.UUID) -> None:
    found = await db.scalar(
        select(Project.id).where(Project.id == project_id, Project.org_id == org_id)
    )
    if found is None:
        raise HTTPException(404, "Project not found.")


@router.get("", response_model=TrafficOut)
async def get_traffic(
    project_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("viewer")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> TrafficOut:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await _project(db, access.org_id, project_id)
        index = (await load_traffic(db, access.org_id, {project_id})).get(project_id)
    return _out(index)


@router.put("", response_model=TrafficOut)
async def put_traffic(
    project_id: uuid.UUID,
    body: TrafficIn,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> TrafficOut:
    """Replaces the project's traffic data with this upload."""
    try:
        parsed = parse_csv(body.csv)
    except TrafficFormatError as exc:
        raise HTTPException(422, str(exc)) from exc
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await _project(db, access.org_id, project_id)
        await db.execute(
            delete(TrafficImport).where(
                TrafficImport.org_id == access.org_id, TrafficImport.project_id == project_id
            )
        )
        db.add_all(
            TrafficImport(
                org_id=access.org_id,
                project_id=project_id,
                path=path,
                views=views,
                period_start=body.period_start,
                period_end=body.period_end,
                source_label=body.source_label.strip(),
                imported_by=access.user.user_id,
            )
            for path, views in parsed.rows.items()
        )
        await db.flush()
        await record_audit(
            db,
            action="traffic.import",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="project",
            target_id=project_id,
            details={"pages": len(parsed.rows), "rejected": parsed.rejected},
        )
        index = (await load_traffic(db, access.org_id, {project_id})).get(project_id)
    return _out(index, parsed.rejected)


@router.delete("", status_code=204)
async def delete_traffic(
    project_id: uuid.UUID,
    access: OrgAccess = Depends(org_access("member")),  # noqa: B008
    engine: AsyncEngine = Depends(get_engine),  # noqa: B008
) -> None:
    async with tenant_session(engine, user_id=access.user.user_id, org_id=access.org_id) as db:
        await _project(db, access.org_id, project_id)
        await db.execute(
            delete(TrafficImport).where(
                TrafficImport.org_id == access.org_id, TrafficImport.project_id == project_id
            )
        )
        await record_audit(
            db,
            action="traffic.delete",
            outcome="success",
            actor_user_id=access.user.user_id,
            org_id=access.org_id,
            target_type="project",
            target_id=project_id,
        )
