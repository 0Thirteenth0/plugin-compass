"""Closed validation for controller retry-authorization evidence."""

from __future__ import annotations

from typing import Any

from ._validation import digest, enum, fail, identifier, integer, object_, run_id


RETRY_EVIDENCE_VERSION = "compass-builder.retry-evidence.v1"
RETRY_FAILURE_KINDS = {
    "reasoning", "startup", "model", "config", "tool", "permission",
    "missing-input", "validation", "other",
}
RETRY_EVIDENCE_SOURCES = {"controller", "worker"}
_FIELDS = {
    "schemaVersion", "runId", "storyId", "attempt", "source", "kind",
    "evidenceDigest", "previousLaunchDigest",
}


def validate_retry_evidence_shape(data: dict[str, Any]) -> None:
    """Validate one immutable attempt-two authorization record."""

    object_(data, "$", _FIELDS)
    run_id(data["runId"], "$.runId")
    identifier(data["storyId"], "$.storyId")
    attempt = integer(data["attempt"], "$.attempt", minimum=2)
    if attempt != 2:
        fail(
            "$.attempt", "must be the sole second attempt",
            "bind retry evidence to attempt 2",
        )
    enum(data["source"], "$.source", RETRY_EVIDENCE_SOURCES)
    enum(data["kind"], "$.kind", RETRY_FAILURE_KINDS)
    digest(data["evidenceDigest"], "$.evidenceDigest")
    digest(data["previousLaunchDigest"], "$.previousLaunchDigest")


__all__ = [
    "RETRY_EVIDENCE_SOURCES", "RETRY_EVIDENCE_VERSION",
    "RETRY_FAILURE_KINDS", "validate_retry_evidence_shape",
]
