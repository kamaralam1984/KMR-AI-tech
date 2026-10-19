"""A small, controlled load check against a RUNNING dev API. Not a benchmark of production.

Creates one throwaway user and organization (random @example.com address; remove them with
`make clean-test-data`), then sends bounded concurrent GET requests and reports latency and errors.
Also confirms that repeated bad logins are rate limited. Usage: python scripts/load-check.py
"""

import asyncio
import statistics
import sys
import time
import uuid

import httpx

BASE = "http://127.0.0.1:8000"
ORIGIN = {"Origin": "http://localhost:3000"}
REQUESTS, CONCURRENCY = 300, 20


async def hammer(client: httpx.AsyncClient, path: str) -> dict[str, float | int]:
    sem = asyncio.Semaphore(CONCURRENCY)
    times: list[float] = []
    codes: dict[int, int] = {}

    async def one() -> None:
        async with sem:
            started = time.perf_counter()
            response = await client.get(path)
            times.append((time.perf_counter() - started) * 1000)
            codes[response.status_code] = codes.get(response.status_code, 0) + 1

    started = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(REQUESTS)))
    wall = time.perf_counter() - started
    times.sort()
    return {
        "path": path,
        "requests": REQUESTS,
        "ok": codes.get(200, 0),
        "other": REQUESTS - codes.get(200, 0),
        "p50_ms": round(statistics.median(times), 1),
        "p95_ms": round(times[int(len(times) * 0.95) - 1], 1),
        "max_ms": round(times[-1], 1),
        "rps": round(REQUESTS / wall, 1),
    }


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE, headers=ORIGIN, timeout=30) as client:
        email = f"load-{uuid.uuid4().hex[:8]}@example.com"
        password = "load check passphrase 12345"
        r = await client.post("/v1/auth/register", json={"email": email, "password": password, "display_name": "Load"})
        r.raise_for_status()
        csrf = (await client.get("/v1/auth/csrf")).json()["csrf_token"]
        org = (await client.post("/v1/organizations", json={"name": "Load check"}, headers={"X-CSRF-Token": csrf})).json()["id"]
        rows = [await hammer(client, p) for p in ("/health", f"/v1/organizations/{org}/dashboard", f"/v1/organizations/{org}/projects")]
        anon = httpx.AsyncClient(base_url=BASE, headers=ORIGIN, timeout=30)
        statuses = [
            (await anon.post("/v1/auth/login", json={"email": email, "password": f"wrong-{i}-passphrase"})).status_code
            for i in range(12)
        ]
        await anon.aclose()
    for row in rows:
        print(row)
    print("bad-login statuses:", statuses)
    failed = any(row["other"] for row in rows)
    limited = 429 in statuses
    print("rate limit engaged:", limited, "| errors under load:", failed)
    return 0 if limited and not failed else 1


sys.exit(asyncio.run(main()))
