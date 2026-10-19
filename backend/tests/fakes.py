"""Test doubles. Everything here is a labeled stand-in, never a claim that a real check passed."""

import uuid

import httpx

from app.diagnosis.anthropic_provider import ProviderError
from app.diagnosis.schemas import DiagnosisInput, DiagnosisOutput, ProviderResult, Usage
from app.scanning.api_checks import TransportResult
from app.scanning.ownership import VerificationUnavailable
from app.scanning.queue import QueueUnavailable


class FakeVerifier:
    """STAND-IN for NetworkOwnershipVerifier: answers from dictionaries, never touches a network."""

    def __init__(self) -> None:
        self.txt: dict[str, list[str]] = {}
        self.files: dict[str, str | None] = {}
        self.unavailable = False
        self.calls: list[tuple[str, str]] = []

    async def dns_txt(self, name: str) -> list[str]:
        self.calls.append(("dns", name))
        if self.unavailable:
            raise VerificationUnavailable
        return self.txt.get(name, [])

    async def fetch_file(self, url: str) -> str | None:
        self.calls.append(("file", url))
        if self.unavailable:
            raise VerificationUnavailable
        return self.files.get(url)


class FakeQueue:
    """STAND-IN for ScanQueue that records what would have been queued."""

    def __init__(self) -> None:
        self.enqueued: list[uuid.UUID] = []
        self.broken = False

    async def enqueue(self, scan_id: uuid.UUID) -> None:
        if self.broken:
            raise QueueUnavailable
        self.enqueued.append(scan_id)


class FakeTransport:
    """STAND-IN for ProxyTransport: returns a prepared response and records what was sent."""

    def __init__(self, result: TransportResult | None = None) -> None:
        self.result = result or TransportResult(status=200, body=b"{}", latency_ms=20)
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    async def request(
        self, method: str, url: str, headers: dict[str, str], timeout: float
    ) -> TransportResult:
        self.calls.append((method, url, dict(headers)))
        return self.result


class FakeProvider:
    """STAND-IN for a diagnosis provider: returns a prepared answer (or fails on purpose)."""

    name = "fake-ai"
    model = "fake-model-1"
    config_version = "fake-1"

    def __init__(self, output=None, usage=None, error=None) -> None:  # type: ignore[no-untyped-def]
        self.output = output or DiagnosisOutput(issues=[])
        self.usage = usage or Usage(input_tokens=1000, output_tokens=200)
        self.error = error
        self.inputs: list[DiagnosisInput] = []

    async def diagnose(self, inp: DiagnosisInput) -> ProviderResult:
        self.inputs.append(inp)
        if self.error:
            raise ProviderError(self.error)
        return ProviderResult(output=self.output, usage=self.usage, model=self.model)


class FakeGitHub:
    """A stateful stand-in for the few GitHub REST endpoints the adapter uses.

    NOT GitHub: it only proves the adapter sends sensible calls and handles answers. It records
    every request so tests can assert that no existing ref is ever updated or deleted.
    """

    def __init__(
        self,
        files: dict[str, str],
        *,
        token: str = "ghp_fake_token",  # noqa: S107 - throwaway test value
        branch: str = "main",
    ):
        import hashlib

        self._hashlib = hashlib
        self.token = token
        self.requests: list[tuple[str, str]] = []
        self.blobs: dict[str, str] = {}
        self.trees: dict[str, list[dict[str, str | int]]] = {}
        self.commits: dict[str, dict[str, object]] = {}
        entries = [self._entry(path, text) for path, text in files.items()]
        tree = self._add_tree(entries)
        commit = self._add_commit(tree, [])
        self.refs: dict[str, str] = {branch: commit}
        self.issues: list[dict[str, object]] = []
        self.issues_allowed = True  # False mimics a token without the Issues write permission
        self.transport = httpx.MockTransport(self._handle)

    def _sha(self, *parts: str) -> str:
        return self._hashlib.sha1("\0".join(parts).encode()).hexdigest()  # noqa: S324

    def _entry(self, path: str, text: str) -> dict[str, str | int]:
        sha = self._sha("blob", text)
        self.blobs[sha] = text
        return {"path": path, "mode": "100644", "type": "blob", "sha": sha, "size": len(text)}

    def _add_tree(self, entries: list[dict[str, str | int]]) -> str:
        sha = self._sha("tree", *(f"{e['path']}={e['sha']}" for e in entries))
        self.trees[sha] = entries
        return sha

    def _add_commit(self, tree: str, parents: list[str]) -> str:
        sha = self._sha("commit", tree, *parents, str(len(self.commits)))
        self.commits[sha] = {"tree": {"sha": tree}, "parents": parents}
        return sha

    def _handle(self, request: httpx.Request) -> httpx.Response:
        import base64
        import json as _json

        self.requests.append((request.method, request.url.path))
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"message": "Bad credentials"})
        path = request.url.path.split("/repos/", 1)[1].split("/", 2)[2]
        body = _json.loads(request.content) if request.content else {}
        if request.method == "GET" and path.startswith("git/ref/heads/"):
            branch = path.removeprefix("git/ref/heads/")
            if branch not in self.refs:
                return httpx.Response(404, json={})
            return httpx.Response(200, json={"object": {"sha": self.refs[branch]}})
        if request.method == "GET" and path.startswith("git/trees/"):
            sha = path.removeprefix("git/trees/")
            if sha in self.commits:  # like GitHub, a commit is accepted as a tree-ish
                sha = str(self.commits[sha]["tree"]["sha"])  # type: ignore[index]
            return httpx.Response(200, json={"tree": self.trees[sha], "truncated": False})
        if request.method == "GET" and path.startswith("git/blobs/"):
            text = self.blobs[path.removeprefix("git/blobs/")]
            return httpx.Response(
                200,
                json={"content": base64.b64encode(text.encode()).decode(), "encoding": "base64"},
            )
        if request.method == "GET" and path.startswith("git/commits/"):
            return httpx.Response(200, json=self.commits[path.removeprefix("git/commits/")])
        if request.method == "POST" and path == "git/blobs":
            sha = self._sha("blob", body["content"])
            self.blobs[sha] = body["content"]
            return httpx.Response(201, json={"sha": sha})
        if request.method == "POST" and path == "git/trees":
            merged = {e["path"]: e for e in self.trees[body["base_tree"]]}
            for item in body["tree"]:
                merged[item["path"]] = {**item, "size": len(self.blobs[item["sha"]])}
            return httpx.Response(201, json={"sha": self._add_tree(list(merged.values()))})
        if request.method == "POST" and path == "git/commits":
            return httpx.Response(
                201, json={"sha": self._add_commit(body["tree"], body["parents"])}
            )
        if request.method == "POST" and path == "issues":
            if not self.issues_allowed:
                return httpx.Response(
                    403, json={"message": "Resource not accessible by integration"}
                )
            repo = request.url.path.split("/repos/", 1)[1].rsplit("/issues", 1)[0]
            number = len(self.issues) + 1
            self.issues.append(
                {
                    "number": number,
                    "title": body["title"],
                    "body": body["body"],
                    "keys": sorted(body),
                }
            )
            return httpx.Response(
                201,
                json={"number": number, "html_url": f"https://github.com/{repo}/issues/{number}"},
            )
        if request.method == "POST" and path == "git/refs":
            name = body["ref"].removeprefix("refs/heads/")
            if name in self.refs:
                return httpx.Response(422, json={"message": "Reference already exists"})
            self.refs[name] = body["sha"]
            return httpx.Response(201, json={})
        return httpx.Response(404, json={})
