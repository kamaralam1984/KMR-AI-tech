"""Where screenshots live. Local disk for now; the interface is what the rest of the app uses.

Files are named only from ids the server generated, and every read re-validates the stored key,
so a bad value in the database can never turn into a path outside the evidence directory.
"""

import os
import re
import uuid
from pathlib import Path

_KEY = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.png$"
)


class EvidenceStore:
    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir

    @staticmethod
    def key_for(org_id: uuid.UUID, scan_id: uuid.UUID, evidence_id: uuid.UUID) -> str:
        return f"{org_id}/{scan_id}/{evidence_id}.png"

    def _path(self, key: str) -> Path:
        if not _KEY.match(key):
            raise ValueError("invalid storage key")
        path = (self._base / key).resolve()
        if not path.is_relative_to(self._base.resolve()):
            raise ValueError("storage key escapes the evidence directory")
        return path

    def save(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        temporary.replace(path)  # atomic: readers never see a half-written file

    def read(self, key: str) -> bytes | None:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
