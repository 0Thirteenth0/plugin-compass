"""Trusted just-in-time D3 operator decisions and outcome-gate enforcement."""

from __future__ import annotations

import copy
import hashlib
import os
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from ._gate_evidence_models import GENESIS_RECEIPT_DIGEST
from ._validation import (
    canonical_digest, digest, enum, integer, object_, run_id, string, timestamp,
)
from .gate_approval import (
    ApprovalBoundaryError, TrustedGateApproval, inspect_trusted_gate_approval,
)
from .gate_evidence import GateEvidenceFold, GateEvidenceJournal, fold_gate_evidence
from .gate_runner import (
    GateRunnerError, current_platform_identity, digest_file, environment_digest,
    run_approved_gates, validate_gate_execution_identity,
)
from .models import validate_outcome_gate_ledger
from .secure_files import SecureFileError, read_no_follow, require_contained


class GateEnforcementError(RuntimeError):
    """A D3 gate phase could not reach a trustworthy decision."""


class OperatorGateProvider(ABC):
    """Trusted in-process boundary implemented by a host operator broker."""

    @abstractmethod
    def decide(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return one exact decision; mappings outside this boundary grant no authority."""

    @abstractmethod
    def seal_receipt(self, receipt: Mapping[str, Any]) -> str:
        """Seal every immutable receipt field; the circular providerSeal field is absent."""

    @abstractmethod
    def authenticate_receipt(self, receipt: Mapping[str, Any]) -> bool:
        """Authenticate a full persisted receipt with provider-held trust material."""

    @abstractmethod
    def read_evidence_checkpoint(self, run_id: str) -> Mapping[str, Any] | None:
        """Read the provider-held monotonic terminal checkpoint for one run."""

    @abstractmethod
    def initialize_evidence_checkpoint(self, current: Mapping[str, Any]) -> None:
        """Atomically initialize a never-before-seen run at its genesis checkpoint."""

    @abstractmethod
    def advance_evidence_checkpoint(
        self, previous: Mapping[str, Any] | None, current: Mapping[str, Any]
    ) -> None:
        """Atomically advance the provider-held checkpoint from previous to current."""

    @abstractmethod
    def reserve_command_execution(self, reservation: Mapping[str, Any]) -> None:
        """Atomically consume both IDs and retain one exact attempt as reserved."""

    @abstractmethod
    def complete_command_execution(
        self, reservation: Mapping[str, Any], receipt: Mapping[str, Any]
    ) -> None:
        """Idempotently retain one attempt's authenticated receipt as evidenced."""


_DECISION_SEAL = object()


class _TrustedOperatorDecision:
    __slots__ = ("record", "command_approval", "seal", "consumed")

    def __init__(self, record: Mapping[str, Any], command_approval: TrustedGateApproval | None):
        self.record = copy.deepcopy(dict(record))
        self.command_approval = command_approval
        self.seal = _DECISION_SEAL
        self.consumed = False

    def consume(self) -> tuple[dict[str, Any], TrustedGateApproval | None]:
        if self.seal is not _DECISION_SEAL or self.consumed:
            raise GateEnforcementError("operator decision capability is invalid or already consumed")
        self.consumed = True
        return copy.deepcopy(self.record), self.command_approval


@dataclass(frozen=True)
class GateEnforcementOutcome:
    required_met: bool
    adopted: bool
    receipts: tuple[dict[str, Any], ...]
    blocking_reason: str | None


def require_operator_provider(provider: object) -> OperatorGateProvider:
    if not isinstance(provider, OperatorGateProvider):
        raise GateEnforcementError(
            "a trusted in-process operator provider (OperatorGateProvider) is required"
        )
    return provider


_CHECKPOINT_VERSION = "compass-builder.gate-evidence-checkpoint.v1"


def _validate_checkpoint(value: Any, expected_run_id: str) -> dict[str, Any] | None:
    if value is None:
        return None
    checkpoint = object_(value, "checkpoint", {
        "schemaVersion", "runId", "receiptCount", "terminalReceiptDigest",
    })
    if checkpoint["schemaVersion"] != _CHECKPOINT_VERSION:
        raise ValueError("checkpoint.schemaVersion: unsupported gate-evidence checkpoint")
    if run_id(checkpoint["runId"], "checkpoint.runId") != expected_run_id:
        raise ValueError("checkpoint.runId: checkpoint belongs to another run")
    count = integer(checkpoint["receiptCount"], "checkpoint.receiptCount", minimum=0)
    terminal = digest(
        checkpoint["terminalReceiptDigest"], "checkpoint.terminalReceiptDigest"
    )
    if (count == 0) != (terminal == GENESIS_RECEIPT_DIGEST):
        raise ValueError("checkpoint.terminalReceiptDigest: inconsistent genesis checkpoint")
    return checkpoint


def _checkpoint(run_id_value: str, folded: GateEvidenceFold) -> dict[str, Any]:
    return {
        "schemaVersion": _CHECKPOINT_VERSION,
        "runId": run_id_value,
        "receiptCount": len(folded.receipts),
        "terminalReceiptDigest": (
            GENESIS_RECEIPT_DIGEST
            if not folded.receipt_digests else folded.receipt_digests[-1]
        ),
    }


def _provider_authenticates(
    provider: OperatorGateProvider, receipt: Mapping[str, Any]
) -> bool:
    try:
        return provider.authenticate_receipt(copy.deepcopy(dict(receipt))) is True
    except Exception:
        return False


def _provider_seal(
    provider: OperatorGateProvider, receipt: Mapping[str, Any]
) -> str:
    try:
        value = provider.seal_receipt(copy.deepcopy(dict(receipt)))
        return string(value, "providerSeal", maximum=8192)
    except Exception as exc:
        raise GateEnforcementError(f"operator provider could not seal gate evidence: {exc}") from exc


def _read_checkpoint(
    provider: OperatorGateProvider, expected_run_id: str
) -> dict[str, Any] | None:
    try:
        return _validate_checkpoint(
            provider.read_evidence_checkpoint(expected_run_id), expected_run_id
        )
    except Exception as exc:
        raise GateEnforcementError(
            f"operator provider gate-evidence checkpoint is unavailable: {exc}"
        ) from exc


def _advance_checkpoint(
    provider: OperatorGateProvider, previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        provider.advance_evidence_checkpoint(
            None if previous is None else copy.deepcopy(dict(previous)),
            copy.deepcopy(dict(current)),
        )
    except Exception as exc:
        raise GateEnforcementError(
            f"operator provider could not advance the gate-evidence checkpoint: {exc}"
        ) from exc
    observed = _read_checkpoint(provider, str(current["runId"]))
    if observed != dict(current):
        raise GateEnforcementError(
            "operator provider did not retain the exact gate-evidence checkpoint"
        )
    return observed


def _initialize_checkpoint(
    provider: OperatorGateProvider, current: Mapping[str, Any]
) -> dict[str, Any]:
    try:
        provider.initialize_evidence_checkpoint(copy.deepcopy(dict(current)))
    except Exception as exc:
        raise GateEnforcementError(
            f"operator provider could not initialize the gate-evidence lifecycle: {exc}"
        ) from exc
    observed = _read_checkpoint(provider, str(current["runId"]))
    if observed != dict(current):
        raise GateEnforcementError(
            "operator provider did not retain the initialized genesis checkpoint"
        )
    return observed


def _fold_journal(
    ledger: Mapping[str, Any], journal: GateEvidenceJournal
) -> GateEvidenceFold:
    try:
        return fold_gate_evidence(ledger, journal.read())
    except ValueError as exc:
        raise GateEnforcementError(f"gate-evidence journal is invalid: {exc}") from exc


def _reconcile_checkpoint(
    provider: OperatorGateProvider, run_id_value: str, folded: GateEvidenceFold,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    previous = _read_checkpoint(provider, run_id_value)
    current = _checkpoint(run_id_value, folded)
    if previous is None:
        if current["receiptCount"]:
            raise GateEnforcementError(
                "gate-evidence checkpoint is missing for an existing journal"
            )
        previous = _initialize_checkpoint(provider, current)
        return previous, current
    previous_count = int(previous["receiptCount"])
    current_count = int(current["receiptCount"])
    if current_count < previous_count:
        raise GateEnforcementError(
            "gate-evidence journal was truncated below the provider checkpoint"
        )
    if previous_count and folded.receipt_digests[previous_count - 1] != previous["terminalReceiptDigest"]:
        raise GateEnforcementError(
            "gate-evidence journal diverges from the provider checkpoint"
        )
    if current_count == previous_count:
        if current != previous:
            raise GateEnforcementError(
                "gate-evidence terminal digest does not match the provider checkpoint"
            )
        return previous, current
    tail = folded.receipts[previous_count:]
    if tail and not all(_provider_authenticates(provider, receipt) for receipt in tail):
        raise GateEnforcementError(
            "gate-evidence journal has an unauthenticated tail beyond its provider checkpoint"
        )
    if tail:
        previous = _advance_checkpoint(provider, previous, current)
    return previous, current


def _canonical_workspace(workspace: Path) -> Path:
    candidate = Path(workspace)
    if not candidate.is_absolute():
        raise GateEnforcementError("gate workspace must be an explicit absolute path")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise GateEnforcementError(f"gate workspace is unavailable: {exc}") from exc
    if os.path.normcase(os.path.normpath(str(candidate))) != os.path.normcase(str(resolved)):
        raise GateEnforcementError("gate workspace must use its canonical path")
    return resolved


def _live_identity(environment: Mapping[str, str]) -> tuple[str, str]:
    platform_identity = current_platform_identity()
    try:
        return platform_identity, environment_digest(environment, platform_identity)
    except GateRunnerError as exc:
        raise GateEnforcementError(f"live gate environment identity is invalid: {exc}") from exc


def _git(workspace: Path, environment: Mapping[str, str], *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "--no-pager", "-C", str(workspace), *arguments],
            check=False, capture_output=True, shell=False, timeout=20,
            env=dict(environment),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GateEnforcementError(f"gate workspace Git evidence is unavailable: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GateEnforcementError(f"gate workspace Git {arguments[0]} failed: {detail}")
    return result.stdout.decode("utf-8", errors="strict").strip()


def _require_exact_clean_head(
    workspace: Path, target_sha: str, environment: Mapping[str, str]
) -> None:
    if len(target_sha) != 40 or any(character not in "0123456789abcdef" for character in target_sha):
        raise GateEnforcementError("gate target must be one immutable lowercase Git SHA")
    if _git(workspace, environment, "rev-parse", "HEAD") != target_sha:
        raise GateEnforcementError("gate workspace HEAD does not match the exact target SHA")
    if _git(workspace, environment, "status", "--porcelain=v1", "--untracked-files=all"):
        raise GateEnforcementError("gate workspace must be clean at the exact target SHA")


def _manual_artifact_digest(gate: Mapping[str, Any], workspace: Path) -> str | None:
    path = gate["independentReviewPath"]
    if not isinstance(path, str):
        raise GateEnforcementError("manual gate lacks its independent review path")
    target = workspace.joinpath(*path.split("/"))
    try:
        require_contained(target, workspace, label="manual review artifact")
    except SecureFileError as exc:
        raise GateEnforcementError(str(exc)) from exc
    try:
        payload = read_no_follow(
            target, workspace, label="manual review artifact", max_bytes=16_777_216
        )
    except SecureFileError:
        return None
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _require_manual_live_identity(
    gate: Mapping[str, Any], live_platform: str, live_environment_digest: str,
) -> None:
    if gate["verificationType"] != "manual-review":
        return
    if gate["platform"] != live_platform:
        raise GateEnforcementError(
            "manual gate platform does not match the live platform identity"
        )
    if gate["environmentDigest"] != live_environment_digest:
        raise GateEnforcementError(
            "manual gate environment does not match the live environment identity"
        )


def _request(
    gate: Mapping[str, Any], *, run_id: str, workspace: Path, target_sha: str,
    review_artifact_digest: str | None, live_platform: str,
    live_environment_digest: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": "compass-builder.operator-gate-request.v1",
        "runId": run_id,
        "gateId": gate["id"],
        "gateDefinitionDigest": canonical_digest(gate),
        "gateDefinition": copy.deepcopy(dict(gate)),
        "gateScope": gate["gateScope"],
        "storyId": gate["storyId"],
        "workspace": str(workspace),
        "targetSha": target_sha,
        "livePlatform": live_platform,
        "liveEnvironmentDigest": live_environment_digest,
        "verificationType": gate["verificationType"],
        "reviewArtifactDigest": review_artifact_digest,
    }


def _issue_decision(
    provider: OperatorGateProvider, request: Mapping[str, Any]
) -> _TrustedOperatorDecision:
    try:
        raw = provider.decide(copy.deepcopy(dict(request)))
        value = dict(raw)
    except Exception as exc:
        raise GateEnforcementError(f"operator decision is unavailable: {exc}") from exc
    fields = {
        "approvalId", "approvedBy", "approvedAt", "decisionState", "commandApproval",
    }
    if set(value) != fields:
        raise GateEnforcementError("operator decision must match its closed in-process field set")
    try:
        string(value["approvalId"], "approvalId", maximum=96)
        string(value["approvedBy"], "approvedBy", maximum=256)
        timestamp(value["approvedAt"], "approvedAt")
    except ValueError as exc:
        raise GateEnforcementError(f"operator decision audit identity is invalid: {exc}") from exc
    try:
        decision_state = enum(
            value["decisionState"], "decisionState",
            {
                "approved", "denied", "pending", "abandoned", "unavailable",
                "blocked", "unmet",
            },
        )
    except ValueError as exc:
        raise GateEnforcementError(f"operator decision state is invalid: {exc}") from exc
    command_approval = value.pop("commandApproval")
    if command_approval is not None and not isinstance(command_approval, TrustedGateApproval):
        raise GateEnforcementError("raw command approval mappings cannot grant gate authority")
    if decision_state != "approved" and command_approval is not None:
        raise GateEnforcementError("only an approved decision may carry command authority")
    if decision_state == "unmet" and request["verificationType"] != "manual-review":
        raise GateEnforcementError(
            "an unmet operator decision is valid only for a manual-review gate"
        )
    record = {
        **value,
        "requestDigest": canonical_digest(request),
    }
    return _TrustedOperatorDecision(record, command_approval)


def _command_execution_reservation(
    gate: Mapping[str, Any], decision: Mapping[str, Any],
    command_audit: Mapping[str, Any], *, run_id_value: str, workspace: Path,
    target_sha: str, live_platform: str, live_environment_digest: str,
) -> dict[str, Any]:
    scope = {
        "runId": run_id_value,
        "gateId": gate["id"],
        "gateDefinitionDigest": canonical_digest(gate),
        "gateScope": gate["gateScope"],
        "storyId": gate["storyId"],
        "workspace": str(workspace),
        "targetSha": target_sha,
        "livePlatform": live_platform,
        "liveEnvironmentDigest": live_environment_digest,
        "executionIdentityDigest": command_audit["executionIdentityDigest"],
    }
    attempt = {
        "executionKey": canonical_digest(scope),
        **scope,
        "operatorApprovalId": decision["approvalId"],
        "commandApprovalId": command_audit["approvalId"],
        "operatorDecisionDigest": canonical_digest(decision),
    }
    return {
        "schemaVersion": "compass-builder.gate-command-execution-intent.v1",
        "attemptKey": canonical_digest(attempt),
        **attempt,
    }


def _reserve_command_execution(
    provider: OperatorGateProvider, reservation: Mapping[str, Any]
) -> None:
    try:
        provider.reserve_command_execution(copy.deepcopy(dict(reservation)))
    except Exception as exc:
        raise GateEnforcementError(
            f"operator provider could not reserve command execution: {exc}"
        ) from exc


def _complete_command_execution(
    provider: OperatorGateProvider, reservation: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> None:
    try:
        provider.complete_command_execution(
            copy.deepcopy(dict(reservation)), copy.deepcopy(dict(receipt))
        )
    except Exception as exc:
        raise GateEnforcementError(
            f"operator provider could not complete command execution evidence: {exc}"
        ) from exc


def _receipt_completes_intent(
    intent: Mapping[str, Any], receipt: Mapping[str, Any]
) -> bool:
    audit = receipt.get("commandApprovalAudit")
    return (
        isinstance(audit, dict)
        and receipt["runId"] == intent["runId"]
        and receipt["gateId"] == intent["gateId"]
        and receipt["gateDefinitionDigest"] == intent["gateDefinitionDigest"]
        and receipt["gateScope"] == intent["gateScope"]
        and receipt["storyId"] == intent["storyId"]
        and receipt["workspace"] == intent["workspace"]
        and receipt["targetSha"] == intent["targetSha"]
        and receipt["livePlatform"] == intent["livePlatform"]
        and receipt["liveEnvironmentDigest"] == intent["liveEnvironmentDigest"]
        and receipt["approvalId"] == intent["operatorApprovalId"]
        and receipt["operatorDecisionDigest"] == intent["operatorDecisionDigest"]
        and receipt["executionIdentityDigest"] == intent["executionIdentityDigest"]
        and audit.get("approvalId") == intent["commandApprovalId"]
    )


def _reconcile_command_execution_intents(
    provider: OperatorGateProvider, journal: GateEvidenceJournal,
    folded: GateEvidenceFold, run_id_value: str,
) -> None:
    for intent in journal.read_command_execution_intents():
        if intent["runId"] != run_id_value:
            raise GateEnforcementError(
                "command execution intent belongs to another run"
            )
        receipt = next((
            candidate for candidate in folded.receipts
            if _receipt_completes_intent(intent, candidate)
        ), None)
        if receipt is None:
            raise GateEnforcementError(
                "command execution remains durably reserved without authenticated evidence"
            )
        _complete_command_execution(provider, intent, receipt)


def _adoptable(
    gate: Mapping[str, Any], receipt: Mapping[str, Any], *, workspace: Path,
    target_sha: str, environment: Mapping[str, str], review_digest: str | None,
    provider: OperatorGateProvider, live_platform: str,
    live_environment_digest: str, revalidate_live: bool = True,
) -> bool:
    if (
        not _provider_authenticates(provider, receipt)
        or receipt["state"] != "met"
        or receipt["gateDefinitionDigest"] != canonical_digest(gate)
        or receipt["gateScope"] != gate["gateScope"]
        or receipt["storyId"] != gate["storyId"]
        or receipt["workspace"] != str(workspace)
        or receipt["targetSha"] != target_sha
        or receipt["livePlatform"] != live_platform
        or receipt["liveEnvironmentDigest"] != live_environment_digest
    ):
        return False
    if not revalidate_live:
        return True
    if gate["verificationType"] == "manual-review":
        return receipt["reviewArtifactDigest"] == review_digest
    try:
        validate_gate_execution_identity(
            gate, receipt["commandApprovalAudit"], repository_root=workspace,
            environment=environment,
        )
    except (GateRunnerError, ValueError):
        return False
    return True


def enforce_scope_gates(
    outcome_gate_ledger: Mapping[str, Any], *, gate_scope: str,
    story_id: str | None, workspace: Path, target_sha: str,
    environment: Mapping[str, str], provider: OperatorGateProvider,
    journal: GateEvidenceJournal,
    process_runner: Callable[..., subprocess.CompletedProcess[Any]] | None = None,
    verified_at: Callable[[], str] | None = None,
) -> GateEnforcementOutcome:
    """Enforce one story/root phase at its exact clean Git target."""

    broker = require_operator_provider(provider)
    ledger = validate_outcome_gate_ledger(outcome_gate_ledger)
    if gate_scope not in {"story", "root"}:
        raise GateEnforcementError("gate scope must be story or root")
    if (gate_scope == "story") != (story_id is not None):
        raise GateEnforcementError("story gate phases require exactly one story identity")
    root = _canonical_workspace(workspace)
    _require_exact_clean_head(root, target_sha, environment)
    live_platform, live_environment_digest = _live_identity(environment)
    selected = [
        gate for gate in ledger["gates"]
        if gate["gateScope"] == gate_scope and gate["storyId"] == story_id
    ]
    for gate in selected:
        _require_manual_live_identity(gate, live_platform, live_environment_digest)
    folded = _fold_journal(ledger, journal)
    anchored, current_checkpoint = _reconcile_checkpoint(
        broker, ledger["runId"], folded
    )
    _reconcile_command_execution_intents(broker, journal, folded, ledger["runId"])
    checkpoint_is_current = anchored == current_checkpoint
    receipts: list[dict[str, Any]] = []
    adopted = False
    used_ids = set(folded.approval_ids)
    now = verified_at or (
        lambda: datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )
    )
    for gate in selected:
        review_digest = (
            _manual_artifact_digest(gate, root)
            if gate["verificationType"] == "manual-review" else None
        )
        exact = None
        if checkpoint_is_current:
            exact = next((
                receipt for receipt in reversed(folded.receipts)
                if _adoptable(
                    gate, receipt, workspace=root, target_sha=target_sha,
                    environment=environment, review_digest=review_digest,
                    provider=broker, live_platform=live_platform,
                    live_environment_digest=live_environment_digest,
                )
            ), None)
        if exact is not None:
            receipts.append(copy.deepcopy(exact))
            adopted = True
            continue

        request = _request(
            gate, run_id=ledger["runId"], workspace=root, target_sha=target_sha,
            review_artifact_digest=review_digest,
            live_platform=live_platform,
            live_environment_digest=live_environment_digest,
        )
        capability = _issue_decision(broker, request)
        decision, command_capability = capability.consume()
        approval_id = decision["approvalId"]
        if approval_id in used_ids:
            raise GateEnforcementError(f"operator approvalId was reused: {approval_id}")
        used_ids.add(approval_id)
        command_audit = None
        command_reservation = None
        execution_digest = None
        decision_state = decision["decisionState"]
        if (
            gate["verificationType"] == "manual-review"
            and review_digest is None
            and decision_state != "unavailable"
        ):
            raise GateEnforcementError(
                "manual review artifact is unavailable; provider must return unavailable"
            )
        state = decision_state if decision_state != "approved" else "blocked"
        reason = {
            "denied": "operator denied this exact gate decision",
            "pending": "operator left this exact gate decision pending",
            "abandoned": "operator abandoned this exact gate decision",
            "unavailable": "operator reported this exact gate decision unavailable",
            "blocked": "operator reported this exact gate decision blocked",
            "unmet": "operator found the exact independent review artifact unmet",
            "approved": "approved decision has not yet produced gate evidence",
        }[decision_state]
        if gate["verificationType"] == "manual-review" and review_digest is None:
            reason = "manual review artifact is unavailable"
        evidence_digest = canonical_digest(decision)
        if decision_state == "approved":
            if gate["verificationType"] == "manual-review":
                state = "met"
                reason = "operator approved the exact independent review artifact"
            else:
                if command_capability is None:
                    raise GateEnforcementError("approved command decision lacks a trusted command capability")
                try:
                    command_audit = inspect_trusted_gate_approval(command_capability)
                except ApprovalBoundaryError as exc:
                    raise GateEnforcementError(str(exc)) from exc
                command_id = command_audit["approvalId"]
                if command_id in used_ids:
                    raise GateEnforcementError(f"command approvalId was reused: {command_id}")
                used_ids.add(command_id)
                command_reservation = _command_execution_reservation(
                    gate, decision, command_audit, run_id_value=ledger["runId"],
                    workspace=root, target_sha=target_sha,
                    live_platform=live_platform,
                    live_environment_digest=live_environment_digest,
                )
                _reserve_command_execution(broker, command_reservation)
                journal.record_command_execution_intent(command_reservation)
                kwargs: dict[str, Any] = {}
                if process_runner is not None:
                    kwargs["process_runner"] = process_runner
                if verified_at is not None:
                    kwargs["verified_at"] = verified_at
                result = run_approved_gates(
                    ledger, [command_capability], repository_root=root,
                    environment=environment, selected_gate_ids=[gate["id"]], **kwargs,
                )[0]
                state, reason = result.state, result.reason
                evidence_digest = result.evidence_digest
                execution_digest = result.execution_identity_digest
        try:
            receipt = journal.append({
                "runId": ledger["runId"], "gateId": gate["id"],
                "gateDefinitionDigest": canonical_digest(gate),
                "gateScope": gate["gateScope"], "storyId": gate["storyId"],
                "phase": "verification" if gate_scope == "story" else "post-merge-check",
                "workspace": str(root), "targetSha": target_sha,
                "livePlatform": live_platform,
                "liveEnvironmentDigest": live_environment_digest,
                "required": gate["required"], "verificationType": gate["verificationType"],
                "approvalId": approval_id, "approvedBy": decision["approvedBy"],
                "approvedAt": decision["approvedAt"],
                "operatorDecisionDigest": canonical_digest(decision),
                "executionIdentityDigest": execution_digest,
                "commandApprovalAudit": command_audit,
                "reviewArtifactDigest": review_digest,
                "state": state, "evidenceDigest": evidence_digest,
                "verifiedAt": now(), "reason": reason,
            }, seal_receipt=lambda candidate: _provider_seal(broker, candidate),
                authenticate_receipt=lambda candidate: _provider_authenticates(
                    broker, candidate
                ))
        except ValueError as exc:
            raise GateEnforcementError(f"gate-evidence publication failed: {exc}") from exc
        receipts.append(receipt)
        folded = _fold_journal(ledger, journal)
        current_checkpoint = _checkpoint(ledger["runId"], folded)
        anchored = _advance_checkpoint(broker, anchored, current_checkpoint)
        if command_reservation is not None:
            _complete_command_execution(broker, command_reservation, receipt)
        checkpoint_is_current = True
    blocking = [
        receipt for receipt in receipts
        if receipt["required"] and receipt["state"] != "met"
    ]
    reason = None
    if blocking:
        reason = (
            f"required {gate_scope} outcome gate {blocking[0]['gateId']!r} "
            f"is {blocking[0]['state']}: {blocking[0]['reason']}"
        )
    return GateEnforcementOutcome(not blocking, adopted, tuple(receipts), reason)


def require_gate_evidence_coverage(
    outcome_gate_ledger: Mapping[str, Any], *, gate_scope: str,
    story_id: str | None, workspace: Path, target_sha: str,
    environment: Mapping[str, str], provider: OperatorGateProvider,
    journal: GateEvidenceJournal, historical: bool = False,
) -> tuple[dict[str, Any], ...]:
    """Require authenticated met evidence for every required gate at one exact target."""

    broker = require_operator_provider(provider)
    ledger = validate_outcome_gate_ledger(outcome_gate_ledger)
    if gate_scope not in {"story", "root"}:
        raise GateEnforcementError("gate scope must be story or root")
    if (gate_scope == "story") != (story_id is not None):
        raise GateEnforcementError("story gate phases require exactly one story identity")
    root = _canonical_workspace(workspace)
    if not historical:
        _require_exact_clean_head(root, target_sha, environment)
    live_platform, live_environment_digest = _live_identity(environment)
    folded = _fold_journal(ledger, journal)
    anchored, current = _reconcile_checkpoint(broker, ledger["runId"], folded)
    _reconcile_command_execution_intents(broker, journal, folded, ledger["runId"])
    if current["receiptCount"] and anchored != current:
        raise GateEnforcementError(
            "gate-evidence journal has an unauthenticated tail beyond its provider checkpoint"
        )
    matched: list[dict[str, Any]] = []
    for gate in ledger["gates"]:
        if (
            not gate["required"]
            or gate["gateScope"] != gate_scope
            or gate["storyId"] != story_id
        ):
            continue
        _require_manual_live_identity(gate, live_platform, live_environment_digest)
        review_digest = None
        if not historical and gate["verificationType"] == "manual-review":
            review_digest = _manual_artifact_digest(gate, root)
        receipt = next((
            candidate for candidate in reversed(folded.receipts)
            if _adoptable(
                gate, candidate, workspace=root, target_sha=target_sha,
                environment=environment, review_digest=review_digest,
                provider=broker, live_platform=live_platform,
                live_environment_digest=live_environment_digest,
                revalidate_live=not historical,
            )
        ), None)
        if receipt is None:
            raise GateEnforcementError(
                f"required {gate_scope} gate {gate['id']!r} lacks authenticated met "
                f"evidence for target {target_sha}"
            )
        matched.append(copy.deepcopy(receipt))
    return tuple(matched)


__all__ = [
    "GateEnforcementError", "GateEnforcementOutcome", "OperatorGateProvider",
    "enforce_scope_gates", "require_gate_evidence_coverage",
    "require_operator_provider",
]
