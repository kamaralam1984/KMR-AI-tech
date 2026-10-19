"""GitHub adapter (REST, with a token or an installation token from a GitHub App).

NOT verified against the real GitHub in this repository: no credentials were available, so it is
tested against a stateful fake that mimics the few endpoints used (tests/fakes.py). Treat it as
unproven until it has run against a real test repository.

It only reads a commit by SHA and creates a new branch via the Git Data API (blobs, tree, commit,
ref). It never updates an existing ref, so the default branch cannot be moved by it.
"""

import base64
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

from app.repair.repo import MAX_FILE_BYTES, RepoError, valid_branch_name

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


class GitHubRepo:
    def __init__(
        self,
        location: str,
        token: str,
        *,
        api_url: str = "https://api.github.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not _REPO_RE.fullmatch(location):
            raise RepoError("invalid_repo", "Use the form owner/name.")
        self.repo = location
        self._http = httpx.AsyncClient(
            base_url=api_url.rstrip("/"),
            transport=transport,
            timeout=20,
            follow_redirects=False,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "kmr-ai",
            },
        )

    async def _call(
        self, method: str, path: str, *, json: Any = None, ok: tuple[int, ...] = (200,)
    ) -> Any:
        try:
            response = await self._http.request(method, f"/repos/{self.repo}{path}", json=json)
        except httpx.HTTPError as exc:
            raise RepoError("repo_unreachable", "GitHub could not be reached.") from exc
        if response.status_code in ok:
            return response.json()
        if response.status_code in (401, 403):
            raise RepoError("repo_auth", "GitHub rejected the access token.")
        if response.status_code == 404:
            raise RepoError("repo_not_found", "The repository or branch was not found.")
        if response.status_code == 422:
            raise RepoError("branch_exists", "GitHub refused the request (it may already exist).")
        raise RepoError("repo_error", "GitHub returned an unexpected answer.")

    async def create_issue(self, *, title: str, body: str) -> tuple[str, int]:
        """Opens one issue. Needs the Issues write permission, which repair does not need.

        Only a title and body are sent: no labels, assignees or milestone. Returns (url, number).
        """
        try:
            data = await self._call(
                "POST", "/issues", json={"title": title[:250], "body": body[:60000]}, ok=(201,)
            )
        except RepoError as exc:
            if exc.code == "repo_auth":
                raise RepoError(
                    "issues_forbidden",
                    "GitHub refused to create the issue. The access token or GitHub App needs "
                    "the 'Issues: Read and write' permission for this repository.",
                ) from exc
            raise
        url, number = str(data.get("html_url", "")), int(data.get("number", 0))
        if not url.startswith("https://") or number <= 0:
            raise RepoError("repo_error", "GitHub returned an unexpected answer.")
        return url, number

    async def resolve_branch(self, branch: str) -> str:
        if not valid_branch_name(branch):
            raise RepoError("invalid_branch", "That branch name is not valid.")
        data = await self._call("GET", f"/git/ref/heads/{quote(branch, safe='/')}")
        sha = str(data["object"]["sha"])
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise RepoError("repo_error", "GitHub returned an unexpected answer.")
        return sha

    async def _tree(self, sha: str) -> dict[str, str]:
        data = await self._call("GET", f"/git/trees/{sha}?recursive=1")
        if data.get("truncated"):
            raise RepoError("tree_truncated", "The repository is too large to list.")
        return {
            e["path"]: e["mode"]
            for e in data["tree"]
            if e.get("type") == "blob" and e.get("mode") in ("100644", "100755")
        }

    async def list_files(self, sha: str) -> list[str]:
        return sorted(await self._tree(sha))

    async def read_file(self, sha: str, path: str) -> str | None:
        entry = next((e for e in (await self._tree_entries(sha)) if e["path"] == path), None)
        if entry is None:
            return None
        if int(entry.get("size", 0)) > MAX_FILE_BYTES:
            raise RepoError("file_too_large", "A file is too large to read.")
        blob = await self._call("GET", f"/git/blobs/{entry['sha']}")
        try:
            return base64.b64decode(blob["content"]).decode("utf-8")
        except (UnicodeDecodeError, ValueError, KeyError) as exc:
            raise RepoError("not_text", "A file is not UTF-8 text.") from exc

    async def _tree_entries(self, sha: str) -> list[dict[str, Any]]:
        data = await self._call("GET", f"/git/trees/{sha}?recursive=1")
        entries: list[dict[str, Any]] = [e for e in data["tree"] if e.get("type") == "blob"]
        return entries

    async def is_clean(self) -> bool:
        return True  # a remote repository has no working tree

    async def create_branch(
        self, *, branch: str, base_sha: str, changes: Mapping[str, str], message: str
    ) -> str:
        if not valid_branch_name(branch):
            raise RepoError("invalid_branch", "That branch name is not valid.")
        modes = await self._tree(base_sha)
        entries = []
        for path, content in changes.items():
            blob = await self._call(
                "POST",
                "/git/blobs",
                json={"content": content, "encoding": "utf-8"},
                ok=(201,),
            )
            entries.append(
                {
                    "path": path,
                    "mode": modes.get(path, "100644"),
                    "type": "blob",
                    "sha": blob["sha"],
                }
            )
        base = await self._call("GET", f"/git/commits/{base_sha}")
        tree = await self._call(
            "POST",
            "/git/trees",
            json={"base_tree": base["tree"]["sha"], "tree": entries},
            ok=(201,),
        )
        commit = await self._call(
            "POST",
            "/git/commits",
            json={"message": message, "tree": tree["sha"], "parents": [base_sha]},
            ok=(201,),
        )
        await self._call(
            "POST",
            "/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
            ok=(201,),
        )
        return str(commit["sha"])

    async def aclose(self) -> None:
        await self._http.aclose()
