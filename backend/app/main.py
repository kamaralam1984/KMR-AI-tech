from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.assistant.cors import PublicCorsMiddleware
from app.config import Settings, get_settings, validate_scan_settings
from app.db.engine import create_engine
from app.diagnosis.anthropic_provider import AnthropicProvider
from app.diagnosis.rules import RulesProvider
from app.errors import register_exception_handlers
from app.logging_config import configure_logging
from app.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.repair.providers import make_repair_provider
from app.routes import (
    api_checks,
    approvals,
    assistant,
    assistant_public,
    audit,
    auth,
    backend_evidence,
    dashboard,
    extension,
    extension_public,
    health,
    issue_github,
    issues,
    members,
    organizations,
    projects,
    repairs,
    scans,
    sites,
    traffic,
    verification,
)
from app.scanning.api_checks import ProxyTransport
from app.scanning.ownership import NetworkOwnershipVerifier
from app.scanning.queue import ScanQueue
from app.scanning.storage import EvidenceStore
from app.security.rate_limit import MemoryRateLimiter, RateLimiter, RedisRateLimiter
from app.security.sessions import CSRF_HEADER


def _build_rate_limiter(settings: Settings) -> tuple[RateLimiter, Redis | None]:
    if settings.redis_url is not None:
        client = Redis.from_url(settings.redis_url.get_secret_value())
        return RedisRateLimiter(client), client
    if settings.app_env in ("local", "test"):
        return MemoryRateLimiter(), None
    raise RuntimeError("KMR_REDIS_URL is required outside local/test: login must be rate limited")


def _lifespan(settings: Settings):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        validate_scan_settings(settings)
        redis_client: Redis | None = None
        app.state.engine = None
        app.state.scan_queue = None
        app.state.api_transport = ProxyTransport(settings.scan_proxy_url)
        app.state.diagnosis_provider = (
            AnthropicProvider(settings) if settings.ai_provider == "anthropic" else RulesProvider()
        )
        app.state.repair_provider = make_repair_provider(settings)
        app.state.ownership_verifier = NetworkOwnershipVerifier(settings.scan_proxy_url)
        app.state.evidence_store = EvidenceStore(settings.evidence_dir)
        if settings.database_url is not None:
            secret = settings.secret_key
            if secret is None or len(secret.get_secret_value()) < 32:
                raise RuntimeError("KMR_SECRET_KEY (at least 32 characters) is required")
            app.state.engine = create_engine(settings.database_url.get_secret_value())
            app.state.rate_limiter, redis_client = _build_rate_limiter(settings)
            if redis_client is not None:
                app.state.scan_queue = ScanQueue(redis_client)
        try:
            yield
        finally:
            if app.state.engine is not None:
                await app.state.engine.dispose()
            if redis_client is not None:
                await redis_client.aclose()

    return lifespan


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="KMR AI API",
        version="0.1.0",
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=_lifespan(settings),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", REQUEST_ID_HEADER, CSRF_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_middleware(PublicCorsMiddleware)
    app.add_middleware(RequestContextMiddleware, hsts=settings.app_env in ("staging", "production"))
    register_exception_handlers(app)
    app.include_router(health.router)
    for router in (
        auth,
        organizations,
        members,
        projects,
        sites,
        scans,
        api_checks,
        backend_evidence,
        issues,
        issue_github,
        repairs,
        approvals,
        audit,
        verification,
        dashboard,
        assistant,
        assistant_public,
        extension,
        extension_public,
        traffic,
    ):
        app.include_router(router.router)
    return app


app = create_app()
