"""Shared primitive validation and deterministic JSON helpers."""

from __future__ import annotations

import json
import re
from datetime import datetime
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}-[0-9a-f]{16,64}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
BRANCH_RE = re.compile(r"^(?![-/])(?!.*(?:\.\.|//|@\{|\\|\s))[^~^:?*\[\]]+(?<![/.])$")
RFC3339_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
EFFORTS = {"low", "medium", "high", "xhigh", "max", "ultra"}
MAX_ARRAY_ITEMS = 16_384
WINDOWS_FORBIDDEN_RE = re.compile(r'[\x00-\x1f\x7f:<>"|?*]')
WINDOWS_RESERVED_RE = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.IGNORECASE)


class ContractValidationError(ValueError):
    """A field-addressed contract failure with an actionable correction."""


def fail(path: str, reason: str, correction: str) -> None:
    raise ContractValidationError(
        f"{path}: {reason}; corrective direction: {correction}"
    )


def object_(
    value: Any, path: str, required: set[str], optional: set[str] | None = None
) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(path, "must be a JSON object", "provide an object with the documented fields")
    non_string_keys = [key for key in value if not isinstance(key, str)]
    if non_string_keys:
        fail(path, "contains a non-string JSON object key", "use string field names only")
    optional = optional or set()
    missing = sorted(required - value.keys())
    extra = sorted(value.keys() - required - optional)
    if missing:
        fail(path, f"missing required field(s): {', '.join(missing)}", "add every required field")
    if extra:
        fail(path, f"unknown field(s): {', '.join(extra)}", "remove undeclared fields")
    return value


def string(value: Any, path: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(path, "must be a non-empty string", "provide a non-empty string")
    if value != value.strip():
        fail(path, "must not contain leading or trailing whitespace", "trim the value")
    if len(value) > maximum:
        fail(path, f"exceeds the {maximum}-character bound", "shorten the value")
    return value


def enum(value: Any, path: str, allowed: set[str]) -> str:
    value = string(value, path, maximum=128)
    if value not in allowed:
        fail(path, f"unsupported value {value!r}", f"choose one of {sorted(allowed)}")
    return value


def integer(value: Any, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        fail(path, "must be an integer", f"provide an integer >= {minimum}")
    if value < minimum:
        fail(path, f"must be >= {minimum}", f"raise the value to at least {minimum}")
    return value


def boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        fail(path, "must be a boolean", "use true or false")
    return value


def array(
    value: Any, path: str, *, minimum: int = 0, maximum: int = MAX_ARRAY_ITEMS
) -> list[Any]:
    if not isinstance(value, list):
        fail(path, "must be an array", "provide an ordered JSON array")
    if len(value) < minimum:
        fail(path, f"must contain at least {minimum} item(s)", "add the required ordered items")
    if len(value) > maximum:
        fail(path, f"must contain at most {maximum} item(s)", "reduce the collection to the versioned MVP bound")
    return value


def strings(
    value: Any, path: str, *, minimum: int = 0, maximum: int = 4096,
    items_maximum: int = MAX_ARRAY_ITEMS,
) -> list[str]:
    result = [
        string(item, f"{path}[{index}]", maximum=maximum)
        for index, item in enumerate(array(value, path, minimum=minimum, maximum=items_maximum))
    ]
    if len(set(result)) != len(result):
        fail(path, "contains duplicate values", "remove duplicates while preserving order")
    return result


def identifier(value: Any, path: str) -> str:
    value = string(value, path, maximum=64)
    if not ID_RE.fullmatch(value):
        fail(path, "is not a stable lowercase identifier", "use 1-64 lowercase letters, digits, '.', '_' or '-'")
    return value


def sha(value: Any, path: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        fail(path, "must be a lowercase immutable 40-hex Git SHA", "resolve and record the full Git object ID")
    return value


def digest(value: Any, path: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not DIGEST_RE.fullmatch(value):
        fail(path, "must be a sha256: digest with 64 lowercase hex digits", "compute and record the canonical SHA-256 digest")
    return value


def run_id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not RUN_ID_RE.fullmatch(value):
        fail(path, "must end in at least 64 bits of lowercase hexadecimal entropy", "generate an identifier such as cb-<timestamp>-<16+ hex entropy>")
    return value


def branch(value: Any, path: str) -> str:
    value = string(value, path, maximum=240)
    if (
        not BRANCH_RE.fullmatch(value)
        or value == "@"
        or WINDOWS_FORBIDDEN_RE.search(value)
        or any(
            part.startswith(".")
            or part.casefold().endswith(".lock")
            or part.endswith((".", " "))
            or WINDOWS_RESERVED_RE.fullmatch(part)
            for part in value.split("/")
        )
    ):
        fail(path, "is not a safe portable Git branch name", "use check-ref-format-safe segments without controls, Windows metacharacters, trailing dots/spaces, or device names")
    return value


def timestamp(value: Any, path: str) -> datetime:
    value = string(value, path, maximum=64)
    if not RFC3339_RE.fullmatch(value):
        fail(path, "must be an RFC 3339 timestamp", "record an offset-aware timestamp using T and Z or an explicit offset")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        fail(path, "must be an RFC 3339 timestamp", "record an offset-aware ISO timestamp")
    if parsed.tzinfo is None:
        fail(path, "must include a timezone", "record Z or an explicit UTC offset")
    return parsed


def scope(value: Any, path: str) -> str:
    value = string(value, path, maximum=512)
    if "\\" in value:
        fail(path, "uses a Windows separator", "use repository-relative forward-slash paths")
    if value.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", value):
        fail(path, "is absolute, drive-qualified, or UNC", "use a repository-relative path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        fail(path, "contains traversal, empty, or dot segments", "use normalized repository-relative segments")
    if WINDOWS_FORBIDDEN_RE.search(value):
        fail(path, "contains a control, NTFS ADS colon, or Windows metacharacter", "use portable repository-relative path segments")
    if any(part.endswith((".", " ")) for part in parts):
        fail(path, "contains a segment ending in a dot or space", "remove trailing dots and spaces from every segment")
    if any(WINDOWS_RESERVED_RE.fullmatch(part) for part in parts):
        fail(path, "contains a reserved Windows device name", "rename CON, PRN, AUX, NUL, COM1-9, or LPT1-9 segments and aliases")
    if PurePosixPath(value).as_posix() != value:
        fail(path, "is not normalized", "use normalized forward-slash segments")
    return value


def canonical_data(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def canonical_digest(value: Any) -> str:
    return "sha256:" + sha256(canonical_data(value)).hexdigest()
