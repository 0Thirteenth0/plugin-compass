"""Bounded parser for authoritative usage on a direct Codex JSONL stream."""

from __future__ import annotations

import copy
import json
from typing import Any, Mapping

from ._usage_models import MAX_TOKEN_COUNT, TERMINAL_STATUSES
from ._validation import (
    branch,
    canonical_data,
    canonical_digest,
    digest,
    enum,
    fail,
    identifier,
    integer,
    object_,
    run_id,
    sha,
    string,
)
from .models import validate_worker_receipt, validate_worker_usage


MAX_USAGE_STREAM_BYTES = 1_048_576
MAX_USAGE_STREAM_LINES = 16_384
SOURCE = "codex-exec-jsonl-stdout"
_USAGE_IDENTITY_FIELDS = {
    "runId", "storyId", "attempt", "exactModel", "effort", "launchDigest",
}
_RECEIPT_BINDING_FIELDS = {"branch", "worktree", "workerStartSha"}
_FULL_IDENTITY_FIELDS = _USAGE_IDENTITY_FIELDS | _RECEIPT_BINDING_FIELDS
_RAW_REQUIRED = {
    "input_tokens", "cached_input_tokens", "output_tokens",
    "reasoning_output_tokens",
}
_RAW_OPTIONAL = {"cache_write_input_tokens"}


class _MalformedJsonl(ValueError):
    pass


def _reject_constant(value: str) -> None:
    raise _MalformedJsonl(f"non-finite JSON constant {value!r}")


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _MalformedJsonl(f"duplicate JSON object key {key!r}")
        value[key] = item
    return value


def _identity(
    value: Mapping[str, Any], *, require_receipt_binding: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        fail("launchIdentity", "must be an object mapping", "provide validated launch identity fields")
    try:
        result = copy.deepcopy(dict(value))
    except (TypeError, ValueError):
        fail("launchIdentity", "must be a copyable object mapping", "provide JSON-compatible launch identity fields")
    if require_receipt_binding:
        object_(result, "launchIdentity", _FULL_IDENTITY_FIELDS)
    else:
        object_(
            result, "launchIdentity", _USAGE_IDENTITY_FIELDS,
            _RECEIPT_BINDING_FIELDS,
        )
        supplied_binding = set(result) & _RECEIPT_BINDING_FIELDS
        if supplied_binding and supplied_binding != _RECEIPT_BINDING_FIELDS:
            fail(
                "launchIdentity",
                "contains a partial worker receipt binding",
                "provide branch, worktree, and workerStartSha together or omit all three",
            )
    run_id(result["runId"], "launchIdentity.runId")
    identifier(result["storyId"], "launchIdentity.storyId")
    attempt = integer(result["attempt"], "launchIdentity.attempt", minimum=1)
    if attempt > 2:
        fail("launchIdentity.attempt", "must be launch attempt 1 or 2", "copy the validated launch attempt")
    # The worker-usage contract applies the exact model and effort rules below.
    digest(result["launchDigest"], "launchIdentity.launchDigest")
    if _RECEIPT_BINDING_FIELDS <= set(result):
        branch(result["branch"], "launchIdentity.branch")
        string(result["worktree"], "launchIdentity.worktree", maximum=1024)
        sha(result["workerStartSha"], "launchIdentity.workerStartSha")
    probe = {
        "schemaVersion": "compass-builder.worker-usage.v1",
        "source": SOURCE,
        **{field: result[field] for field in _USAGE_IDENTITY_FIELDS},
        "workerReceiptDigest": None,
        "terminalStatus": "succeeded",
        "observed": False,
        "unavailableReason": "no-terminal-usage",
        "usage": None,
    }
    validate_worker_usage(probe)
    return result


def _receipt_digest(
    receipt: Mapping[str, Any] | None,
    launch: Mapping[str, Any],
    terminal_status: str,
) -> str | None:
    if receipt is None:
        return None
    normalized = validate_worker_receipt(receipt)
    bindings = (
        ("runId", "runId"),
        ("storyId", "storyId"),
        ("branch", "branch"),
        ("worktree", "worktree"),
        ("exactModel", "exactModel"),
        ("effort", "effort"),
        ("baseSha", "workerStartSha"),
    )
    for receipt_field, launch_field in bindings:
        if normalized[receipt_field] != launch[launch_field]:
            fail(
                f"workerReceipt.{receipt_field}",
                f"does not match launchIdentity.{launch_field}",
                "bind a receipt from the same exact worker launch",
            )
    if normalized["status"] != terminal_status:
        fail(
            "workerReceipt.status",
            "does not match terminalStatus",
            "record the controller-owned terminal status for this receipt",
        )
    return canonical_digest(normalized)


def _base_record(
    launch: Mapping[str, Any],
    terminal_status: str,
    worker_receipt_digest: str | None,
) -> dict[str, Any]:
    return {
        "schemaVersion": "compass-builder.worker-usage.v1",
        "source": SOURCE,
        **{field: launch[field] for field in _USAGE_IDENTITY_FIELDS},
        "workerReceiptDigest": worker_receipt_digest,
        "terminalStatus": terminal_status,
    }


def _unavailable(base: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return validate_worker_usage({
        **base,
        "observed": False,
        "unavailableReason": reason,
        "usage": None,
    })


def build_unavailable_worker_usage(
    *,
    launch_identity: Mapping[str, Any],
    terminal_status: str,
    unavailable_reason: str,
) -> dict[str, Any]:
    """Build one closed unavailable observation without inventing token counts."""

    launch = _identity(launch_identity)
    status = enum(terminal_status, "terminalStatus", TERMINAL_STATUSES)
    return _unavailable(
        _base_record(launch, status, None), unavailable_reason
    )


def _raw_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("usage count is not an integer")
    if value < 0 or value > MAX_TOKEN_COUNT:
        raise ValueError("usage count is outside the supported range")
    return value


def _normalize_usage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("usage is not an object")
    fields = set(value)
    if not _RAW_REQUIRED.issubset(fields) or fields - _RAW_REQUIRED - _RAW_OPTIONAL:
        raise ValueError("usage field set is invalid")
    cache_write_present = "cache_write_input_tokens" in value
    usage = {
        "inputTokens": _raw_count(value["input_tokens"]),
        "cachedInputTokens": _raw_count(value["cached_input_tokens"]),
        "cacheWriteInputTokens": _raw_count(value.get("cache_write_input_tokens", 0)),
        "cacheWriteInputTokensPresent": cache_write_present,
        "outputTokens": _raw_count(value["output_tokens"]),
        "reasoningOutputTokens": _raw_count(value["reasoning_output_tokens"]),
    }
    if usage["cachedInputTokens"] > usage["inputTokens"]:
        raise ValueError("cached input exceeds total input")
    if usage["reasoningOutputTokens"] > usage["outputTokens"]:
        raise ValueError("reasoning output exceeds total output")
    return usage


def parse_worker_usage(
    stream: bytes,
    *,
    launch_identity: Mapping[str, Any],
    terminal_status: str,
    worker_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one closed observation from direct worker stdout bytes.

    Only a decoded top-level ``turn.completed`` object's direct ``usage`` member
    is eligible. Parser failures become explicit unavailable records after the
    caller-supplied identity and optional receipt binding have been validated.
    A supplied receipt requires launch branch, worktree, and workerStartSha in
    addition to the fields retained directly on the usage record.
    """

    if type(stream) is not bytes:
        raise TypeError("worker usage parser accepts bytes only")
    launch = _identity(
        launch_identity, require_receipt_binding=worker_receipt is not None
    )
    status = enum(terminal_status, "terminalStatus", TERMINAL_STATUSES)
    receipt_digest = _receipt_digest(worker_receipt, launch, status)
    base = _base_record(launch, status, receipt_digest)
    if len(stream) > MAX_USAGE_STREAM_BYTES:
        return _unavailable(base, "input-too-large")
    try:
        text = stream.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return _unavailable(base, "invalid-utf8")
    # JSON Lines records are delimited by LF. ``str.splitlines()`` also treats
    # valid JSON string content such as U+0085/U+2028/U+2029 as record breaks.
    lines = text.split("\n")
    if len(lines) > MAX_USAGE_STREAM_LINES:
        return _unavailable(base, "input-too-large")

    terminals: list[dict[str, Any]] = []
    terminal_bytes: list[bytes] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(
                line,
                parse_constant=_reject_constant,
                object_pairs_hook=_closed_object,
            )
        except (json.JSONDecodeError, _MalformedJsonl, RecursionError, ValueError):
            return _unavailable(base, "malformed-terminal-record")
        if isinstance(event, dict) and event.get("type") == "turn.completed":
            terminals.append(event)
            try:
                terminal_bytes.append(canonical_data(event))
            except (RecursionError, TypeError, UnicodeError, ValueError):
                return _unavailable(base, "malformed-terminal-record")

    if not terminals:
        return _unavailable(base, "no-terminal-usage")
    if len(terminals) > 1:
        reason = (
            "duplicate-terminal-usage"
            if all(item == terminal_bytes[0] for item in terminal_bytes[1:])
            else "conflicting-terminal-usage"
        )
        return _unavailable(base, reason)
    try:
        usage = _normalize_usage(terminals[0].get("usage"))
    except (KeyError, TypeError, ValueError):
        return _unavailable(base, "malformed-terminal-usage")
    return validate_worker_usage({
        **base,
        "observed": True,
        "unavailableReason": None,
        "usage": usage,
    })


__all__ = [
    "MAX_USAGE_STREAM_BYTES", "MAX_USAGE_STREAM_LINES",
    "build_unavailable_worker_usage", "parse_worker_usage",
]
