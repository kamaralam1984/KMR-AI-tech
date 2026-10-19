"""Repository access behind one small interface.

Two adapters: `LocalGitRepo` (a real git repository on this machine, confined to configured root
directories) and `GitHubRepo` (GitHub's REST API; see github.py). Both read a commit by SHA, so a
repair is always planned against an exact, immutable base. Both can create a NEW branch from that
base. Neither can move, delete or force-update an existing branch, and neither touches the default
branch, a working tree or a remote.
"""

import asyncio
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

MAX_FILE_BYTES = 512 * 1024
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
_ZERO_SHA = "0" * 40
_GIT_TIMEOUT = 30


class RepoError(Exception):
    """A repository operation failed. `code` is stable and safe to store and show."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.message = message or code


class RepoClient(Protocol):
    async def resolve_branch(self, branch: str) -> str: ...
    async def list_files(self, sha: str) -> list[str]: ...
    async def read_file(self, sha: str, path: str) -> str | None: ...
    async def is_clean(self) -> bool: ...
    async def create_branch(
        self, *, branch: str, base_sha: str, changes: Mapping[str, str], message: str
    ) -> str: ...


def valid_branch_name(name: str) -> bool:
    return (
        bool(BRANCH_RE.fullmatch(name))
        and ".." not in name
        and not name.endswith((".lock", "/", "."))
    )


class LocalGitRepo:
    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def open(cls, location: str, roots: list[str]) -> "LocalGitRepo":
        """Opens `location` only if, after resolving symlinks, it is a git repo inside a root."""
        if not roots:
            raise RepoError("local_disabled", "Local repositories are not enabled on this server.")
        try:
            resolved = Path(location).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise RepoError("repo_not_found", "That repository path does not exist.") from exc
        allowed = [Path(r).expanduser().resolve() for r in roots]
        if not any(resolved == r or resolved.is_relative_to(r) for r in allowed):
            raise RepoError(
                "path_not_allowed", "That path is outside the allowed repository folders."
            )
        if not resolved.is_dir() or not (resolved / ".git").exists():
            raise RepoError("not_a_repo", "That path is not a git repository.")
        return cls(resolved)

    async def _git(
        self,
        *args: str,
        stdin: bytes | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
    ) -> tuple[int, bytes]:
        full_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": tempfile.gettempdir(),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            **(env or {}),
        }
        proc = await asyncio.create_subprocess_exec(
            "git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
            "-c", "protocol.ext.allow=never", "-C", str(self.path), *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=full_env,
        )  # fmt: skip
        try:
            out, _ = await asyncio.wait_for(proc.communicate(stdin), _GIT_TIMEOUT)
        except TimeoutError as exc:
            proc.kill()
            raise RepoError("git_timeout", "Git took too long.") from exc
        if check and proc.returncode != 0:
            raise RepoError("git_failed", "A git command failed.")
        return proc.returncode or 0, out

    async def resolve_branch(self, branch: str) -> str:
        if not valid_branch_name(branch):
            raise RepoError("invalid_branch", "That branch name is not valid.")
        code, out = await self._git(
            "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}", check=False
        )
        sha = out.decode().strip()
        if code != 0 or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise RepoError("branch_not_found", "That branch does not exist in the repository.")
        return sha

    async def _tree(self, sha: str) -> dict[str, str]:
        """path -> mode for regular files only (symlinks and submodules are skipped)."""
        _, out = await self._git("ls-tree", "-r", "-z", sha)
        files: dict[str, str] = {}
        for entry in out.split(b"\x00"):
            if not entry:
                continue
            meta, _, name = entry.partition(b"\t")
            mode = meta.split()[0].decode()
            if mode in ("100644", "100755"):
                files[name.decode("utf-8", "replace")] = mode
        return files

    async def list_files(self, sha: str) -> list[str]:
        return sorted(await self._tree(sha))

    async def read_file(self, sha: str, path: str) -> str | None:
        if (await self._tree(sha)).get(path) is None:
            return None
        _, size = await self._git("cat-file", "-s", f"{sha}:{path}")
        if int(size.strip() or b"0") > MAX_FILE_BYTES:
            raise RepoError("file_too_large", "A file is too large to read.")
        _, data = await self._git("cat-file", "blob", f"{sha}:{path}")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RepoError("not_text", "A file is not UTF-8 text.") from exc

    async def is_clean(self) -> bool:
        _, out = await self._git("status", "--porcelain")
        return not out.strip()

    async def create_branch(
        self, *, branch: str, base_sha: str, changes: Mapping[str, str], message: str
    ) -> str:
        """New branch = base + one commit. Built with plumbing in a temporary index, so the
        working tree, HEAD, the index and every existing branch are left exactly as they were."""
        if not valid_branch_name(branch):
            raise RepoError("invalid_branch", "That branch name is not valid.")
        tree_modes = await self._tree(base_sha)
        with tempfile.TemporaryDirectory() as tmp:
            index = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            await self._git("read-tree", base_sha, env=index)
            for path, content in changes.items():
                _, blob = await self._git(
                    "hash-object", "-w", "--stdin", stdin=content.encode("utf-8")
                )
                mode = tree_modes.get(path, "100644")
                entry = f"{mode},{blob.decode().strip()},{path}"
                await self._git("update-index", "--add", "--cacheinfo", entry, env=index)
            _, tree = await self._git("write-tree", env=index)
        identity = {
            "GIT_AUTHOR_NAME": "KMR AI", "GIT_AUTHOR_EMAIL": "kmr-ai@localhost",
            "GIT_COMMITTER_NAME": "KMR AI", "GIT_COMMITTER_EMAIL": "kmr-ai@localhost",
        }  # fmt: skip
        _, commit = await self._git(
            "commit-tree", tree.decode().strip(), "-p", base_sha, "-m", message, env=identity
        )
        sha = commit.decode().strip()
        # The all-zero old value means "only if the branch does not exist yet".
        code, _ = await self._git("update-ref", f"refs/heads/{branch}", sha, _ZERO_SHA, check=False)
        if code != 0:
            raise RepoError("branch_exists", "A branch with that name already exists.")
        return sha


def open_repo(
    provider: str,
    location: str,
    token: str | None,
    *,
    local_roots: list[str],
    github_api_url: str,
    github_transport: "object | None" = None,
) -> RepoClient:
    if provider == "local_git":
        return LocalGitRepo.open(location, local_roots)
    if provider == "github":
        from app.repair.github import GitHubRepo

        if not token:
            raise RepoError("repo_auth", "No access token is stored for this repository.")
        return GitHubRepo(
            location,
            token,
            api_url=github_api_url,
            transport=github_transport,  # type: ignore[arg-type]
        )
    raise RepoError("unsupported_provider", "That repository type is not supported.")
