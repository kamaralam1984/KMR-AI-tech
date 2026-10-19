"""Quotas and cost for diagnosis runs, per organization."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import DiagnosisRun


def cost_micros(
    settings: Settings, input_tokens: int | None, output_tokens: int | None
) -> int | None:
    """Cost in millionths of a dollar, or None when prices are not configured or usage unknown.

    Prices are dollars per million tokens, so one token costs `price` micro-dollars.
    """
    if input_tokens is None or output_tokens is None:
        return None
    if not (settings.ai_input_price_per_mtok or settings.ai_output_price_per_mtok):
        return None
    return round(
        input_tokens * settings.ai_input_price_per_mtok
        + output_tokens * settings.ai_output_price_per_mtok
    )


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def runs_last_day(db: AsyncSession, org_id: object) -> int:
    since = datetime.now(UTC) - timedelta(hours=24)
    return int(
        await db.scalar(
            select(func.count())
            .select_from(DiagnosisRun)
            .where(DiagnosisRun.org_id == org_id, DiagnosisRun.created_at >= since)
        )
        or 0
    )


async def month_spend_micros(db: AsyncSession, org_id: object) -> int:
    return int(
        await db.scalar(
            select(func.coalesce(func.sum(DiagnosisRun.cost_micros), 0)).where(
                DiagnosisRun.org_id == org_id, DiagnosisRun.created_at >= month_start()
            )
        )
        or 0
    )


async def month_runs(db: AsyncSession, org_id: object) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(DiagnosisRun)
            .where(DiagnosisRun.org_id == org_id, DiagnosisRun.created_at >= month_start())
        )
        or 0
    )
