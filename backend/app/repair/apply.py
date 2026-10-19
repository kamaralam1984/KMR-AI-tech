"""Everything that must still be true at the moment a change is applied.

Approval is not a flag: it is checked against the live plan, repository and checks every time.
Each failure raises `ApplyBlocked` with a stable code, so it can be shown, audited and tested.
The only thing applying ever does is create a NEW branch from the approved base commit; there is
no merge, deploy, migration or deletion path.
"""

from datetime import datetime

from app.config import Settings
from app.db.models import Approval, CheckRun, Issue, RepairPlan, RepositoryConnection
from app.repair.binding import ACTION_PUSH_BRANCH, binding_hash, binding_payload
from app.repair.diffing import build_diff, diff_hash
from app.repair.policy import sha256_text, validate_changes
from app.repair.repo import RepoClient, RepoError


class ApplyBlocked(Exception):  # noqa: N818 - domain term
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


def approval_state(approval: Approval, plan: RepairPlan, now: datetime) -> str:
    if approval.decision == "rejected":
        return "rejected"
    if approval.revoked_at is not None:
        return "revoked"
    if approval.consumed_at is not None:
        return "used"
    if approval.expires_at <= now:
        return "expired"
    if plan.status != "proposed":
        return "invalidated"
    return "active"


def expected_binding(
    approval: Approval, plan: RepairPlan, conn: RepositoryConnection, issue_ids: list[str]
) -> str:
    return binding_hash(
        binding_payload(
            org_id=plan.org_id,
            project_id=plan.project_id,
            provider=conn.provider,
            location=conn.location,
            base_branch=plan.base_branch,
            branch_name=plan.branch_name,
            base_sha=plan.base_sha,
            issue_ids=issue_ids,
            diff_sha256=plan.diff_sha256,
            action=ACTION_PUSH_BRANCH,
            approver_id=approval.approver_id,
        )
    )


async def verify_for_apply(
    *,
    repo: RepoClient,
    plan: RepairPlan,
    conn: RepositoryConnection,
    approval: Approval,
    issues: list[Issue],
    latest_check: CheckRun | None,
    now: datetime,
    settings: Settings,
) -> dict[str, str]:
    """Returns the exact file contents to commit, or raises ApplyBlocked."""
    state = approval_state(approval, plan, now)
    if approval.decision != "approved" or state != "active":
        raise ApplyBlocked(f"approval_{state}", f"This approval cannot be used: it is {state}.")
    if approval.repair_id != plan.id or approval.org_id != plan.org_id:
        raise ApplyBlocked("approval_mismatch", "This approval belongs to a different repair.")
    if approval.action != ACTION_PUSH_BRANCH:
        raise ApplyBlocked("action_not_allowed", "This action is not allowed.")
    issue_ids = [str(i.id) for i in issues]
    if sorted(approval.issue_ids) != sorted(issue_ids) or any(i.state != "open" for i in issues):
        raise ApplyBlocked("issues_changed", "The issues are no longer open or have changed.")
    if expected_binding(approval, plan, conn, issue_ids) != approval.bound_hash:
        raise ApplyBlocked(
            "binding_mismatch",
            "The plan, repository or branch changed after approval. Ask for a new approval.",
        )
    if sha256_text(plan.diff) != plan.diff_sha256 or plan.diff_sha256 != approval.diff_sha256:
        raise ApplyBlocked("diff_mismatch", "The change does not match what was approved.")
    if latest_check is None or latest_check.diff_sha256 != plan.diff_sha256:
        raise ApplyBlocked("checks_missing", "Checks have not been run on this exact change.")
    if latest_check.overall == "failed":
        raise ApplyBlocked("checks_failed", "The latest checks failed. Nothing was applied.")
    if latest_check.overall != "passed" and not approval.acknowledged_incomplete_checks:
        raise ApplyBlocked("checks_incomplete", "The checks did not all run and were not accepted.")

    changes = {c["path"]: c["new_content"] for c in plan.changes}
    # The target must still look like it did when the change was planned.
    try:
        head = await repo.resolve_branch(plan.base_branch)
        old: dict[str, str | None] = {}
        for path in changes:
            at_base = await repo.read_file(plan.base_sha, path)
            old[path] = at_base
            if head != plan.base_sha and await repo.read_file(head, path) != at_base:
                raise ApplyBlocked(
                    "target_changed",
                    f"{path} changed on {plan.base_branch} after this plan was made. Plan again.",
                )
    except RepoError as exc:
        raise ApplyBlocked("repo_unavailable", exc.message) from exc

    validation = validate_changes(
        changes,
        old,
        max_files=settings.repo_max_files_per_repair,
        max_bytes=settings.repo_max_patch_bytes,
    )
    if not validation.ok:
        raise ApplyBlocked("policy_violation", "The change no longer passes the safety rules.")
    if diff_hash(build_diff(old, changes)) != approval.diff_sha256:
        raise ApplyBlocked("diff_mismatch", "The change does not match what was approved.")
    return changes
