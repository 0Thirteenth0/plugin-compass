"""Closed ownership policy for controller-persisted run artifacts."""

from __future__ import annotations

from collections.abc import Collection
import hashlib
import json
import re
from pathlib import Path
from typing import Mapping

from .models import canonical_json
from .secure_files import (
    SecureFileError, is_reparse, read_no_follow, reject_reparse_components,
    require_contained, write_new_no_follow,
)


REQUIRED = frozenset({"transaction.json", "controller.json", "state.json"})
OPTIONAL = frozenset({
    "plan-bundle.json", "git-environment", "launch-records", "failure-records",
    "cleanup-progress", "merge-intents",
})
DIRECTORIES = frozenset({
    "git-environment", "launch-records", "failure-records", "cleanup-progress",
    "merge-intents",
})
MAX_RECORDS = 1024
MAX_RECORD_BYTES = 1_048_576
MAX_AGGREGATE_BYTES = 16_777_216
_RECEIPT_NAME = re.compile(r"^[0-9a-f]{64}\.json$")


def accepts(names: Collection[str], *, require_bundle: bool = False) -> bool:
    present = set(names)
    required = REQUIRED | ({"plan-bundle.json"} if require_bundle else set())
    return required <= present and present <= REQUIRED | OPTIONAL


class ArtifactJournal:
    """Append/read immutable auxiliary evidence beneath one validated run root."""

    def __init__(self, run_root: Path, controller_root: Path):
        self.root = Path(run_root).absolute()
        self.controller_root = Path(controller_root).absolute()
        require_contained(self.root, self.controller_root, label="durable run root")

    def _directory(self, name: str, *, create: bool) -> Path:
        if name not in DIRECTORIES - {"git-environment", "launch-records"}:
            raise ValueError("unsupported auxiliary artifact directory")
        directory = self.root / name
        if create:
            require_contained(directory, self.controller_root, label=f"durable {name}")
            reject_reparse_components(directory.parent, label=f"durable {name}")
            directory.mkdir(exist_ok=True)
        require_contained(directory, self.controller_root, label=f"durable {name}")
        if not directory.is_dir() or is_reparse(directory):
            raise ValueError(f"durable {name} directory is unsafe")
        return directory

    def _entries(self, directory: Path) -> tuple[tuple[Path, bytes], ...]:
        entries = []
        total = 0
        for path in directory.iterdir():
            if len(entries) >= MAX_RECORDS:
                raise ValueError(f"durable {directory.name} receipt collection exceeds its bound")
            if not _RECEIPT_NAME.fullmatch(path.name):
                raise ValueError(f"durable {directory.name} contains an unknown entry")
            reject_reparse_components(path, label=f"durable {directory.name} receipt")
            size = path.lstat().st_size
            if size > MAX_RECORD_BYTES:
                raise ValueError(f"durable {directory.name} receipt exceeds its byte bound")
            total += size
            if total > MAX_AGGREGATE_BYTES:
                raise ValueError(f"durable {directory.name} receipt collection exceeds its bound")
            payload = read_no_follow(
                path, self.controller_root, label=f"durable {directory.name} receipt",
                max_bytes=MAX_RECORD_BYTES,
            )
            if hashlib.sha256(payload).hexdigest() != path.stem:
                raise ValueError(f"durable {directory.name} receipt digest does not match its filename")
            entries.append((path, payload))
        return tuple(sorted(entries, key=lambda item: item[0].name))

    def record(self, name: str, record: Mapping[str, object]) -> Path:
        directory = self._directory(name, create=True)
        payload = canonical_json(record)
        if len(payload) > MAX_RECORD_BYTES:
            raise ValueError(f"durable {name} receipt exceeds its byte bound")
        path = directory / f"{hashlib.sha256(payload).hexdigest()}.json"
        entries = self._entries(directory)
        if path.exists():
            if read_no_follow(
                path, self.controller_root, label=f"durable {name} receipt",
                max_bytes=MAX_RECORD_BYTES,
            ) != payload:
                raise ValueError(f"durable {name} receipt changed")
            return path
        if len(entries) >= MAX_RECORDS or sum(len(existing) for _path, existing in entries) + len(payload) > MAX_AGGREGATE_BYTES:
            raise ValueError(f"durable {name} receipt collection exceeds its bound")
        write_new_no_follow(
            path, payload, self.controller_root, label=f"durable {name} receipt"
        )
        return path

    def read(self, name: str) -> tuple[dict[str, object], ...]:
        directory = self.root / name
        if not directory.exists():
            return ()
        directory = self._directory(name, create=False)
        records = []
        for path, payload in self._entries(directory):
            value = json.loads(payload.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError(f"durable {name} receipt is malformed")
            records.append(value)
        return tuple(records)


__all__ = [
    "ArtifactJournal", "DIRECTORIES", "MAX_AGGREGATE_BYTES", "MAX_RECORD_BYTES",
    "MAX_RECORDS", "OPTIONAL", "REQUIRED", "accepts",
]
