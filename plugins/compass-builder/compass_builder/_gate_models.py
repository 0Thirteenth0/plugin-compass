"""Outcome-gate ledger contract validation.

Gate definitions are controller-owned data.  This module validates their shape and
semantics; it deliberately does not approve or execute gate commands.
"""

from __future__ import annotations

import re
from typing import Any

from ._validation import (
    array,
    boolean,
    digest,
    enum,
    fail,
    identifier,
    object_,
    run_id,
    scope,
    string,
    timestamp,
)


GATE_STATES = {"pending", "met", "unmet", "blocked", "abandoned"}
GATE_SCOPES = {"story", "root"}
VERIFICATION_TYPES = {"command", "manual-review"}
REQUIREMENT_ID_RE = re.compile(r"^R[0-9]{3,}$")
ACCEPTANCE_ID_RE = re.compile(r"^A[0-9]{3,}$")
COMMAND_MARKER_RE = re.compile(
    r"^(?:stdout-exact:.+|artifact-sha256:[0-9a-f]{64})$",
    re.DOTALL,
)
NO_OP_COMMAND_RE = re.compile(
    r"^(?:true|:|exit\s+0|echo\s+(?:ok|success|passed)|"
    r"write-output\s+(?:ok|success|passed))$",
    re.IGNORECASE,
)
MAX_GATES = 512
MAX_COVERAGE_IDS = 256


def _coverage_ids(value: Any, path: str, pattern: re.Pattern[str], kind: str) -> list[str]:
    items = array(value, path, maximum=MAX_COVERAGE_IDS)
    result: list[str] = []
    for index, item in enumerate(items):
        item = string(item, f"{path}[{index}]", maximum=64)
        if not pattern.fullmatch(item):
            fail(
                f"{path}[{index}]",
                f"is not a stable {kind} ID",
                f"use the controller ledger's canonical {kind} ID",
            )
        result.append(item)
    if len(set(result)) != len(result):
        fail(path, "contains duplicate IDs", "retain each covered ID once")
    return result


def _working_directory(value: Any, path: str) -> str:
    if value == ".":
        return value
    return scope(value, path)


def _optional_string(value: Any, path: str, *, maximum: int = 4096) -> str | None:
    if value is None:
        return None
    return string(value, path, maximum=maximum)


def _gate(value: Any, path: str, ledger_run_id: str) -> dict[str, Any]:
    gate = object_(
        value,
        path,
        {
            "id",
            "gateScope",
            "storyId",
            "observableOutcome",
            "coveredRequirementIds",
            "coveredAcceptanceIds",
            "verificationType",
            "command",
            "independentReviewPath",
            "successMarker",
            "workingDirectory",
            "shell",
            "platform",
            "environmentDigest",
            "risk",
            "validationStrength",
            "required",
            "state",
            "evidenceDigest",
            "validatedAt",
            "verificationRunId",
            "handoffReason",
        },
    )
    identifier(gate["id"], f"{path}.id")
    gate_scope = enum(gate["gateScope"], f"{path}.gateScope", GATE_SCOPES)
    if gate_scope == "story":
        if gate["storyId"] is None:
            fail(f"{path}.storyId", "is required for a story gate", "bind the gate to its stable story ID")
        identifier(gate["storyId"], f"{path}.storyId")
    elif gate["storyId"] is not None:
        fail(f"{path}.storyId", "must be null for a root gate", "remove the story binding")

    string(gate["observableOutcome"], f"{path}.observableOutcome", maximum=2000)
    covered_requirements = _coverage_ids(
        gate["coveredRequirementIds"],
        f"{path}.coveredRequirementIds",
        REQUIREMENT_ID_RE,
        "requirement",
    )
    covered_acceptance = _coverage_ids(
        gate["coveredAcceptanceIds"],
        f"{path}.coveredAcceptanceIds",
        ACCEPTANCE_ID_RE,
        "acceptance",
    )
    if not covered_requirements and not covered_acceptance:
        fail(
            f"{path}.coveredRequirementIds",
            "gate does not cover any requirement or acceptance ID",
            "map the observable outcome to at least one declared ledger ID",
        )

    verification_type = enum(
        gate["verificationType"], f"{path}.verificationType", VERIFICATION_TYPES
    )
    command = _optional_string(gate["command"], f"{path}.command")
    review_path = gate["independentReviewPath"]
    marker = string(gate["successMarker"], f"{path}.successMarker", maximum=1024)
    shell = gate["shell"]
    validation_strength = enum(
        gate["validationStrength"],
        f"{path}.validationStrength",
        {"none", "partial", "decisive"},
    )
    if verification_type == "command":
        if command is None:
            fail(f"{path}.command", "is required for a command gate", "record the exact reviewed command")
        if NO_OP_COMMAND_RE.fullmatch(command):
            fail(f"{path}.command", "is a success-insensitive no-op", "use a command that observes the stated outcome")
        if review_path is not None:
            fail(f"{path}.independentReviewPath", "must be null for a command gate", "remove the manual review path")
        string(shell, f"{path}.shell", maximum=160)
        if not COMMAND_MARKER_RE.fullmatch(marker):
            fail(
                f"{path}.successMarker",
                "is not a decisive command success marker",
                "use stdout-exact:<value> or artifact-sha256:<digest>",
            )
        if validation_strength != "decisive":
            fail(
                f"{path}.validationStrength",
                "runnable gates must be decisive",
                "strengthen the oracle or use an explicit independent manual review",
            )
    else:
        if command is not None:
            fail(f"{path}.command", "must be null for manual review", "remove the executable command")
        if shell is not None:
            fail(f"{path}.shell", "must be null for manual review", "remove the unused shell identity")
        if review_path is None:
            fail(
                f"{path}.independentReviewPath",
                "is required for manual review",
                "record the repository-relative independent review path",
            )
        scope(review_path, f"{path}.independentReviewPath")
        if marker != "review-decision:approved":
            fail(
                f"{path}.successMarker",
                "is not the decisive manual-review marker",
                "use review-decision:approved",
            )
        if validation_strength == "none":
            fail(
                f"{path}.validationStrength",
                "manual review cannot have no validation strength",
                "record partial or decisive review strength",
            )

    _working_directory(gate["workingDirectory"], f"{path}.workingDirectory")
    string(gate["platform"], f"{path}.platform", maximum=160)
    digest(gate["environmentDigest"], f"{path}.environmentDigest")
    enum(gate["risk"], f"{path}.risk", {"low", "medium", "high"})
    required = boolean(gate["required"], f"{path}.required")
    state = enum(gate["state"], f"{path}.state", GATE_STATES)

    evidence_values = (
        gate["evidenceDigest"],
        gate["validatedAt"],
        gate["verificationRunId"],
    )
    has_any_evidence = any(item is not None for item in evidence_values)
    has_all_evidence = all(item is not None for item in evidence_values)
    if has_any_evidence and not has_all_evidence:
        missing_fields = ("evidenceDigest", "validatedAt", "verificationRunId")
        missing = next(name for name, item in zip(missing_fields, evidence_values) if item is None)
        fail(f"{path}.{missing}", "partial evidence identity is invalid", "record all evidence identity fields or none")
    if has_all_evidence:
        digest(gate["evidenceDigest"], f"{path}.evidenceDigest")
        timestamp(gate["validatedAt"], f"{path}.validatedAt")
        run_id(gate["verificationRunId"], f"{path}.verificationRunId")
        if gate["verificationRunId"] != ledger_run_id:
            fail(
                f"{path}.verificationRunId",
                "does not match the ledger run identity",
                "record evidence from this immutable controller run",
            )
    if state in {"met", "unmet"} and not has_all_evidence:
        fail(f"{path}.evidenceDigest", f"state {state!r} requires complete evidence identity", "record the digest, timestamp, and run ID")
    if state in {"pending", "abandoned"} and has_any_evidence:
        fail(f"{path}.evidenceDigest", f"state {state!r} cannot claim verification evidence", "clear the evidence fields")

    handoff_reason = _optional_string(gate["handoffReason"], f"{path}.handoffReason", maximum=2000)
    if state in {"pending", "met", "unmet"} and handoff_reason is not None:
        fail(f"{path}.handoffReason", f"must be null for state {state!r}", "clear the non-success handoff reason")
    if required and state in {"blocked", "abandoned"} and handoff_reason is None:
        fail(
            f"{path}.handoffReason",
            "is required for a blocked or abandoned required gate",
            "record a non-empty owner-facing handoff reason",
        )
    return gate


def validate_outcome_gate_ledger_shape(data: dict[str, Any]) -> None:
    """Validate one closed controller-owned outcome-gate ledger."""
    object_(
        data,
        "$",
        {
            "schemaVersion",
            "controller",
            "runId",
            "requiredRequirementIds",
            "requiredAcceptanceIds",
            "gates",
        },
    )
    if data["controller"] != "compass-builder":
        fail("$.controller", "must identify the controller owner", "use the immutable value 'compass-builder'")
    ledger_run_id = run_id(data["runId"], "$.runId")
    required_requirements = _coverage_ids(
        data["requiredRequirementIds"],
        "$.requiredRequirementIds",
        REQUIREMENT_ID_RE,
        "requirement",
    )
    required_acceptance = _coverage_ids(
        data["requiredAcceptanceIds"],
        "$.requiredAcceptanceIds",
        ACCEPTANCE_ID_RE,
        "acceptance",
    )
    if not required_requirements and not required_acceptance:
        fail("$.requiredRequirementIds", "ledger declares no required coverage", "declare at least one requirement or acceptance ID")

    gates = [
        _gate(item, f"$.gates[{index}]", ledger_run_id)
        for index, item in enumerate(array(data["gates"], "$.gates", minimum=1, maximum=MAX_GATES))
    ]
    gate_ids = [gate["id"] for gate in gates]
    if len(set(gate_ids)) != len(gate_ids):
        fail("$.gates", "contains duplicate gate IDs", "assign one stable ID to each gate")

    requirement_set = set(required_requirements)
    acceptance_set = set(required_acceptance)
    for index, gate in enumerate(gates):
        unknown_requirements = set(gate["coveredRequirementIds"]) - requirement_set
        unknown_acceptance = set(gate["coveredAcceptanceIds"]) - acceptance_set
        if unknown_requirements:
            fail(
                f"$.gates[{index}].coveredRequirementIds",
                f"references undeclared requirement IDs: {', '.join(sorted(unknown_requirements))}",
                "declare the IDs in requiredRequirementIds or remove the mapping",
            )
        if unknown_acceptance:
            fail(
                f"$.gates[{index}].coveredAcceptanceIds",
                f"references undeclared acceptance IDs: {', '.join(sorted(unknown_acceptance))}",
                "declare the IDs in requiredAcceptanceIds or remove the mapping",
            )

    required_gates = [gate for gate in gates if gate["required"]]
    covered_requirements = {
        item for gate in required_gates for item in gate["coveredRequirementIds"]
    }
    covered_acceptance = {
        item for gate in required_gates for item in gate["coveredAcceptanceIds"]
    }
    missing_requirements = requirement_set - covered_requirements
    missing_acceptance = acceptance_set - covered_acceptance
    if missing_requirements:
        fail(
            "$.gates",
            f"missing required requirement coverage: {', '.join(sorted(missing_requirements))}",
            "map each required requirement ID to at least one required gate",
        )
    if missing_acceptance:
        fail(
            "$.gates",
            f"missing required acceptance coverage: {', '.join(sorted(missing_acceptance))}",
            "map each required acceptance ID to a runnable gate or independent manual review",
        )
