"""What an approval is bound to.

The hash covers every value that decides what an apply would do: the organization, project,
repository, base branch and commit, the branch to create, the issues, the exact diff hash, the
action and the approver. Change any one and the hash differs, so the approval no longer matches.
"""

import hashlib
import json
from typing import Any

ACTION_PUSH_BRANCH = "push_branch"


def binding_payload(
    *,
    org_id: object,
    project_id: object,
    provider: str,
    location: str,
    base_branch: str,
    branch_name: str,
    base_sha: str,
    issue_ids: list[str],
    diff_sha256: str,
    action: str,
    approver_id: object,
) -> dict[str, Any]:
    return {
        "v": 1,
        "org_id": str(org_id),
        "project_id": str(project_id),
        "repository": {"provider": provider, "location": location},
        "base_branch": base_branch,
        "branch_name": branch_name,
        "base_sha": base_sha,
        "issue_ids": sorted(str(i) for i in issue_ids),
        "diff_sha256": diff_sha256,
        "action": action,
        "approver_id": str(approver_id),
    }


def binding_hash(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
