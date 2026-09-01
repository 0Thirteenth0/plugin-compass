"""Exclusive integration-branch leases with fail-closed stale handling."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ._validation import branch as validate_branch
from ._validation import digest as validate_digest
from ._validation import identifier


class LeaseError(ValueError):
    """An integration lease is contended, malformed, stale, or not owned."""


LEASE_VERSION = "compass-builder.integration-lease.v1"
_FIELDS = {
    "schemaVersion", "leaseKey", "commonGitDir", "integrationBranch",
    "ownerId", "ownerPid", "acquiredAt", "expiresAt", "evidenceDigest", "token",
}


@dataclass(frozen=True)
class LeaseHandle:
    path: Path
    record: dict[str, object]
    file_identity: tuple[int, int]


def _is_reparse(path: Path) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    stat = path.lstat()
    return path.is_symlink() or bool(
        getattr(stat, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _canonical(value: dict[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _timestamp(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise LeaseError(f"{field} must be a UTC RFC 3339 timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LeaseError(f"{field} is malformed") from exc
    return parsed


def _canonical_common_dir(path: Path) -> Path:
    raw = Path(path)
    if not raw.is_absolute():
        raise LeaseError("Git common directory must be absolute")
    if _is_reparse(raw):
        raise LeaseError("Git common directory may not be a symlink or reparse point")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise LeaseError(f"Git common directory is unavailable: {exc}") from exc
    if not resolved.is_dir() or _is_reparse(resolved):
        raise LeaseError("Git common directory must be a real directory")
    return resolved


def _key(common: Path, integration_branch: str) -> str:
    identity = json.dumps(
        {"commonGitDir": str(common), "integrationBranch": integration_branch},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(identity).hexdigest()


@contextmanager
def _lease_guard(root: Path, lease_key: str):
    """Serialize all cooperative operations for one branch lease."""

    guard = root / (lease_key.removeprefix("sha256:") + ".guard")
    try:
        guard.mkdir()
    except FileExistsError as exc:
        raise LeaseError("lease operation is already in progress") from exc
    except OSError as exc:
        raise LeaseError(f"lease serialization failed: {exc}") from exc
    try:
        yield
    finally:
        try:
            guard.rmdir()
        except OSError as exc:
            raise LeaseError(f"lease serialization cleanup failed: {exc}") from exc


def _before_tombstone_delete(path: Path) -> None:
    """Test seam immediately before conditional tombstone deletion."""


def _validate(record: object, expected_key: str | None = None) -> dict[str, object]:
    if not isinstance(record, dict) or set(record) != _FIELDS:
        raise LeaseError("lease record is malformed or has an unsupported field set")
    if record["schemaVersion"] != LEASE_VERSION:
        raise LeaseError("lease record has an unsupported version")
    common = _canonical_common_dir(Path(str(record["commonGitDir"])))
    try:
        validate_branch(record["integrationBranch"], "integrationBranch")
        identifier(record["ownerId"], "ownerId")
        validate_digest(record["evidenceDigest"], "evidenceDigest")
    except ValueError as exc:
        raise LeaseError(str(exc)) from exc
    if type(record["ownerPid"]) is not int or record["ownerPid"] < 1:
        raise LeaseError("ownerPid must be a positive integer")
    acquired = _timestamp(record["acquiredAt"], "acquiredAt")
    expires = _timestamp(record["expiresAt"], "expiresAt")
    if expires <= acquired:
        raise LeaseError("expiresAt must be later than acquiredAt")
    token = record["token"]
    if not isinstance(token, str) or len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
        raise LeaseError("lease token must contain 256 bits of lowercase hexadecimal entropy")
    calculated = _key(common, str(record["integrationBranch"]))
    if record["leaseKey"] != calculated or (expected_key is not None and calculated != expected_key):
        raise LeaseError("lease key does not bind the canonical Git common directory and branch")
    return record


def acquire_lease(
    common_git_dir: Path,
    integration_branch: str,
    *,
    owner_id: str,
    evidence_digest: str,
    acquired_at: str,
    expires_at: str,
    owner_pid: int | None = None,
    token: str | None = None,
) -> LeaseHandle:
    """Acquire by exclusive creation; existing records are never stolen."""

    common = _canonical_common_dir(common_git_dir)
    try:
        validate_branch(integration_branch, "integrationBranch")
        identifier(owner_id, "ownerId")
        validate_digest(evidence_digest, "evidenceDigest")
    except ValueError as exc:
        raise LeaseError(str(exc)) from exc
    lease_key = _key(common, integration_branch)
    root = common / "compass-builder-leases"
    root.mkdir(exist_ok=True)
    if _is_reparse(root):
        raise LeaseError("lease directory may not be a reparse point")
    path = root / (lease_key.removeprefix("sha256:") + ".json")
    record = _validate({
        "schemaVersion": LEASE_VERSION,
        "leaseKey": lease_key,
        "commonGitDir": str(common),
        "integrationBranch": integration_branch,
        "ownerId": owner_id,
        "ownerPid": os.getpid() if owner_pid is None else owner_pid,
        "acquiredAt": acquired_at,
        "expiresAt": expires_at,
        "evidenceDigest": evidence_digest,
        "token": token or secrets.token_hex(32),
    }, lease_key)
    with _lease_guard(root, lease_key):
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            existing = _inspect_unlocked(path, lease_key, acquired_at)
            raise LeaseError(
                f"integration branch is already leased by {existing['ownerId']!r}"
            ) from exc
        except OSError as exc:
            raise LeaseError(f"exclusive lease acquisition failed: {exc}") from exc
        file_identity: tuple[int, int]
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(_canonical(record))
                stream.flush()
                os.fsync(stream.fileno())
                stat = os.fstat(stream.fileno())
                file_identity = (stat.st_dev, stat.st_ino)
        except BaseException:
            try:
                path.unlink()
            except OSError:
                pass
            raise
    return LeaseHandle(path=path, record=record, file_identity=file_identity)


def inspect_lease(
    common_git_dir: Path,
    integration_branch: str,
    *,
    now: str | None,
) -> dict[str, object]:
    common = _canonical_common_dir(common_git_dir)
    try:
        validate_branch(integration_branch, "integrationBranch")
    except ValueError as exc:
        raise LeaseError(str(exc)) from exc
    lease_key = _key(common, integration_branch)
    root = common / "compass-builder-leases"
    path = root / (lease_key.removeprefix("sha256:") + ".json")
    with _lease_guard(root, lease_key):
        return _inspect_unlocked(path, lease_key, now)


def _inspect_unlocked(
    path: Path, lease_key: str, now: str | None,
) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise LeaseError(f"existing lease is unreadable or malformed: {exc}") from exc
    record = _validate(value, lease_key)
    if now is not None and _timestamp(now, "now") >= _timestamp(str(record["expiresAt"]), "expiresAt"):
        raise LeaseError(
            "existing lease is stale; v1 has no safe automatic stale-recovery contract"
        )
    return record


def release_lease(handle: LeaseHandle) -> None:
    """Release only when the complete durable record still matches this owner."""

    if not isinstance(handle, LeaseHandle):
        raise LeaseError("release requires the exact acquired lease handle")
    expected = _validate(dict(handle.record))
    root = handle.path.parent
    lease_key = str(expected["leaseKey"])
    tombstone = root / (
        lease_key.removeprefix("sha256:") + f".release-{expected['token']}"
    )
    with _lease_guard(root, lease_key):
        if tombstone.exists() or tombstone.is_symlink():
            raise LeaseError("lease release tombstone already exists")
        try:
            os.rename(handle.path, tombstone)
        except OSError as exc:
            raise LeaseError(f"lease could not enter conditional release: {exc}") from exc
        try:
            opened = os.stat(tombstone, follow_symlinks=False)
            if (opened.st_dev, opened.st_ino) != handle.file_identity:
                raise LeaseError("lease file identity changed before conditional release")
            current = json.loads(tombstone.read_text(encoding="utf-8-sig"))
            actual = _validate(current, lease_key)
            if _canonical(actual) != _canonical(expected):
                raise LeaseError("lease ownership or evidence changed; refusing release")
            _before_tombstone_delete(tombstone)
            if handle.path.exists() or handle.path.is_symlink():
                raise LeaseError("a replacement lease appeared during release")
            tombstone.unlink()
        except (LeaseError, OSError, UnicodeError, ValueError) as exc:
            if tombstone.exists() and not handle.path.exists():
                try:
                    os.rename(tombstone, handle.path)
                except OSError:
                    pass
            if isinstance(exc, LeaseError):
                raise
            raise LeaseError(f"lease release failed closed: {exc}") from exc


__all__ = [
    "LEASE_VERSION", "LeaseError", "LeaseHandle", "acquire_lease", "inspect_lease",
    "release_lease",
]
