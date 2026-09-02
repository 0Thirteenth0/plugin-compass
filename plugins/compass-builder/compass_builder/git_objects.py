"""Raw Git commit evidence that cannot be rewritten by graft metadata."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .process_runner import BoundedProcessError, run_bounded


class GitObjectError(ValueError):
    """Raw Git object evidence is missing, malformed, or locally rewritten."""


@dataclass(frozen=True)
class RawCommit:
    oid: str
    parents: tuple[str, ...]


MAX_RAW_COMMIT_BYTES = 1_048_576
MAX_COMMIT_PARENTS = 64
GIT_OBJECT_TIMEOUT_SECONDS = 10
_OID_RE = re.compile(rb"^[0-9a-f]{40}$")
_HEADER_KEY_RE = re.compile(rb"^[a-z][a-z0-9-]*$")
_REQUIRED_HEADERS = {b"tree", b"parent", b"author", b"committer"}
_IDENTITY_RE = re.compile(
    rb"^(author|committer) ([^<>\x00-\x1f\x7f]+) <([^<>\s\x00-\x1f\x7f]+)> "
    rb"(-?[0-9]+) ([+-])([0-9]{2})([0-9]{2})$"
)


def reject_active_grafts(common_git_dir: Path) -> None:
    """Reject every form of the legacy graft surface, including empty/link entries."""

    grafts = Path(common_git_dir) / "info" / "grafts"
    try:
        os.lstat(grafts)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise GitObjectError(f"Git graft metadata cannot be inspected safely: {exc}") from exc
    raise GitObjectError(f"active Git graft metadata is forbidden: {grafts}")


def _run(
    repository: Path,
    arguments: Sequence[str],
    environment: Mapping[str, str] | None,
    *,
    maximum_bytes: int,
) -> bytes:
    try:
        result = run_bounded(
            ["git", "--no-pager", "-C", str(repository), *arguments],
            environment=environment, timeout=GIT_OBJECT_TIMEOUT_SECONDS,
            max_output_bytes=maximum_bytes,
        )
    except (OSError, BoundedProcessError) as exc:
        raise GitObjectError(f"raw Git object evidence is unavailable: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitObjectError(f"raw Git object inspection failed: {detail or arguments[0]}")
    return result.stdout


def _object_type(
    repository: Path, sha: str, environment: Mapping[str, str] | None
) -> bytes:
    return _run(
        repository, ["cat-file", "-t", sha], environment, maximum_bytes=64
    ).rstrip(b"\n")


def _validate_identity(line: bytes, key: bytes, sha: str) -> None:
    match = _IDENTITY_RE.fullmatch(line)
    if match is None or match.group(1) != key:
        raise GitObjectError(f"raw commit {key.decode()} header is malformed: {sha}")
    name, timestamp = match.group(2), match.group(4)
    if name.strip() != name or len(timestamp.lstrip(b"-")) > 20:
        raise GitObjectError(f"raw commit {key.decode()} header is malformed: {sha}")
    if int(match.group(6)) > 23 or int(match.group(7)) > 59:
        raise GitObjectError(f"raw commit {key.decode()} timezone is malformed: {sha}")


def read_raw_commit(
    repository: Path,
    sha: str,
    environment: Mapping[str, str] | None,
    *,
    expected_parent_count: int | None = None,
) -> RawCommit:
    """Read and hash the stored commit bytes; no revision traversal is involved."""

    if len(sha) != 40 or any(character not in "0123456789abcdef" for character in sha):
        raise GitObjectError("raw commit identity must be one lowercase 40-character SHA")
    if _object_type(repository, sha, environment) != b"commit":
        raise GitObjectError(f"Git object is not an exact commit: {sha}")
    size_raw = _run(
        repository, ["cat-file", "-s", sha], environment, maximum_bytes=32
    ).strip()
    if not size_raw.isdigit():
        raise GitObjectError(f"raw commit size is malformed: {sha}")
    size = int(size_raw)
    if size > MAX_RAW_COMMIT_BYTES:
        raise GitObjectError(f"raw commit exceeds the {MAX_RAW_COMMIT_BYTES}-byte bound: {sha}")
    payload = _run(
        repository, ["cat-file", "commit", sha], environment,
        maximum_bytes=MAX_RAW_COMMIT_BYTES,
    )
    if len(payload) != size:
        raise GitObjectError(f"raw commit size changed during inspection: {sha}")
    actual = hashlib.sha1(
        b"commit " + str(len(payload)).encode("ascii") + b"\x00" + payload
    ).hexdigest()
    if actual != sha:
        raise GitObjectError(f"raw commit bytes do not match their claimed identity: {sha}")
    header, separator, _message = payload.partition(b"\n\n")
    if not separator or not header or b"\x00" in header or b"\r" in header:
        raise GitObjectError(f"raw commit header is malformed: {sha}")
    lines = header.split(b"\n")
    headers: list[tuple[bytes, bytes]] = []
    for line in lines:
        if line.startswith(b" "):
            if not headers or headers[-1][0] in _REQUIRED_HEADERS:
                raise GitObjectError(f"raw commit header continuation is malformed: {sha}")
            continue
        key, separator, value = line.partition(b" ")
        if not separator or not value or _HEADER_KEY_RE.fullmatch(key) is None:
            raise GitObjectError(f"raw commit header line is malformed: {sha}")
        headers.append((key, value))
    if not headers or headers[0][0] != b"tree":
        raise GitObjectError(f"raw commit tree header must be first: {sha}")
    tree_value = headers[0][1]
    if _OID_RE.fullmatch(tree_value) is None:
        raise GitObjectError(f"raw commit tree header is malformed: {sha}")
    index = 1
    parent_values: list[bytes] = []
    while index < len(headers) and headers[index][0] == b"parent":
        parent_values.append(headers[index][1])
        index += 1
    if index >= len(headers) or headers[index][0] != b"author":
        raise GitObjectError(
            f"raw commit requires canonical author and committer order after parents: {sha}"
        )
    author = headers[index][1]
    index += 1
    if index >= len(headers) or headers[index][0] != b"committer":
        raise GitObjectError(
            f"raw commit requires canonical author and committer order after parents: {sha}"
        )
    committer = headers[index][1]
    index += 1
    if any(key in _REQUIRED_HEADERS for key, _value in headers[index:]):
        raise GitObjectError(f"raw commit required header appears outside canonical order: {sha}")
    _validate_identity(b"author " + author, b"author", sha)
    _validate_identity(b"committer " + committer, b"committer", sha)
    tree = tree_value.decode("ascii")
    if _object_type(repository, tree, environment) != b"tree":
        raise GitObjectError(f"raw commit tree does not reference a tree object: {sha}")
    parents: list[str] = []
    for value in parent_values:
        if _OID_RE.fullmatch(value) is None:
            raise GitObjectError(f"raw commit parent header is malformed: {sha}")
        parents.append(value.decode("ascii"))
    parent_bound = MAX_COMMIT_PARENTS if expected_parent_count is None else expected_parent_count
    if len(parents) != parent_bound and expected_parent_count is not None:
        if expected_parent_count == 1:
            raise GitObjectError(f"raw commit is not one non-merge commit: {sha}")
        raise GitObjectError(
            f"raw commit requires exactly {expected_parent_count} parent(s): {sha}"
        )
    if len(parents) > parent_bound:
        raise GitObjectError(f"raw commit parent count exceeds its inspection bound: {sha}")
    for parent in parents:
        if _object_type(repository, parent, environment) != b"commit":
            raise GitObjectError(f"raw commit parent is not a commit object: {sha}")
    return RawCommit(oid=actual, parents=tuple(parents))


__all__ = [
    "GIT_OBJECT_TIMEOUT_SECONDS", "GitObjectError", "MAX_RAW_COMMIT_BYTES",
    "RawCommit", "read_raw_commit", "reject_active_grafts",
]
