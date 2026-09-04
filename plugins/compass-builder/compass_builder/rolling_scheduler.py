"""Pure deterministic scheduling decisions for the experimental rolling pipeline."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from ._limits import MAX_STORIES
from ._rolling_models import (
    MAX_GATE_EVIDENCE,
    _scopes_overlap,
    validate_rolling_execution_bundle,
    validate_rolling_state_bindings,
)
from ._validation import (
    ContractValidationError,
    array,
    canonical_digest,
    digest,
    enum,
    identifier,
    object_,
    strings,
    timestamp,
)


DECISION_EVIDENCE_VERSION = "compass-builder.scheduler-decision-evidence.v1"
GATE_READINESS_STATUSES = {"actionable", "waiting", "blocked"}
PROTECTED_LIFECYCLES = {
    "running",
    "process-unknown",
    "worker-complete-unverified",
    "verified-unimported",
    "imported-awaiting-integration",
    "merged-awaiting-post-check",
}


class SchedulerDecisionError(ValueError):
    """A rolling scheduler input is malformed, stale, or not exactly bound."""


def _reject(path: str, message: str) -> None:
    raise SchedulerDecisionError(f"{path}: {message}")


def _require_mapping(value: Any, path: str) -> None:
    if not isinstance(value, Mapping):
        _reject(path, "must be a Mapping/decoded JSON object")


def _normalize_decision_evidence(
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
    host: Mapping[str, Any],
    planning_timestamp: str,
) -> dict[str, Any]:
    try:
        evidence = copy.deepcopy(dict(value))
    except (TypeError, ValueError) as exc:
        raise SchedulerDecisionError(
            "decisionEvidence: must be a decoded JSON object"
        ) from exc
    object_(
        evidence,
        "decisionEvidence",
        {
            "schemaVersion",
            "planDigest",
            "hostEvidenceDigest",
            "gatePolicyDigest",
            "observedAt",
            "validUntil",
            "gateReadiness",
        },
    )
    if evidence["schemaVersion"] != DECISION_EVIDENCE_VERSION:
        _reject(
            "decisionEvidence.schemaVersion",
            f"expected exact version {DECISION_EVIDENCE_VERSION!r}",
        )
    for field in ("planDigest", "hostEvidenceDigest", "gatePolicyDigest"):
        digest(evidence[field], f"decisionEvidence.{field}")

    expected_plan_digest = canonical_digest(plan)
    if evidence["planDigest"] != expected_plan_digest:
        _reject(
            "decisionEvidence.planDigest",
            "does not bind the canonical validated pipeline plan",
        )
    if evidence["hostEvidenceDigest"] != plan["hostEvidenceDigest"]:
        _reject(
            "decisionEvidence.hostEvidenceDigest",
            "does not bind the plan's exact host evidence",
        )
    if evidence["gatePolicyDigest"] != plan["gatePolicyDigest"]:
        _reject(
            "decisionEvidence.gatePolicyDigest",
            "does not bind the plan's exact gate policy",
        )

    observed_at = timestamp(evidence["observedAt"], "decisionEvidence.observedAt")
    valid_until = timestamp(evidence["validUntil"], "decisionEvidence.validUntil")
    planning = timestamp(planning_timestamp, "executionBundle.planningTimestamp")
    host_valid_until = timestamp(host["validUntil"], "hostCapabilities.validUntil")
    if observed_at < planning:
        _reject(
            "decisionEvidence.observedAt",
            "predates executionBundle.planningTimestamp",
        )
    if observed_at > valid_until:
        _reject(
            "decisionEvidence.validUntil",
            "is stale at the injected observedAt timestamp",
        )
    if observed_at > host_valid_until:
        _reject(
            "hostCapabilities.validUntil",
            "host evidence is stale at decisionEvidence.observedAt",
        )

    raw_items = array(
        evidence["gateReadiness"],
        "decisionEvidence.gateReadiness",
        minimum=1,
        maximum=MAX_STORIES,
    )
    by_story: dict[str, dict[str, Any]] = {}
    for index, raw_item in enumerate(raw_items):
        path = f"decisionEvidence.gateReadiness[{index}]"
        item = object_(
            raw_item,
            path,
            {
                "storyId",
                "status",
                "requiredOutcomeGateIds",
                "approvalDigests",
            },
        )
        story_id = identifier(item["storyId"], f"{path}.storyId")
        if story_id in by_story:
            _reject(
                "decisionEvidence.gateReadiness",
                f"contains duplicate story identity {story_id!r}",
            )
        enum(item["status"], f"{path}.status", GATE_READINESS_STATUSES)
        required_gate_ids = strings(
            item["requiredOutcomeGateIds"],
            f"{path}.requiredOutcomeGateIds",
            maximum=64,
            items_maximum=MAX_GATE_EVIDENCE,
        )
        for gate_index, gate_id in enumerate(required_gate_ids):
            identifier(gate_id, f"{path}.requiredOutcomeGateIds[{gate_index}]")
        approvals = strings(
            item["approvalDigests"],
            f"{path}.approvalDigests",
            maximum=80,
            items_maximum=MAX_GATE_EVIDENCE,
        )
        for approval_index, approval in enumerate(approvals):
            digest(approval, f"{path}.approvalDigests[{approval_index}]")
        by_story[story_id] = copy.deepcopy(item)

    planned_ids = [story["storyId"] for story in plan["stories"]]
    unknown = sorted(set(by_story) - set(planned_ids))
    missing = [story_id for story_id in planned_ids if story_id not in by_story]
    if unknown:
        _reject(
            "decisionEvidence.gateReadiness",
            f"contains unknown story identities: {', '.join(unknown)}",
        )
    if missing:
        _reject(
            "decisionEvidence.gateReadiness",
            f"is missing planned story identities: {', '.join(missing)}",
        )
    if len(raw_items) != len(planned_ids):
        _reject(
            "decisionEvidence.gateReadiness",
            "must contain exactly one item per planned story",
        )

    normalized_items = []
    for planned in plan["stories"]:
        story_id = planned["storyId"]
        item = by_story[story_id]
        if item["requiredOutcomeGateIds"] != planned["requiredOutcomeGateIds"]:
            _reject(
                f"decisionEvidence.gateReadiness.{story_id}.requiredOutcomeGateIds",
                "does not preserve the story's exact ordered gate identities",
            )
        if item["status"] == "actionable":
            if len(item["approvalDigests"]) != len(
                planned["requiredOutcomeGateIds"]
            ):
                _reject(
                    f"decisionEvidence.gateReadiness.{story_id}.approvalDigests",
                    "must contain one ordered approval digest per required gate",
                )
        elif item["approvalDigests"]:
            _reject(
                f"decisionEvidence.gateReadiness.{story_id}.approvalDigests",
                "waiting or blocked evidence cannot grant or smuggle approval",
            )
        normalized_items.append(copy.deepcopy(item))
    evidence["gateReadiness"] = normalized_items
    return evidence


def _bindings(
    bundle: Mapping[str, Any],
    state: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, str]:
    plan = bundle["pipelinePlan"]
    return {
        "decisionEvidenceDigest": canonical_digest(evidence),
        "executionBundleDigest": canonical_digest(bundle),
        "gatePolicyDigest": plan["gatePolicyDigest"],
        "hostEvidenceDigest": plan["hostEvidenceDigest"],
        "pipelineStateDigest": canonical_digest(state),
        "planDigest": canonical_digest(plan),
        "schedulingPolicyDigest": plan["schedulingPolicyDigest"],
    }


def _has_scope_conflict(
    candidate_scopes: list[str], protected_scope_sets: list[list[str]]
) -> bool:
    return any(
        _scopes_overlap(candidate_scope, protected_scope)
        for candidate_scope in candidate_scopes
        for protected_scopes in protected_scope_sets
        for protected_scope in protected_scopes
    )


def _dispatch_candidate(
    source: Mapping[str, Any],
    planned: Mapping[str, Any],
    state_by_id: Mapping[str, Mapping[str, Any]],
    readiness: Mapping[str, Any],
    bundle: Mapping[str, Any],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    prerequisites = []
    for prerequisite_id in planned["dependsOn"]:
        prerequisite = state_by_id[prerequisite_id]
        prerequisites.append(
            {
                "storyId": prerequisite_id,
                "workerReceiptDigest": prerequisite["workerReceiptDigest"],
                "integrationEvidenceDigest": prerequisite[
                    "postCheckEvidenceDigest"
                ],
                "gateEvidenceDigests": copy.deepcopy(
                    prerequisite["gateEvidenceDigests"]
                ),
            }
        )
    return {
        "storyId": planned["storyId"],
        "specificationOrder": planned["specificationOrder"],
        "priority": source["priority"],
        "workerStartSha": state["lastVerifiedIntegrationSha"],
        "exactModel": bundle["hostCapabilities"]["selectedModel"],
        "recommendedEffort": planned["recommendedEffort"],
        "handoffDigest": planned["handoffDigest"],
        "writeScopes": copy.deepcopy(planned["writeScopes"]),
        "requiredOutcomeGateIds": copy.deepcopy(
            planned["requiredOutcomeGateIds"]
        ),
        "gateApprovalDigests": copy.deepcopy(readiness["approvalDigests"]),
        "prerequisites": prerequisites,
    }


def _decide_validated(
    bundle: dict[str, Any],
    state: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    spec = bundle["runSpec"]
    plan = bundle["pipelinePlan"]
    bindings = _bindings(bundle, state, evidence)
    plan_by_id = {story["storyId"]: story for story in plan["stories"]}
    source_by_id = {story["id"]: story for story in spec["stories"]}
    state_by_id = {story["storyId"]: story for story in state["stories"]}
    readiness_by_id = {
        item["storyId"]: item for item in evidence["gateReadiness"]
    }

    if state["state"] == "blocked" or state["activeBlocker"] is not None:
        blocked_story_ids = [
            story["storyId"]
            for story in state["stories"]
            if story["lifecycle"] == "blocked"
        ]
        return {
            "action": "block",
            "bindings": bindings,
            "reason": "durable-blocker",
            "storyIds": blocked_story_ids,
        }

    gate_blocked = [
        story["storyId"]
        for story in plan["stories"]
        if readiness_by_id[story["storyId"]]["status"] == "blocked"
    ]
    if gate_blocked:
        return {
            "action": "block",
            "bindings": bindings,
            "reason": "gate-readiness-blocked",
            "storyIds": gate_blocked,
        }

    if (
        all(
            story["lifecycle"] == "integration-verified"
            for story in state["stories"]
        )
        and not state["activeOwners"]
        and not state["integrationQueue"]
    ):
        return {
            "action": "complete",
            "bindings": bindings,
            "storyIds": [story["storyId"] for story in plan["stories"]],
        }

    verified_count = 0
    for story in state["stories"]:
        if story["lifecycle"] != "integration-verified":
            break
        verified_count += 1
    next_ordinal = verified_count + 1
    for story_id in state["integrationQueue"]:
        planned = plan_by_id[story_id]
        if planned["integrationOrdinal"] == next_ordinal:
            return {
                "action": "integrate",
                "bindings": bindings,
                "storyId": story_id,
                "integrationOrdinal": next_ordinal,
            }

    active_count = len(state["activeOwners"])
    if active_count > plan["concurrency"]:
        _reject(
            "pipelineState.activeOwners",
            "exceeds the validated plan concurrency",
        )
    available_capacity = plan["concurrency"] - active_count
    if available_capacity > 0 and state["state"] in {"planned", "running"}:
        protected_scope_sets = [
            copy.deepcopy(plan_by_id[story["storyId"]]["writeScopes"])
            for story in state["stories"]
            if story["lifecycle"] in PROTECTED_LIFECYCLES
        ]
        ordered_candidates = sorted(
            (
                (
                    source_by_id[planned["storyId"]]["priority"],
                    planned["specificationOrder"],
                    planned,
                )
                for planned in plan["stories"]
                if state_by_id[planned["storyId"]]["lifecycle"]
                == "never-launched"
            ),
            key=lambda item: (item[0], item[1]),
        )
        selected = []
        selected_scope_sets: list[list[str]] = []
        for _, _, planned in ordered_candidates:
            story_id = planned["storyId"]
            source = source_by_id[story_id]
            readiness = readiness_by_id[story_id]
            if any(
                state_by_id[dependency]["lifecycle"] != "integration-verified"
                for dependency in planned["dependsOn"]
            ):
                continue
            if source["sharedState"]["mode"] == "mutates":
                continue
            if readiness["status"] != "actionable":
                continue
            if _has_scope_conflict(
                planned["writeScopes"], protected_scope_sets + selected_scope_sets
            ):
                continue
            selected.append(
                _dispatch_candidate(
                    source,
                    planned,
                    state_by_id,
                    readiness,
                    bundle,
                    state,
                )
            )
            selected_scope_sets.append(copy.deepcopy(planned["writeScopes"]))
            if len(selected) == available_capacity:
                break
        if selected:
            return {
                "action": "dispatch",
                "bindings": bindings,
                "proposalType": "eligibility-proposal-only",
                "candidates": selected,
            }

    return {
        "action": "wait",
        "bindings": bindings,
        "reason": "no-eligible-action",
    }


def decide(
    execution_bundle: Mapping[str, Any],
    pipeline_state: Mapping[str, Any],
    decision_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Return one closed eligibility decision without granting execution authority."""

    try:
        _require_mapping(execution_bundle, "executionBundle")
        _require_mapping(pipeline_state, "pipelineState")
        _require_mapping(decision_evidence, "decisionEvidence")
        bundle = validate_rolling_execution_bundle(execution_bundle)
        plan = bundle["pipelinePlan"]
        _, state = validate_rolling_state_bindings(plan, pipeline_state)
        if plan["dispatchStrategy"] != "rolling":
            _reject(
                "pipelinePlan.dispatchStrategy",
                "the rolling scheduler accepts only the exact 'rolling' strategy",
            )
        evidence = _normalize_decision_evidence(
            decision_evidence,
            plan,
            bundle["hostCapabilities"],
            bundle["planningTimestamp"],
        )
        return _decide_validated(bundle, state, evidence)
    except SchedulerDecisionError:
        raise
    except ContractValidationError as exc:
        raise SchedulerDecisionError(f"scheduler input invalid: {exc}") from exc


__all__ = [
    "DECISION_EVIDENCE_VERSION",
    "SchedulerDecisionError",
    "decide",
]
