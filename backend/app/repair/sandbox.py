"""Runs checks on a proposed change inside a locked-down container.

The change is applied to a throwaway COPY of the base commit; the container gets that copy
read-only, no network at all, no capabilities, a read-only root, an unprivileged user, and memory,
CPU, process and time limits. No environment variable, token or secret is passed in. Its output is
sanitized and truncated before it is stored or shown.

Statuses are honest: `passed` and `failed` mean the check really ran; `skipped` means it did not
apply; `unavailable` means it could not run here (no Docker, image missing, timeout). Nothing is
reported as passed unless it ran and exited 0.
"""

import ast
import asyncio
import json
import os
import shutil
import tempfile
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

from app.config import Settings
from app.scanning.sanitize import sanitize_text

Status = Literal["passed", "failed", "skipped", "unavailable"]
MAX_OUTPUT = 4000


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str = ""
    duration_ms: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
        }


def materialize(base_files: Mapping[str, str], changes: Mapping[str, str], root: Path) -> None:
    """Writes the base tree with `changes` applied under `root` (paths are pre-validated)."""
    merged = {**base_files, **changes}
    root_resolved = root.resolve()
    for path, text in merged.items():
        target = (root / path).resolve()
        if not target.is_relative_to(root_resolved):
            raise ValueError("path escapes the sandbox directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


class _Html(HTMLParser):
    VOID = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
        "track", "wbr",
    }  # fmt: skip

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.problem: str | None = None

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.VOID:
            return
        if tag in self.stack:
            while self.stack and self.stack[-1] != tag:
                self.stack.pop()  # an unclosed inner tag is tolerated by browsers
            self.stack.pop()
        else:
            self.problem = self.problem or f"</{tag}> has no matching opening tag"


def syntax_check(changes: Mapping[str, str]) -> CheckResult:
    """Parses each changed .json/.py/.html file in-process. Parsing never executes anything."""
    started = time.monotonic()
    checked, problems = 0, []
    for path, text in sorted(changes.items()):
        lower = path.lower()
        try:
            if lower.endswith(".json"):
                json.loads(text)
            elif lower.endswith(".py"):
                ast.parse(text)
            elif lower.endswith((".html", ".htm")):
                parser = _Html()
                parser.feed(text)
                parser.close()
                if parser.problem:
                    raise ValueError(parser.problem)
            else:
                continue
        except (ValueError, SyntaxError) as exc:
            problems.append(f"{path}: {sanitize_text(str(exc), 120)}")
        checked += 1
    ms = int((time.monotonic() - started) * 1000)
    if problems:
        return CheckResult("syntax", "failed", "; ".join(problems)[:MAX_OUTPUT], ms)
    if checked == 0:
        return CheckResult("syntax", "skipped", "No JSON, Python or HTML files changed.", ms)
    return CheckResult("syntax", "passed", f"{checked} file(s) parsed without errors.", ms)


def docker_command(settings: Settings, name: str, workdir: Path, shell_command: str) -> list[str]:
    return [
        "docker", "run", "--rm", "--pull", "never", "--name", name,
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", str(settings.check_pids_limit),
        "--memory", settings.check_memory,
        "--memory-swap", settings.check_memory,
        "--cpus", str(settings.check_cpus),
        "--user", "65534:65534",
        "--tmpfs", "/tmp:rw,nosuid,size=64m",  # noqa: S108 - inside the container
        "-e", "HOME=/tmp",
        "-v", f"{workdir}:/work:ro",
        "-w", "/work",
        "--entrypoint", "sh",
        settings.check_image,
        "-c", shell_command,
    ]  # fmt: skip


async def run_in_sandbox(
    settings: Settings, name: str, workdir: Path, shell_command: str
) -> CheckResult:
    started = time.monotonic()
    name_ = f"kmr-check-{uuid.uuid4().hex[:12]}"
    try:
        proc = await asyncio.create_subprocess_exec(
            *docker_command(settings, name_, workdir, shell_command),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        )
    except FileNotFoundError:
        return CheckResult(name, "unavailable", "Docker is not installed on this server.")
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), settings.check_timeout_seconds)
    except TimeoutError:
        kill = await asyncio.create_subprocess_exec(
            "docker", "kill", name_,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )  # fmt: skip
        await kill.wait()
        proc.kill()
        ms = int((time.monotonic() - started) * 1000)
        return CheckResult(
            name, "unavailable", f"Stopped after {settings.check_timeout_seconds} seconds.", ms
        )
    ms = int((time.monotonic() - started) * 1000)
    text = sanitize_text(out.decode("utf-8", "replace")[-MAX_OUTPUT * 2 :], MAX_OUTPUT)
    if proc.returncode in (125, 126, 127) and ("docker" in text.lower() or "image" in text.lower()):
        return CheckResult(name, "unavailable", "The check sandbox could not start.", ms)
    if proc.returncode == 0:
        return CheckResult(name, "passed", text, ms)
    return CheckResult(name, "failed", text or f"Exited with code {proc.returncode}.", ms)


async def run_checks(
    settings: Settings,
    base_files: Mapping[str, str],
    changes: Mapping[str, str],
    check_command: str | None,
) -> list[CheckResult]:
    results = [syntax_check(changes)]
    js = sorted(p for p in changes if p.lower().endswith((".js", ".mjs", ".cjs")))
    workdir = Path(tempfile.mkdtemp(prefix="kmr-check-"))
    try:
        workdir.chmod(0o755)
        materialize(base_files, changes, workdir)
        for path in workdir.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)
        if js:
            quoted = " ".join("'" + p.replace("'", "'\\''") + "'" for p in js)
            results.append(
                await run_in_sandbox(
                    settings,
                    "javascript_syntax",
                    workdir,
                    f'for f in {quoted}; do node --check "$f" || exit 1; done',
                )
            )
        else:
            results.append(
                CheckResult("javascript_syntax", "skipped", "No JavaScript files changed.")
            )
        if check_command:
            results.append(await run_in_sandbox(settings, "project_check", workdir, check_command))
        else:
            results.append(
                CheckResult(
                    "project_check",
                    "skipped",
                    "No project check command is configured: the project's own tests did not run.",
                )
            )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return results


def overall_status(results: list[CheckResult]) -> str:
    """One word for a check run. `passed` only if nothing failed or was unavailable and at least
    one check really ran and exited 0; `not_run` means every check was skipped."""
    statuses = {r.status for r in results}
    if "failed" in statuses:
        return "failed"
    if "unavailable" in statuses:
        return "unavailable"
    if "passed" in statuses:
        return "passed"
    return "not_run"
