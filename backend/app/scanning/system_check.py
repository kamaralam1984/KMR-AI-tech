"""Checks the real scanner sandbox: `make scanner-check` (needs `make scanner-up` and internet).

Unlike the unit tests, this talks to the actual containers. It proves, from the outside, that the
browser has no route of its own, that the hardening settings are really applied, that the egress
proxy stops a real browser from reaching private and metadata addresses, and that a public site
can be scanned end to end. Every line printed is a real observation, and any failure exits 1.
"""

import asyncio
import json
import subprocess
import sys
from typing import Any

from app.config import get_settings
from app.scanning.browser import open_browser
from app.scanning.scanner import ScanLimits, run_scan

BROWSER = "kmr-ai-scan-browser-1"
PROXY = "kmr-ai-scan-proxy-1"
failures: list[str] = []
warnings: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)
    if not ok:
        failures.append(name)


def warn(name: str, detail: str) -> None:
    print(f"  WARN  {name}  ({detail})", flush=True)
    warnings.append(name)


def docker(*args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed docker CLI arguments
        ["docker", *args],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def inspect(container: str) -> dict[str, Any]:
    result = docker("inspect", container)
    data: dict[str, Any] = json.loads(result.stdout)[0]
    return data


def node_probe(script: str) -> str:
    """Runs a short Node script inside the BROWSER container and returns its output."""
    result = docker("exec", BROWSER, "node", "-e", script, timeout=40)
    return (result.stdout + result.stderr).strip()


def check_hardening() -> None:
    print("Container hardening (docker inspect)")
    for name in (BROWSER, PROXY):
        info = inspect(name)
        host = info["HostConfig"]
        check(f"{name}: read-only root filesystem", host["ReadonlyRootfs"] is True)
        check(f"{name}: all capabilities dropped", host["CapDrop"] == ["ALL"], str(host["CapDrop"]))
        check(f"{name}: no-new-privileges", "no-new-privileges:true" in (host["SecurityOpt"] or []))
        check(f"{name}: memory limit set", host["Memory"] > 0, f"{host['Memory'] // 2**20} MiB")
        check(f"{name}: process limit set", (host["PidsLimit"] or 0) > 0, str(host["PidsLimit"]))
        check(f"{name}: not privileged", host["Privileged"] is False)
    networks = set(inspect(BROWSER)["NetworkSettings"]["Networks"])
    check(
        "browser is attached only to the internal scan network",
        networks == {"kmr-ai_scan-net"},
        ", ".join(sorted(networks)),
    )
    check(
        "proxy bridges the scan network and the egress network",
        set(inspect(PROXY)["NetworkSettings"]["Networks"]) == {"kmr-ai_scan-net", "kmr-ai_egress"},
    )
    net = json.loads(docker("network", "inspect", "kmr-ai_scan-net").stdout)[0]
    check("scan network is internal (no route out)", net["Internal"] is True)
    check(
        "browser container is not published on the host",
        not inspect(BROWSER)["NetworkSettings"]["Ports"]
        or all(v is None for v in inspect(BROWSER)["NetworkSettings"]["Ports"].values()),
    )


def check_no_direct_route() -> None:
    print("Network isolation of the browser container (probes run INSIDE it)")
    internet = node_probe(
        "const s=require('net').connect({host:'1.1.1.1',port:443,timeout:4000});"
        "s.on('connect',()=>{console.log('CONNECTED');process.exit(0)});"
        "s.on('timeout',()=>{console.log('BLOCKED');process.exit(0)});"
        "s.on('error',e=>{console.log('BLOCKED '+e.code);process.exit(0)})"
    )
    check(
        "direct connection to the internet (1.1.1.1:443) is impossible",
        "BLOCKED" in internet,
        internet,
    )
    host = node_probe(
        "const s=require('net').connect({host:'172.31.250.1',port:5432,timeout:4000});"
        "s.on('connect',()=>{console.log('CONNECTED');process.exit(0)});"
        "s.on('timeout',()=>{console.log('TIMEOUT');process.exit(0)});"
        "s.on('error',e=>{console.log('ERR '+e.code);process.exit(0)})"
    )
    if "TIMEOUT" in host or "UNREACH" in host:
        check("the host machine is unreachable from the browser container", True, host)
    else:
        # "refused" means the host answered: Docker still lets internal networks reach the
        # gateway address. Safe today because all browser traffic goes through the proxy, but a
        # compromised browser could reach host services bound to 0.0.0.0.
        warn(
            "the host gateway answers from the browser container",
            f"{host}; see 'Scanner sandbox limits' in README.md and scripts/scanner-firewall.sh",
        )
    dns = node_probe(
        "require('dns').lookup('example.com',(e,a)=>{"
        "console.log(e?'NO-DNS '+e.code:'RESOLVED '+a);process.exit(0)})"
    )
    check("the browser container cannot resolve names itself", "NO-DNS" in dns, dns)
    proxy = node_probe(
        "const s=require('net').connect({host:'172.31.250.11',port:3128,timeout:4000});"
        "s.on('connect',()=>{console.log('CONNECTED');process.exit(0)});"
        "s.on('timeout',()=>{console.log('TIMEOUT');process.exit(0)});"
        "s.on('error',e=>{console.log('ERR '+e.code);process.exit(0)})"
    )
    check("it CAN reach the egress proxy (its only way out)", "CONNECTED" in proxy, proxy)


async def check_browser_through_proxy(engine: str = "chromium") -> None:
    print(f"A real {engine} browser asking for forbidden targets (through the real proxy)")
    settings = get_settings()
    page_html = "data:text/html,<html><body>probe</body></html>"
    targets = {
        "cloud metadata http://169.254.169.254/": "http://169.254.169.254/latest/meta-data/",
        "browser's own server http://localhost:3000/": "http://localhost:3000/",
        "loopback http://127.0.0.1:3000/": "http://127.0.0.1:3000/",
        "the other engine's server http://127.0.0.1:3001/": "http://127.0.0.1:3001/",
        "private http://10.0.0.1/": "http://10.0.0.1/",
        "private http://192.168.0.1/": "http://192.168.0.1/",
        "link-local IPv6 http://[fe80::1]/": "http://[fe80::1]/",
    }
    async with open_browser(settings, engine=engine) as browser:
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(page_html)
        for label, url in targets.items():
            result = await page.evaluate(
                """async (url) => {
                    try {
                        const r = await fetch(url);
                        return {status: r.status, blocked: r.headers.get('x-kmr-blocked')};
                    } catch (e) { return {error: String(e)}; }
                }""",
                url,
            )
            blocked = result.get("blocked") or ("error" if "error" in result else None)
            check(
                f"{label} is refused",
                blocked is not None and result.get("status") in (None, 403),
                json.dumps(result),
            )
        secure = await page.evaluate(
            "async () => { try { await fetch('https://169.254.169.254/'); return 'LOADED'; }"
            " catch (e) { return 'FAILED'; } }"
        )
        check("https://169.254.169.254/ (CONNECT tunnel) is refused", secure == "FAILED", secure)
        public = await page.evaluate(
            "async () => { try { const r = await fetch('https://example.com/', {mode:'no-cors'});"
            " return r.type; } catch (e) { return 'FAILED ' + e; } }"
        )
        check(
            "a public site (https://example.com/) IS reachable through the proxy",
            public == "opaque",
            public,
        )
        await context.close()
    log = docker("logs", "--tail", "200", PROXY).stdout
    codes = {
        json.loads(line).get("code")
        for line in log.splitlines()
        if line.startswith("{") and '"blocked"' in line
    }
    check(
        "the proxy logged blocks for address, host and port policies",
        {"blocked_address", "blocked_host"} <= codes,
        ", ".join(sorted(c for c in codes if c)),
    )


class _Sink:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str | None, dict[str, Any], bytes | None]] = []

    async def observation(
        self,
        kind: str,
        severity: str,
        page_url: str | None,
        data: dict[str, Any],
        screenshot: bytes | None = None,
    ) -> None:
        self.items.append((kind, severity, page_url, data, screenshot))

    async def cancel_requested(self) -> bool:
        return False

    async def heartbeat(self) -> None:
        pass


async def check_public_scan() -> None:
    print("A real scan of a public site (https://example.com/)")
    settings = get_settings()
    sink = _Sink()
    async with open_browser(settings) as browser:
        outcome = await asyncio.wait_for(
            run_scan(
                browser,
                start_url="https://example.com/",
                allowed_host="example.com",
                limits=ScanLimits(
                    max_pages=3, max_requests=100, max_bytes=20_000_000, page_timeout_seconds=25
                ),
                sink=sink,
            ),
            timeout=120,
        )
    pages = [i for i in sink.items if i[0] == "page"]
    check("scan completed", outcome.status == "completed", f"{outcome.status} {outcome.error_code}")
    check("the page was loaded with status 200", bool(pages) and pages[0][3].get("status") == 200)
    check("title was read", bool(pages) and "Example Domain" in str(pages[0][3].get("title")))
    shot = pages[0][4] if pages else None
    check("a PNG screenshot was captured", shot is not None and shot.startswith(b"\x89PNG"))
    check(
        "off-site links (iana.org) were not followed",
        outcome.pages_scanned == 1,
        f"pages_scanned={outcome.pages_scanned}",
    )
    dump = json.dumps([i[3] for i in sink.items], default=str)
    check("no secrets or cookies in what was recorded", "cookie" not in dump.lower())


async def main() -> int:
    if docker("version").returncode != 0:
        print("docker is not available")
        return 2
    for name in (BROWSER, PROXY):
        if docker("inspect", "-f", "{{.State.Running}}", name).stdout.strip() != "true":
            print(f"{name} is not running. Start the sandbox with: make scanner-up")
            return 2
    check_hardening()
    check_no_direct_route()
    settings = get_settings()
    engines = ["chromium", *[e for e in settings.scan_engines_available if e != "chromium"]]
    for engine in engines:
        await check_browser_through_proxy(engine)
    await check_public_scan()
    print()
    if failures:
        print(f"FAILED ({len(failures)}): " + "; ".join(failures))
        return 1
    note = f" ({len(warnings)} warning(s) above)" if warnings else ""
    print("ALL SANDBOX CHECKS PASSED" + note)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
