"""Runs scans: claim -> authorize -> preflight -> crawl -> store results -> finish.

Authorization: a scan runs as the user who requested it. The worker re-checks, through
`run_as_tenant`, that this user is still a member with at least the `member` role before any
network activity. Every database write happens inside that organization's tenant context, so the
worker has no way to touch another tenant's rows (Row-Level Security enforces it).
"""

import asyncio
import hashlib
import logging
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import anyio.to_thread
from playwright.async_api import Browser
from playwright.async_api import Error as PlaywrightError
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.config import Settings
from app.db.audit import record_audit
from app.db.models import ApiCheck, ApiCheckRun, Evidence, Observation, Scan
from app.db.tenant import TenantAccessDenied, run_as_tenant, tenant_session
from app.scanning.api_checks import ApiTransport, CheckSpec, ProxyTransport, run_check
from app.scanning.browser import EngineUnavailableError
from app.scanning.net_policy import TargetRejected, resolve_public_addresses, validate_url
from app.scanning.queue import QueueUnavailable, ScanQueue
from app.scanning.sanitize import sanitize_text
from app.scanning.scanner import ScanLimits, ScanOutcome, run_scan
from app.scanning.site import net_policy
from app.scanning.storage import EvidenceStore
from app.security.secrets_box import decrypt_secret

logger = logging.getLogger("app.worker")

MAX_OBSERVATIONS_PER_SCAN = 1500
MAX_ATTEMPTS = 2
HEARTBEAT_STALE_SECONDS = 90
QUEUED_STALE_SECONDS = 120

BrowserFactory = Callable[[Settings], AbstractAsyncContextManager[Browser]]


@dataclass
class WorkerDeps:
    engine: AsyncEngine
    settings: Settings
    store: EvidenceStore
    browser_factory: BrowserFactory
    api_transport: ApiTransport | None = None  # defaults to the egress proxy


class DbSink:
    """Stores what the scanner observes, inside the scan's tenant context."""

    def __init__(
        self,
        engine: AsyncEngine,
        store: EvidenceStore,
        *,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        scan_id: uuid.UUID,
        max_observations: int = MAX_OBSERVATIONS_PER_SCAN,
    ) -> None:
        self._engine = engine
        self._store = store
        self._org_id = org_id
        self._user_id = user_id
        self._scan_id = scan_id
        self._max = max_observations
        self.stored = 0
        self.dropped = 0

    def _session(self) -> AbstractAsyncContextManager[AsyncSession]:
        return tenant_session(self._engine, user_id=self._user_id, org_id=self._org_id)

    async def observation(
        self,
        kind: str,
        severity: str,
        page_url: str | None,
        data: dict[str, Any],
        screenshot: bytes | None = None,
    ) -> None:
        if self.stored >= self._max:
            self.dropped += 1
            return
        self.stored += 1
        evidence_id: uuid.UUID | None = None
        if screenshot is not None:
            evidence_id = uuid.uuid4()
            key = EvidenceStore.key_for(self._org_id, self._scan_id, evidence_id)
            await anyio.to_thread.run_sync(self._store.save, key, screenshot)
        async with self._session() as db:
            if evidence_id is not None:
                db.add(
                    Evidence(
                        id=evidence_id,
                        org_id=self._org_id,
                        scan_id=self._scan_id,
                        kind="screenshot",
                        storage_key=key,
                        content_type="image/png",
                        size_bytes=len(screenshot or b""),
                        sha256=hashlib.sha256(screenshot or b"").hexdigest(),
                        page_url=page_url,
                    )
                )
                await db.flush()  # the observation below references this evidence row
            db.add(
                Observation(
                    org_id=self._org_id,
                    scan_id=self._scan_id,
                    kind=kind,
                    severity=severity,
                    page_url=page_url,
                    data=data,
                    evidence_id=evidence_id,
                )
            )

    async def cancel_requested(self) -> bool:
        async with self._session() as db:
            flag = await db.scalar(select(Scan.cancel_requested_at).where(Scan.id == self._scan_id))
        return flag is not None

    async def heartbeat(self) -> None:
        async with self._session() as db:
            await db.execute(
                update(Scan).where(Scan.id == self._scan_id).values(heartbeat_at=func.now())
            )


async def _finish(
    deps: WorkerDeps,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    scan_id: uuid.UUID,
    status: str,
    summary: dict[str, Any],
    stopped_reason: str | None = None,
    error_code: str | None = None,
) -> None:
    async with tenant_session(deps.engine, user_id=user_id, org_id=org_id) as db:
        result = await db.execute(
            update(Scan)
            .where(Scan.id == scan_id, Scan.status == "running")
            .values(
                status=status,
                summary=summary,
                stopped_reason=stopped_reason,
                error_code=error_code,
                finished_at=datetime.now(UTC),
            )
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            return  # no longer ours (recovered or changed elsewhere); leave it alone
        await record_audit(
            db,
            action="scan.finish",
            outcome="failure" if status == "failed" else "success",
            actor_user_id=user_id,
            org_id=org_id,
            target_type="scan",
            target_id=scan_id,
            details={"status": status, "error_code": error_code, "stopped_reason": stopped_reason},
        )


MAX_API_CHECKS_PER_SCAN = 20


async def run_api_checks(
    deps: WorkerDeps,
    sink: DbSink,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    scan_id: uuid.UUID,
    project_id: uuid.UUID,
    site_url: str,
    allowed_host: str,
) -> dict[str, int]:
    """Runs the project's API checks against the verified host and stores each result."""
    async with tenant_session(deps.engine, user_id=user_id, org_id=org_id) as db:
        checks = (
            await db.scalars(
                select(ApiCheck)
                .where(ApiCheck.project_id == project_id, ApiCheck.org_id == org_id)
                .order_by(ApiCheck.name)
                .limit(MAX_API_CHECKS_PER_SCAN)
            )
        ).all()
        specs = [
            (
                c.id,
                c.name,
                CheckSpec(c.path, c.method, c.expected_status, c.max_latency_ms, c.response_schema),
                c.auth_header_name,
                c.auth_secret_ct,
            )
            for c in checks
        ]
    counts = {"run": 0, "passed": 0, "failed": 0, "error": 0}
    if not specs:
        return counts
    policy = net_policy(deps.settings)
    try:
        origin = validate_url(site_url, policy).origin
    except TargetRejected:
        return counts
    transport = deps.api_transport or ProxyTransport(deps.settings.scan_proxy_url)
    prefix = f"kmr-{str(scan_id)[:8]}-chk"
    for number, (check_id, name, spec, header, ciphertext) in enumerate(specs, start=1):
        credentials: tuple[str, str] | None = None
        unreadable = False
        if header and ciphertext:
            secret = decrypt_secret(deps.settings, ciphertext)
            credentials = (header, secret) if secret is not None else None
            unreadable = secret is None
        request_id = f"{prefix}-{number}"
        result = await run_check(
            spec,
            origin=origin,
            allowed_host=allowed_host,
            transport=transport,
            policy=policy,
            timeout=deps.settings.api_check_timeout_seconds,
            request_id=request_id,
            credentials=credentials,
        )
        if unreadable:
            result.reasons.append({"code": "credentials_unreadable"})
            result.status = "error"
        async with tenant_session(deps.engine, user_id=user_id, org_id=org_id) as db:
            db.add(
                ApiCheckRun(
                    org_id=org_id,
                    check_id=check_id,
                    scan_id=scan_id,
                    triggered_by="scan",
                    status=result.status,
                    http_status=result.http_status,
                    latency_ms=result.latency_ms,
                    response_bytes=result.response_bytes,
                    reasons=result.reasons,
                    request_id=request_id,
                )
            )
        severity = {"passed": "info", "failed": "error", "error": "warning"}[result.status]
        await sink.observation(
            "api_check",
            severity,
            f"{origin}{spec.path}",
            {
                "check_id": str(check_id),
                "name": sanitize_text(name, 100),
                "path": spec.path,
                "status": result.status,
                "http_status": result.http_status,
                "latency_ms": result.latency_ms,
                "reasons": result.reasons,
                "request_id": request_id,
            },
        )
        counts["run"] += 1
        counts[result.status] += 1
    return counts


async def process_scan(deps: WorkerDeps, scan_id: uuid.UUID) -> str:
    """Runs one scan. Returns the final status, or "skipped" if it was not ours to run."""
    async with tenant_session(deps.engine) as db:
        claimed = (
            await db.execute(
                text("SELECT org_id, project_id, requested_by FROM scan_claim(:scan_id)"),
                {"scan_id": scan_id},
            )
        ).first()
    if claimed is None:
        return "skipped"  # duplicate or stale queue message, already running, or cancelled
    org_id, project_id, user_id = claimed
    log = {"scan_id": str(scan_id), "org_id": str(org_id)}

    try:
        async with run_as_tenant(
            deps.engine, user_id=user_id, org_id=org_id, min_role="member"
        ) as scope:
            scan = await scope.session.get(Scan, scan_id)
            if scan is None:
                raise TenantAccessDenied
            site_url, allowed_host = scan.site_url, scan.allowed_host
            limits_json: dict[str, Any] = dict(scan.limits)
    except TenantAccessDenied:
        logger.warning("scan_not_authorized", extra=log)
        await _finish(
            deps,
            org_id=org_id,
            user_id=user_id,
            scan_id=scan_id,
            status="failed",
            summary={},
            error_code="not_authorized",
        )
        return "failed"

    limits = ScanLimits(
        max_pages=int(limits_json["max_pages"]),
        max_requests=int(limits_json["max_requests"]),
        max_bytes=int(limits_json["max_bytes"]),
        page_timeout_seconds=int(limits_json["page_timeout_seconds"]),
        exclusions=tuple(limits_json.get("exclusions", [])),
        inject_request_id=bool(limits_json.get("inject_request_id", False)),
        request_id_prefix=f"kmr-{str(scan_id)[:8]}",
        profile=str(limits_json.get("profile", {}).get("key", "desktop")),
    )
    timeout = int(limits_json["timeout_seconds"])
    sink = DbSink(deps.engine, deps.store, org_id=org_id, user_id=user_id, scan_id=scan_id)

    async def finish(outcome: ScanOutcome) -> str:
        summary = outcome.summary()
        if sink.dropped:
            summary["observations_dropped"] = sink.dropped
        await _finish(
            deps,
            org_id=org_id,
            user_id=user_id,
            scan_id=scan_id,
            status=outcome.status,
            summary=summary,
            stopped_reason=outcome.stopped_reason,
            error_code=outcome.error_code,
        )
        return outcome.status

    started = time.monotonic()
    try:
        # Fail fast, with a clear reason, if the site now resolves somewhere it must not. The
        # egress proxy enforces this again on every connection; this check is for the message.
        policy = net_policy(deps.settings)
        target = validate_url(site_url, policy)
        await anyio.to_thread.run_sync(resolve_public_addresses, target.host, target.port, policy)
    except TargetRejected as rejected:
        logger.warning("scan_target_rejected", extra={**log, "code": rejected.code})
        return await finish(ScanOutcome(status="failed", error_code=rejected.code))

    try:
        async with asyncio.timeout(timeout):
            engine_name = str(limits_json.get("profile", {}).get("engine", "chromium"))
            factory = (
                deps.browser_factory(deps.settings)
                if engine_name == "chromium"
                else deps.browser_factory(deps.settings, engine=engine_name)  # type: ignore[call-arg]
            )
            async with factory as browser:
                outcome = await run_scan(
                    browser, start_url=site_url, allowed_host=allowed_host, limits=limits, sink=sink
                )
            if outcome.status == "completed":
                outcome.api_checks = await run_api_checks(
                    deps,
                    sink,
                    org_id=org_id,
                    user_id=user_id,
                    scan_id=scan_id,
                    project_id=project_id,
                    site_url=site_url,
                    allowed_host=allowed_host,
                )
    except TimeoutError:
        outcome = ScanOutcome(status="failed", error_code="timeout")
    except EngineUnavailableError:
        logger.warning("scan_engine_unavailable", extra=log)
        outcome = ScanOutcome(status="failed", error_code="engine_unavailable")
    except (PlaywrightError, OSError):
        logger.exception("scan_browser_unavailable", extra=log)
        outcome = ScanOutcome(status="failed", error_code="browser_unavailable")
    except Exception:
        logger.exception("scan_internal_error", extra=log)
        outcome = ScanOutcome(status="failed", error_code="internal_error")
    if outcome.duration_ms == 0:
        outcome.duration_ms = int((time.monotonic() - started) * 1000)
    return await finish(outcome)


async def recover_scans(engine: AsyncEngine, queue: ScanQueue) -> int:
    """Re-sends lost queue messages and retries or fails scans whose worker disappeared."""
    async with tenant_session(engine) as db:
        rows = (
            await db.execute(
                text(
                    "SELECT scan_id, action FROM scan_recover("
                    "make_interval(secs => :queued), make_interval(secs => :running), :attempts)"
                ),
                {
                    "queued": QUEUED_STALE_SECONDS,
                    "running": HEARTBEAT_STALE_SECONDS,
                    "attempts": MAX_ATTEMPTS,
                },
            )
        ).all()
    resent = 0
    for scan_id, action in rows:
        if action in ("requeued", "requeue_message"):
            await queue.enqueue(scan_id)
            resent += 1
    return resent


async def run_worker(
    deps: WorkerDeps, queue: ScanQueue, stop: asyncio.Event, *, recover_every: float = 30.0
) -> None:
    last_recovery = 0.0
    while not stop.is_set():
        try:
            if time.monotonic() - last_recovery >= recover_every:
                await recover_scans(deps.engine, queue)
                last_recovery = time.monotonic()
            scan_id = await queue.dequeue(timeout=3)
            if scan_id is not None:
                status = await process_scan(deps, scan_id)
                logger.info("scan_processed", extra={"scan_id": str(scan_id), "status": status})
        except QueueUnavailable:
            logger.error("queue_unavailable")
            await asyncio.sleep(2)
        except Exception:
            logger.exception("worker_iteration_failed")
            await asyncio.sleep(1)
