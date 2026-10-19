"""Scan worker entry point: `python -m app.worker` (see `make worker`)."""

import asyncio
import signal
from contextlib import AbstractAsyncContextManager

from playwright.async_api import Browser
from redis.asyncio import Redis

from app.config import Settings, get_settings, validate_scan_settings
from app.db.engine import create_engine
from app.logging_config import configure_logging
from app.scanning.browser import open_browser
from app.scanning.queue import ScanQueue
from app.scanning.storage import EvidenceStore
from app.scanning.worker import WorkerDeps, run_worker


def _browser(settings: Settings, engine: str = "chromium") -> AbstractAsyncContextManager[Browser]:
    return open_browser(settings, engine=engine)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    validate_scan_settings(settings)
    if settings.database_url is None or settings.redis_url is None:
        raise SystemExit("KMR_DATABASE_URL and KMR_REDIS_URL are required for the worker")

    engine = create_engine(settings.database_url.get_secret_value())
    redis = Redis.from_url(settings.redis_url.get_secret_value())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)  # finish the current scan, then exit
    deps = WorkerDeps(
        engine=engine,
        settings=settings,
        store=EvidenceStore(settings.evidence_dir),
        browser_factory=_browser,
    )
    try:
        await run_worker(deps, ScanQueue(redis), stop)
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
