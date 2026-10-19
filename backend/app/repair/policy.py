"""What a repair is allowed to touch, and the checks every proposed change must pass.

The proposer (rules or a model) is never trusted: whatever it returns is validated here before it
is stored, and again when it is applied. Paths are repository-relative and confined; CI, secrets,
dependency and migration files are out of scope; nothing is deleted; and a change may not add a
credential.
"""

import hashlib
import posixpath
from collections.abc import Mapping
from dataclasses import dataclass, field

from app.repair.secrets import find_secrets

ALLOWED_SUFFIXES = (
    ".html", ".htm", ".css", ".scss", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue",
    ".svelte", ".json", ".md", ".txt", ".py",
)  # fmt: skip
DENIED_DIRS = frozenset(
    {".git", ".github", ".gitlab", ".circleci", ".husky", "node_modules", "migrations", "alembic",
     "versions", "infra", "terraform", "deploy", ".vscode", ".idea"}
)  # fmt: skip
DENIED_NAMES = frozenset(
    {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock",
     "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "makefile", "dockerfile",
     "docker-compose.yml", "docker-compose.yaml", ".gitignore", ".gitattributes", ".npmrc",
     ".pypirc", ".netrc", "id_rsa", "id_ed25519"}
)  # fmt: skip
MAX_PATH = 300


@dataclass
class Finding:
    rule: str
    message: str
    path: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"rule": self.rule, "message": self.message, "path": self.path}


@dataclass
class Validation:
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def add(self, rule: str, message: str, path: str | None = None) -> None:
        self.findings.append(Finding(rule, message, path))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def path_problem(path: object) -> str | None:
    """Why `path` cannot be edited, or None if it can."""
    if not isinstance(path, str) or not path:
        return "The path is empty."
    if len(path) > MAX_PATH or "\x00" in path or "\\" in path:
        return "The path is not a plain repository path."
    if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        return "The path must be relative to the repository."
    if posixpath.normpath(path) != path or any(p in ("", ".", "..") for p in path.split("/")):
        return "The path must not contain '.', '..' or empty parts."
    parts = [p.lower() for p in path.split("/")]
    if any(p in DENIED_DIRS for p in parts[:-1]):
        return "Files in this directory are out of scope for automatic repairs."
    name = parts[-1]
    if name in DENIED_NAMES or name.startswith(".env") or name.endswith((".pem", ".key", ".p12")):
        return "This file is out of scope for automatic repairs."
    if not name.endswith(ALLOWED_SUFFIXES):
        return "Only text source files can be changed."
    return None


def validate_changes(
    changes: Mapping[str, str],
    old_contents: Mapping[str, str | None],
    *,
    max_files: int,
    max_bytes: int,
) -> Validation:
    """Checks a proposal: `changes` maps path -> full new content, `old_contents` the base."""
    result = Validation()
    if not changes:
        result.add("empty", "The proposal changes nothing.")
        return result
    if len(changes) > max_files:
        result.add(
            "too_many_files",
            f"The proposal touches {len(changes)} files; the limit is {max_files}.",
        )
    total = 0
    for path, new in changes.items():
        problem = path_problem(path)
        if problem:
            result.add("path_not_allowed", problem, path if isinstance(path, str) else None)
            continue
        if not isinstance(new, str):
            result.add("invalid_content", "The new content is not text.", path)
            continue
        if "\x00" in new:
            result.add("binary_content", "Binary content is not accepted.", path)
            continue
        total += len(new.encode("utf-8"))
        old = old_contents.get(path)
        if old is not None and old == new:
            result.add("no_change", "The new content equals the current content.", path)
        if new == "" and old:
            result.add(
                "deletion", "Emptying a file is treated as a deletion and is not allowed.", path
            )
        added = set(find_secrets(new)) - set(find_secrets(old or ""))
        if added:
            result.add(
                "adds_secret", f"The change adds a credential ({', '.join(sorted(added))}).", path
            )
    if total > max_bytes:
        result.add("too_large", f"The proposal is {total} bytes; the limit is {max_bytes}.")
    return result
