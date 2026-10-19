"""Unified diff of a proposal against its base, and the hash an approval is bound to."""

import difflib
from collections.abc import Mapping

from app.repair.policy import sha256_text


def build_diff(old_contents: Mapping[str, str | None], changes: Mapping[str, str]) -> str:
    """Deterministic unified diff (paths sorted); `None` in old_contents means a new file."""
    chunks: list[str] = []
    for path in sorted(changes):
        old = old_contents.get(path)
        old_lines = (old or "").splitlines(keepends=True)
        new_lines = changes[path].splitlines(keepends=True)
        chunks.extend(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{path}" if old is not None else "/dev/null",
                tofile=f"b/{path}",
            )
        )
        if chunks and not chunks[-1].endswith("\n"):
            chunks.append("\n\\ No newline at end of file\n")
    return "".join(chunks)


def diff_hash(diff: str) -> str:
    return sha256_text(diff)
