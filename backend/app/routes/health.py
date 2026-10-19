import asyncio
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, SecretStr

from app.config import Settings, get_settings

router = APIRouter(tags=["health"])

CheckState = Literal["not_configured", "reachable", "unreachable"]


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, CheckState]


async def _tcp_check(url: SecretStr | None, default_port: int) -> CheckState:
    """Reports whether the host:port of a configured URL accepts a TCP connection.

    This proves reachability only, not that credentials or schema are valid.
    """
    if url is None:
        return "not_configured"
    parts = urlsplit(url.get_secret_value())
    if not parts.hostname:
        return "unreachable"
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(parts.hostname, parts.port or default_port), timeout=1.0
        )
    except (OSError, TimeoutError):
        return "unreachable"
    writer.close()
    await writer.wait_closed()
    return "reachable"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness: the process is up. Touches no dependencies."""
    return HealthResponse(status="ok")


@router.get("/ready", response_model=ReadinessResponse)
async def ready(settings: Settings = Depends(get_settings)) -> JSONResponse:  # noqa: B008
    """Readiness: every configured dependency is reachable; unconfigured ones are only reported."""
    checks: dict[str, CheckState] = {
        "database": await _tcp_check(settings.database_url, 5432),
        "redis": await _tcp_check(settings.redis_url, 6379),
    }
    is_ready = all(state != "unreachable" for state in checks.values())
    body = ReadinessResponse(status="ready" if is_ready else "not_ready", checks=checks)
    return JSONResponse(status_code=200 if is_ready else 503, content=body.model_dump())
