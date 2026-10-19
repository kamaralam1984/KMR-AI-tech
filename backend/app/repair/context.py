"""Reads the repository files a proposer may see.

Only editable text files are read (see policy.py); a file that contains a credential is never read
into a proposer's input, and is reported as excluded so nobody is told the whole repo was used.
"""

from dataclasses import dataclass, field

from app.repair.policy import path_problem
from app.repair.repo import RepoClient, RepoError
from app.repair.secrets import find_secrets

MAX_FILES_LISTED = 400
MAX_FILE_CHARS = 100_000
MAX_TOTAL_CHARS = 300_000


@dataclass
class Snapshot:
    files: dict[str, str] = field(default_factory=dict)
    excluded: list[dict[str, str]] = field(default_factory=list)


async def load_snapshot(repo: RepoClient, sha: str) -> Snapshot:
    snapshot = Snapshot()
    total = 0
    paths = [p for p in await repo.list_files(sha) if path_problem(p) is None]
    if len(paths) > MAX_FILES_LISTED:
        snapshot.excluded.append(
            {"path": "*", "reason": f"only the first {MAX_FILES_LISTED} editable files were read"}
        )
        paths = paths[:MAX_FILES_LISTED]
    for path in paths:
        try:
            text = await repo.read_file(sha, path)
        except RepoError as exc:
            snapshot.excluded.append({"path": path, "reason": exc.code})
            continue
        if text is None:
            continue
        if len(text) > MAX_FILE_CHARS:
            snapshot.excluded.append({"path": path, "reason": "too_large"})
            continue
        if find_secrets(text):
            snapshot.excluded.append({"path": path, "reason": "contains_secret"})
            continue
        if total + len(text) > MAX_TOTAL_CHARS:
            snapshot.excluded.append({"path": path, "reason": "context_limit"})
            continue
        snapshot.files[path] = text
        total += len(text)
    return snapshot


MAX_TREE_FILES = 2000
MAX_TREE_BYTES = 20 * 1024 * 1024


async def export_tree(repo: RepoClient, sha: str) -> dict[str, str]:
    """Every UTF-8 text file of a commit, for running checks on a copy. Binary files are left out
    (and so are files over the per-file limit); a project check that needs them will say so."""
    files: dict[str, str] = {}
    total = 0
    for path in (await repo.list_files(sha))[:MAX_TREE_FILES]:
        try:
            text = await repo.read_file(sha, path)
        except RepoError:
            continue
        if text is None:
            continue
        total += len(text.encode("utf-8"))
        if total > MAX_TREE_BYTES:
            break
        files[path] = text
    return files
