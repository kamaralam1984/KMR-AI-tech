"""Turns one issue into a validated repair plan against an exact base commit.

Nothing here writes to a repository. The proposal (from rules, a model, or a person) is checked
against policy.py, diffed against the base, and hashed; the hash is what an approval will be bound
to. Issues that need review get no automatic proposal.
"""

from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.repair.context import load_snapshot
from app.repair.diffing import build_diff, diff_hash
from app.repair.policy import Finding, path_problem, sha256_text, validate_changes
from app.repair.providers import RepairInput, RepairProposal, RepairProvider
from app.repair.repo import RepoClient, RepoError


@dataclass
class PlanResult:
    status: str  # proposed | no_fix | rejected
    source: str  # rules | ai | manual
    provider: str
    model: str
    base_sha: str
    explanation: str
    changes: list[dict[str, Any]] = field(default_factory=list)
    diff: str = ""
    diff_sha256: str = ""
    findings: list[Finding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    usage: Any = None


def _count(old: str | None, new: str) -> tuple[int, int]:
    old_lines = set((old or "").splitlines())
    new_lines = new.splitlines()
    return (
        sum(1 for line in new_lines if line not in old_lines),
        sum(1 for line in (old or "").splitlines() if line not in set(new_lines)),
    )


async def build_plan(
    *,
    repo: RepoClient,
    settings: Settings,
    default_branch: str,
    issue: RepairInput,
    needs_review: bool,
    provider: RepairProvider,
    manual: dict[str, str] | None = None,
    manual_explanation: str = "",
) -> PlanResult:
    base_sha = await repo.resolve_branch(default_branch)
    source, name, model = (
        ("manual", "customer", "")
        if manual is not None
        else (provider.source, provider.name, provider.model)
    )
    result = PlanResult("no_fix", source, name, model, base_sha, "")
    if not await repo.is_clean():
        result.warnings.append(
            "The repository has uncommitted changes. The plan uses the committed code of the "
            f"{default_branch} branch only; the uncommitted work is not touched or included."
        )
    if needs_review and manual is None:
        result.explanation = (
            "This issue needs review, so no automatic fix is proposed. "
            "Review it, or submit your own change."
        )
        return result

    snapshot = await load_snapshot(repo, base_sha)
    for item in snapshot.excluded:
        result.warnings.append(f"Not shown to the proposer: {item['path']} ({item['reason']}).")

    proposal: RepairProposal | None
    if manual is not None:
        proposal = RepairProposal(
            changes=manual, explanation=manual_explanation or "Submitted by you."
        )
    else:
        inp = RepairInput(**{**issue.__dict__, "files": snapshot.files})
        proposal = await provider.propose(inp)
        if proposal is None:
            result.explanation = (
                "No automatic fix is available for this issue with the files in this repository."
            )
            return result
        result.usage = proposal.usage
        result.model = proposal.model or model
    result.explanation = proposal.explanation

    old: dict[str, str | None] = {}
    findings: list[Finding] = []
    for path in proposal.changes:
        if path_problem(path) is not None:
            continue  # reported by validate_changes below
        if path in snapshot.files:
            old[path] = snapshot.files[path]
            continue
        try:
            existing = await repo.read_file(base_sha, path)
        except RepoError as exc:
            findings.append(Finding("unreadable_file", f"Cannot read the file ({exc.code}).", path))
            continue
        if existing is not None:
            if manual is None:
                findings.append(
                    Finding("file_not_shown", "The proposer changed a file it was not shown.", path)
                )
                continue
            old[path] = existing
        else:
            old[path] = None

    validation = validate_changes(
        proposal.changes,
        old,
        max_files=settings.repo_max_files_per_repair,
        max_bytes=settings.repo_max_patch_bytes,
    )
    findings.extend(validation.findings)
    if findings:
        result.status = "rejected"
        result.findings = findings
        return result

    diff = build_diff(old, proposal.changes)
    result.status = "proposed"
    result.diff = diff
    result.diff_sha256 = diff_hash(diff)
    for path in sorted(proposal.changes):
        added, removed = _count(old[path], proposal.changes[path])
        result.changes.append(
            {
                "path": path,
                "action": "create" if old[path] is None else "modify",
                "old_sha256": None if (before := old[path]) is None else sha256_text(before),
                "new_sha256": sha256_text(proposal.changes[path]),
                "additions": added,
                "deletions": removed,
                "new_content": proposal.changes[path],
            }
        )
    return result
