"""Append-only hash-chained D3 gate evidence and deterministic folding."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ._gate_evidence_models import (
    GENESIS_RECEIPT_DIGEST, RECEIPT_SCHEMA_VERSION, validate_gate_evidence_receipt,
)
from ._validation import (
    canonical_digest, digest, enum, identifier, object_, run_id, sha, string,
)
from .durable_artifacts import ArtifactJournal
from .models import validate_outcome_gate_ledger


@dataclass(frozen=True)
class GateEvidenceFold:
    valid_chain: bool
    receipts: tuple[dict[str, Any], ...]
    receipt_digests: tuple[str, ...]
    approval_ids: frozenset[str]


_COMMAND_INTENT_VERSION = "compass-builder.gate-command-execution-intent.v1"


def _validate_command_execution_intent(value: Mapping[str, Any]) -> dict[str, Any]:
    intent = object_(copy.deepcopy(dict(value)), "commandIntent", {
        "schemaVersion", "attemptKey", "executionKey", "runId", "gateId",
        "gateDefinitionDigest", "gateScope", "storyId", "workspace", "targetSha",
        "livePlatform", "liveEnvironmentDigest", "operatorApprovalId",
        "commandApprovalId", "operatorDecisionDigest", "executionIdentityDigest",
    })
    if intent["schemaVersion"] != _COMMAND_INTENT_VERSION:
        raise ValueError("commandIntent.schemaVersion: unsupported command intent")
    digest(intent["attemptKey"], "commandIntent.attemptKey")
    digest(intent["executionKey"], "commandIntent.executionKey")
    run_id(intent["runId"], "commandIntent.runId")
    identifier(intent["gateId"], "commandIntent.gateId")
    digest(intent["gateDefinitionDigest"], "commandIntent.gateDefinitionDigest")
    scope = enum(intent["gateScope"], "commandIntent.gateScope", {"story", "root"})
    if scope == "story":
        identifier(intent["storyId"], "commandIntent.storyId")
    elif intent["storyId"] is not None:
        raise ValueError("commandIntent.storyId: root intent cannot name a story")
    string(intent["workspace"], "commandIntent.workspace", maximum=2048)
    sha(intent["targetSha"], "commandIntent.targetSha")
    string(intent["livePlatform"], "commandIntent.livePlatform", maximum=160)
    digest(intent["liveEnvironmentDigest"], "commandIntent.liveEnvironmentDigest")
    string(intent["operatorApprovalId"], "commandIntent.operatorApprovalId", maximum=96)
    string(intent["commandApprovalId"], "commandIntent.commandApprovalId", maximum=96)
    digest(intent["operatorDecisionDigest"], "commandIntent.operatorDecisionDigest")
    digest(intent["executionIdentityDigest"], "commandIntent.executionIdentityDigest")
    scope = {
        field: intent[field] for field in (
            "runId", "gateId", "gateDefinitionDigest", "gateScope", "storyId",
            "workspace", "targetSha", "livePlatform", "liveEnvironmentDigest",
            "executionIdentityDigest",
        )
    }
    if intent["executionKey"] != canonical_digest(scope):
        raise ValueError("commandIntent.executionKey: does not match execution scope")
    attempt = {
        "executionKey": intent["executionKey"],
        **scope,
        "operatorApprovalId": intent["operatorApprovalId"],
        "commandApprovalId": intent["commandApprovalId"],
        "operatorDecisionDigest": intent["operatorDecisionDigest"],
    }
    if intent["attemptKey"] != canonical_digest(attempt):
        raise ValueError("commandIntent.attemptKey: does not match exact attempt")
    return intent


def _ordered_chain(receipts: Sequence[Mapping[str, Any]]) -> GateEvidenceFold:
    normalized = [validate_gate_evidence_receipt(copy.deepcopy(dict(item))) for item in receipts]
    normalized.sort(key=lambda item: item["sequence"])
    prior = GENESIS_RECEIPT_DIGEST
    ids: set[str] = set()
    digests: list[str] = []
    for sequence, receipt in enumerate(normalized, start=1):
        if receipt["sequence"] != sequence or receipt["priorReceiptDigest"] != prior:
            raise ValueError("gate-evidence receipt chain is forked, reordered, or incomplete")
        approval_id = receipt["approvalId"]
        command_id = (
            receipt["commandApprovalAudit"].get("approvalId")
            if receipt["commandApprovalAudit"] is not None else None
        )
        for candidate in (approval_id, command_id):
            if candidate is not None:
                if candidate in ids:
                    raise ValueError(f"gate-evidence approvalId was reused: {candidate}")
                ids.add(candidate)
        prior = canonical_digest(receipt)
        digests.append(prior)
    return GateEvidenceFold(True, tuple(normalized), tuple(digests), frozenset(ids))


def fold_gate_evidence(
    outcome_gate_ledger: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
) -> GateEvidenceFold:
    ledger = validate_outcome_gate_ledger(outcome_gate_ledger)
    folded = _ordered_chain(receipts)
    gates = {gate["id"]: gate for gate in ledger["gates"]}
    for receipt in folded.receipts:
        if receipt["runId"] != ledger["runId"]:
            raise ValueError("gate-evidence receipt belongs to another run")
        gate = gates.get(receipt["gateId"])
        if gate is None or receipt["gateDefinitionDigest"] != canonical_digest(gate):
            raise ValueError("gate-evidence receipt does not bind an immutable ledger gate")
        if (
            receipt["gateScope"] != gate["gateScope"]
            or receipt["storyId"] != gate["storyId"]
            or receipt["required"] != gate["required"]
            or receipt["verificationType"] != gate["verificationType"]
        ):
            raise ValueError("gate-evidence receipt scope does not match its ledger gate")
    return folded


class GateEvidenceJournal:
    """Persist immutable gate receipts through the controller artifact journal."""

    def __init__(self, run_root: Path, controller_root: Path):
        self._journal = ArtifactJournal(run_root, controller_root)

    def read(self) -> tuple[dict[str, Any], ...]:
        return _ordered_chain(self._journal.read("gate-evidence")).receipts

    def read_command_execution_intents(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            _validate_command_execution_intent(item)
            for item in self._journal.read("gate-execution-intents")
        )

    def record_command_execution_intent(
        self, record: Mapping[str, Any]
    ) -> dict[str, Any]:
        value = copy.deepcopy(dict(record))
        value["schemaVersion"] = _COMMAND_INTENT_VERSION
        normalized = _validate_command_execution_intent(value)
        self._journal.record("gate-execution-intents", normalized)
        return copy.deepcopy(normalized)

    def append(
        self,
        record: Mapping[str, Any],
        *,
        seal_receipt: Callable[[Mapping[str, Any]], str],
        authenticate_receipt: Callable[[Mapping[str, Any]], bool],
    ) -> dict[str, Any]:
        """Append a receipt sealed by the trusted provider over every field except the seal."""

        prior = self.read()
        value = copy.deepcopy(dict(record))
        value.update(
            schemaVersion=RECEIPT_SCHEMA_VERSION,
            sequence=len(prior) + 1,
            priorReceiptDigest=(
                GENESIS_RECEIPT_DIGEST if not prior else canonical_digest(prior[-1])
            ),
        )
        value["providerSeal"] = seal_receipt(copy.deepcopy(value))
        normalized = validate_gate_evidence_receipt(value)
        candidate = _ordered_chain((*prior, normalized))
        if authenticate_receipt(copy.deepcopy(normalized)) is not True:
            raise ValueError("gate-evidence provider seal was not authenticated before publication")
        self._journal.record("gate-evidence", normalized)
        return copy.deepcopy(candidate.receipts[-1])


__all__ = ["GateEvidenceFold", "GateEvidenceJournal", "fold_gate_evidence"]
