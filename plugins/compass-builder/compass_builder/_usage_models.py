"""Closed validation for direct worker-stream usage evidence."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ._validation import (
    EFFORTS,
    array,
    boolean,
    digest,
    enum,
    fail,
    identifier,
    integer,
    object_,
    run_id,
)


MAX_TOKEN_COUNT = 9_007_199_254_740_991
TERMINAL_STATUSES = {
    "succeeded", "failed", "blocked", "timed-out", "transport-error",
}
UNAVAILABLE_REASONS = {
    "no-terminal-usage",
    "malformed-terminal-usage",
    "duplicate-terminal-usage",
    "conflicting-terminal-usage",
    "invalid-utf8",
    "input-too-large",
    "malformed-terminal-record",
    "invalid-transport-telemetry",
    "worker-receipt-binding-failed",
}
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
_FIELDS = {
    "schemaVersion", "source", "runId", "storyId", "attempt", "exactModel",
    "effort", "launchDigest", "workerReceiptDigest", "terminalStatus",
    "observed", "unavailableReason", "usage",
}
_USAGE_FIELDS = {
    "inputTokens", "cachedInputTokens", "cacheWriteInputTokens",
    "cacheWriteInputTokensPresent", "outputTokens", "reasoningOutputTokens",
}
SEMANTIC_EXTENSION = "x-compassBuilderSemanticConstraints"
SEMANTIC_SCHEMA_VERSION = "compass-builder.semantic-constraints.v1"
_SEMANTIC_OPERATOR = "less-than-or-equal"
_SEMANTIC_PATHS = {
    "$.usage.cachedInputTokens",
    "$.usage.inputTokens",
    "$.usage.reasoningOutputTokens",
    "$.usage.outputTokens",
}
_REQUIRED_SEMANTIC_RULES = {
    (
        _SEMANTIC_OPERATOR,
        "$.usage.cachedInputTokens",
        "$.usage.inputTokens",
    ),
    (
        _SEMANTIC_OPERATOR,
        "$.usage.reasoningOutputTokens",
        "$.usage.outputTokens",
    ),
}


def _token_count(value: Any, path: str) -> int:
    count = integer(value, path)
    if count > MAX_TOKEN_COUNT:
        fail(
            path,
            f"exceeds the interoperable integer bound {MAX_TOKEN_COUNT}",
            "record a non-negative count within the JSON safe-integer range",
        )
    return count


def _usage(value: Any, path: str) -> None:
    usage = object_(value, path, _USAGE_FIELDS)
    input_tokens = _token_count(usage["inputTokens"], f"{path}.inputTokens")
    cached_tokens = _token_count(
        usage["cachedInputTokens"], f"{path}.cachedInputTokens"
    )
    cache_write_tokens = _token_count(
        usage["cacheWriteInputTokens"], f"{path}.cacheWriteInputTokens"
    )
    cache_write_present = boolean(
        usage["cacheWriteInputTokensPresent"],
        f"{path}.cacheWriteInputTokensPresent",
    )
    output_tokens = _token_count(usage["outputTokens"], f"{path}.outputTokens")
    reasoning_tokens = _token_count(
        usage["reasoningOutputTokens"], f"{path}.reasoningOutputTokens"
    )
    if cached_tokens > input_tokens:
        fail(
            f"{path}.cachedInputTokens",
            "cannot exceed inputTokens",
            "record cached input as a component of total input",
        )
    if reasoning_tokens > output_tokens:
        fail(
            f"{path}.reasoningOutputTokens",
            "cannot exceed outputTokens",
            "record reasoning output as a component of total output",
        )
    if not cache_write_present and cache_write_tokens != 0:
        fail(
            f"{path}.cacheWriteInputTokens",
            "must be zero when the raw cache-write field was absent",
            "normalize a missing official cache-write field to zero without claiming presence",
        )


def validate_worker_usage_shape(data: dict[str, Any]) -> None:
    """Validate one immutable launch-bound usage observation."""

    object_(data, "$", _FIELDS)
    enum(data["source"], "$.source", {"codex-exec-jsonl-stdout"})
    run_id(data["runId"], "$.runId")
    identifier(data["storyId"], "$.storyId")
    attempt = integer(data["attempt"], "$.attempt", minimum=1)
    if attempt > 2:
        fail("$.attempt", "must be launch attempt 1 or 2", "bind an existing Compass Builder launch")
    model = data["exactModel"]
    if not isinstance(model, str) or not _MODEL_RE.fullmatch(model) or model == "inherit":
        fail(
            "$.exactModel",
            "must be one exact interpolation-free model identifier",
            "copy the exact model from the validated launch record",
        )
    enum(data["effort"], "$.effort", EFFORTS)
    digest(data["launchDigest"], "$.launchDigest")
    digest(data["workerReceiptDigest"], "$.workerReceiptDigest", nullable=True)
    enum(data["terminalStatus"], "$.terminalStatus", TERMINAL_STATUSES)
    observed = boolean(data["observed"], "$.observed")

    reason = data["unavailableReason"]
    usage = data["usage"]
    if observed:
        if reason is not None:
            fail(
                "$.unavailableReason",
                "must be null when usage was observed",
                "clear the unavailable reason",
            )
        if usage is None:
            fail("$.usage", "must be present when observed is true", "record every raw usage count")
        _usage(usage, "$.usage")
    else:
        if reason is None:
            fail(
                "$.unavailableReason",
                "is required when usage is unavailable",
                "record one supported machine-readable reason",
            )
        enum(reason, "$.unavailableReason", UNAVAILABLE_REASONS)
        if usage is not None:
            fail(
                "$.usage",
                "must be null when usage is unavailable",
                "do not zero-fill or retain untrusted partial counts",
            )


def _semantic_count(record: Mapping[str, Any], path: str) -> int:
    usage = record.get("usage")
    if not isinstance(usage, Mapping):
        fail(
            "$.usage",
            "must be an object before semantic component rules can be evaluated",
            "run structural validation and provide observed usage values",
        )
    field = path.removeprefix("$.usage.")
    if field not in usage:
        fail(
            path,
            "is absent and the declared semantic rule cannot be evaluated",
            "provide the structurally required usage field",
        )
    value = usage[field]
    if isinstance(value, bool) or not isinstance(value, int):
        fail(path, "must be an integer for semantic comparison", "provide a non-negative integer count")
    return value


def validate_worker_usage_schema_semantics(
    schema: Mapping[str, Any], record: Mapping[str, Any]
) -> None:
    """Independently evaluate the closed sibling constraints declared by the schema."""

    if not isinstance(schema, Mapping):
        fail("$schema", "must be an object mapping", "load the worker-usage JSON Schema object")
    extension = schema.get(SEMANTIC_EXTENSION)
    extension_path = f"$schema.{SEMANTIC_EXTENSION}"
    extension = object_(
        extension,
        extension_path,
        {"schemaVersion", "rules"},
    )
    if extension["schemaVersion"] != SEMANTIC_SCHEMA_VERSION:
        fail(
            f"{extension_path}.schemaVersion",
            f"must be {SEMANTIC_SCHEMA_VERSION!r}",
            "use the supported immutable semantic rule version",
        )
    declared: list[tuple[str, str, str]] = []
    rules = array(
        extension["rules"],
        f"{extension_path}.rules",
        minimum=2,
        maximum=2,
    )
    for index, item in enumerate(rules):
        path = f"{extension_path}.rules[{index}]"
        rule = object_(item, path, {"operator", "left", "right"})
        operator = enum(rule["operator"], f"{path}.operator", {_SEMANTIC_OPERATOR})
        left = enum(rule["left"], f"{path}.left", _SEMANTIC_PATHS)
        right = enum(rule["right"], f"{path}.right", _SEMANTIC_PATHS)
        declared.append((operator, left, right))
    if set(declared) != _REQUIRED_SEMANTIC_RULES or len(set(declared)) != 2:
        fail(
            f"{extension_path}.rules",
            "must declare exactly the cached-input/input and reasoning-output/output component rules",
            "restore both unique versioned less-than-or-equal rules",
        )

    if not isinstance(record, Mapping):
        fail("$", "must be an object mapping", "provide a worker-usage record")
    usage = record.get("usage")
    if usage is None:
        if record.get("observed") is False:
            return
        fail(
            "$.usage",
            "is unavailable while observed is not false",
            "run structural validation and provide a coherent observation",
        )
    for operator, left, right in declared:
        left_value = _semantic_count(record, left)
        right_value = _semantic_count(record, right)
        if operator == _SEMANTIC_OPERATOR and left_value > right_value:
            fail(
                left,
                f"cannot exceed {right}",
                "record the component count at or below its containing total",
            )


__all__ = [
    "MAX_TOKEN_COUNT", "SEMANTIC_EXTENSION", "SEMANTIC_SCHEMA_VERSION",
    "TERMINAL_STATUSES", "UNAVAILABLE_REASONS",
    "validate_worker_usage_schema_semantics", "validate_worker_usage_shape",
]
