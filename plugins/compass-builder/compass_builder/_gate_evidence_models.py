"""Closed D3 gate-evidence receipt validation."""

from __future__ import annotations

from typing import Any

from ._validation import (
    boolean, digest, enum, fail, identifier, integer, object_, run_id, sha, string,
    timestamp,
)
from .gate_approval import validate_detached_gate_approval_audit


GENESIS_RECEIPT_DIGEST = "sha256:" + "0" * 64
RECEIPT_SCHEMA_VERSION = "compass-builder.gate-evidence.v1"


def _clean_text(value: Any, path: str, *, maximum: int) -> str:
    """Validate receipt text against the public schema's portable text subset."""

    normalized = string(value, path, maximum=maximum)
    if any(ord(character) <= 0x1F or ord(character) == 0x7F for character in normalized):
        fail(path, "must not contain control characters", "use printable text only")
    return normalized


def validate_gate_evidence_receipt(value: Any) -> dict[str, Any]:
    receipt = object_(value, "$", {
        "schemaVersion", "sequence", "priorReceiptDigest", "runId", "gateId",
        "gateDefinitionDigest", "gateScope", "storyId", "phase", "workspace",
        "targetSha", "required", "verificationType", "approvalId", "approvedBy",
        "approvedAt", "operatorDecisionDigest", "executionIdentityDigest",
        "commandApprovalAudit", "reviewArtifactDigest", "state", "evidenceDigest",
        "verifiedAt", "reason", "providerSeal", "livePlatform",
        "liveEnvironmentDigest",
    })
    if receipt["schemaVersion"] != RECEIPT_SCHEMA_VERSION:
        raise ValueError("$.schemaVersion: unsupported gate-evidence version")
    integer(receipt["sequence"], "$.sequence", minimum=1)
    digest(receipt["priorReceiptDigest"], "$.priorReceiptDigest")
    run_id(receipt["runId"], "$.runId")
    identifier(receipt["gateId"], "$.gateId")
    digest(receipt["gateDefinitionDigest"], "$.gateDefinitionDigest")
    scope = enum(receipt["gateScope"], "$.gateScope", {"story", "root"})
    if scope == "story":
        identifier(receipt["storyId"], "$.storyId")
        if receipt["phase"] != "verification":
            raise ValueError("$.phase: story evidence must use the verification phase")
    elif receipt["storyId"] is not None:
        raise ValueError("$.storyId: root evidence must not name a story")
    elif receipt["phase"] != "post-merge-check":
        raise ValueError("$.phase: root evidence must use the post-merge-check phase")
    enum(receipt["phase"], "$.phase", {"verification", "post-merge-check"})
    _clean_text(receipt["workspace"], "$.workspace", maximum=2048)
    sha(receipt["targetSha"], "$.targetSha")
    _clean_text(receipt["livePlatform"], "$.livePlatform", maximum=160)
    digest(receipt["liveEnvironmentDigest"], "$.liveEnvironmentDigest")
    boolean(receipt["required"], "$.required")
    verification = enum(
        receipt["verificationType"], "$.verificationType", {"command", "manual-review"}
    )
    state = enum(
        receipt["state"], "$.state",
        {"met", "unmet", "blocked", "denied", "pending", "abandoned", "unavailable"},
    )
    _clean_text(receipt["approvalId"], "$.approvalId", maximum=96)
    _clean_text(receipt["approvedBy"], "$.approvedBy", maximum=256)
    timestamp(receipt["approvedAt"], "$.approvedAt")
    digest(receipt["operatorDecisionDigest"], "$.operatorDecisionDigest")
    digest(receipt["executionIdentityDigest"], "$.executionIdentityDigest", nullable=True)
    digest(receipt["reviewArtifactDigest"], "$.reviewArtifactDigest", nullable=True)
    audit = receipt["commandApprovalAudit"]
    if verification == "command":
        if audit is None:
            if receipt["executionIdentityDigest"] is not None or state not in {
                "denied", "pending", "abandoned", "unavailable", "blocked",
            }:
                raise ValueError("$.commandApprovalAudit: non-executed command decision has invalid execution evidence")
        elif (
            not isinstance(audit, dict)
            or receipt["executionIdentityDigest"] is None
            or state not in {"met", "unmet", "blocked"}
        ):
            raise ValueError("$.commandApprovalAudit: executed command evidence requires its detached execution audit")
        else:
            validated_audit = validate_detached_gate_approval_audit(audit)
            if (
                validated_audit["gateId"] != receipt["gateId"]
                or validated_audit["gateDefinitionDigest"]
                != receipt["gateDefinitionDigest"]
                or validated_audit["executionIdentityDigest"]
                != receipt["executionIdentityDigest"]
            ):
                raise ValueError(
                    "$.commandApprovalAudit: detached audit does not bind this receipt"
                )
        if receipt["reviewArtifactDigest"] is not None:
            raise ValueError("$.reviewArtifactDigest: command evidence cannot bind a manual artifact")
    else:
        if audit is not None or receipt["executionIdentityDigest"] is not None:
            raise ValueError("$.commandApprovalAudit: manual evidence cannot contain command authority")
        if state in {"met", "unmet"} and receipt["reviewArtifactDigest"] is None:
            raise ValueError(
                "$.reviewArtifactDigest: met or unmet manual evidence must bind the reviewed artifact"
            )
    digest(receipt["evidenceDigest"], "$.evidenceDigest")
    timestamp(receipt["verifiedAt"], "$.verifiedAt")
    _clean_text(receipt["reason"], "$.reason", maximum=2000)
    _clean_text(receipt["providerSeal"], "$.providerSeal", maximum=8192)
    return receipt


__all__ = [
    "GENESIS_RECEIPT_DIGEST", "RECEIPT_SCHEMA_VERSION", "validate_gate_evidence_receipt",
]
