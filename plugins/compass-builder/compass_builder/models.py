"""Public validation and canonicalization API for Compass Builder contracts."""

from __future__ import annotations

import copy
from typing import Any, Callable, Mapping, Sequence

from ._benchmark_models import (
    CONTROL_FIELDS,
    bind_aggregate_receipts,
    validate_aggregate_shape,
    validate_benchmark_receipt_shape,
    validate_workloads_shape,
)
from ._benchmark_usage_models import (
    ATTEMPT_USAGE_VERSION,
    TOKEN_REPORT_VERSION,
    validate_benchmark_attempt_usage_schema_semantics,
    validate_benchmark_attempt_usage_shape,
    validate_benchmark_token_report_shape,
)
from ._receipt_models import validate_worker_receipt_shape
from ._rolling_models import (
    DISPATCH_RECORD_VERSION,
    EXECUTION_BUNDLE_V2_VERSION,
    PIPELINE_EVENT_VERSION,
    PIPELINE_PLAN_VERSION,
    PIPELINE_STATE_VERSION,
    RUN_SPEC_V2_VERSION,
    validate_dispatch_record_bindings,
    validate_dispatch_record_shape,
    validate_execution_bundle_v2_shape,
    validate_pipeline_event_chain,
    validate_pipeline_event_shape,
    validate_pipeline_plan_shape,
    validate_pipeline_state_shape,
    validate_rolling_execution_bundle,
    validate_rolling_plan_bindings,
    validate_rolling_state_bindings,
    validate_run_spec_v2_shape,
)
from ._retry_models import RETRY_EVIDENCE_VERSION, validate_retry_evidence_shape
from ._gate_models import validate_outcome_gate_ledger_shape
from ._usage_models import (
    validate_worker_usage_schema_semantics, validate_worker_usage_shape,
)
from ._run_models import (
    validate_host_shape, validate_plan_safety, validate_run_spec_shape,
    validate_wave_plan_shape,
)
from ._state_models import validate_run_state_shape, validate_transition_evidence
from ._validation import ContractValidationError, canonical_data, canonical_digest, fail, object_, timestamp


SCHEMA_VERSIONS = {
    "run-spec": "compass-builder.run-spec.v1",
    "wave-plan": "compass-builder.wave-plan.v1",
    "run-state": "compass-builder.run-state.v1",
    "host-capabilities": "compass-builder.host-capabilities.v1",
    "worker-receipt": "compass-builder.worker-receipt.v1",
    "worker-usage": "compass-builder.worker-usage.v1",
    "retry-evidence": RETRY_EVIDENCE_VERSION,
    "benchmark-receipt": "compass-builder.benchmark-receipt.v1",
    "benchmark-workloads": "compass-builder.benchmark-workloads.v1",
    "benchmark-aggregate": "compass-builder.benchmark-aggregate.v1",
    "benchmark-attempt-usage": ATTEMPT_USAGE_VERSION,
    "benchmark-token-report": TOKEN_REPORT_VERSION,
    "outcome-gate-ledger": "compass-builder.outcome-gate-ledger.v1",
    "run-spec-v2": RUN_SPEC_V2_VERSION,
    "pipeline-plan": PIPELINE_PLAN_VERSION,
    "pipeline-state": PIPELINE_STATE_VERSION,
    "pipeline-event": PIPELINE_EVENT_VERSION,
    "execution-bundle-v2": EXECUTION_BUNDLE_V2_VERSION,
    "dispatch-record": DISPATCH_RECORD_VERSION,
}
VALIDATORS: dict[str, Callable[[dict[str, Any]], None]] = {
    "run-spec": validate_run_spec_shape,
    "wave-plan": validate_wave_plan_shape,
    "run-state": validate_run_state_shape,
    "host-capabilities": validate_host_shape,
    "worker-receipt": validate_worker_receipt_shape,
    "worker-usage": validate_worker_usage_shape,
    "retry-evidence": validate_retry_evidence_shape,
    "benchmark-receipt": validate_benchmark_receipt_shape,
    "benchmark-workloads": validate_workloads_shape,
    "benchmark-aggregate": validate_aggregate_shape,
    "benchmark-attempt-usage": validate_benchmark_attempt_usage_shape,
    "benchmark-token-report": validate_benchmark_token_report_shape,
    "outcome-gate-ledger": validate_outcome_gate_ledger_shape,
    "run-spec-v2": validate_run_spec_v2_shape,
    "pipeline-plan": validate_pipeline_plan_shape,
    "pipeline-state": validate_pipeline_state_shape,
    "pipeline-event": validate_pipeline_event_shape,
    "execution-bundle-v2": validate_execution_bundle_v2_shape,
    "dispatch-record": validate_dispatch_record_shape,
}


def normalize_contract(contract: str, value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a detached JSON-compatible contract object."""
    if contract not in VALIDATORS:
        fail("$contract", f"unsupported contract {contract!r}", f"choose one of {sorted(VALIDATORS)}")
    if not isinstance(value, Mapping):
        fail("$", "must be a JSON object mapping", "provide a decoded JSON object, not key-value pairs")
    try:
        data = copy.deepcopy(dict(value))
    except (TypeError, ValueError):
        fail("$", "must be a mapping", "provide a decoded JSON object")
    object_(data, "$", {"schemaVersion"}, set(data) - {"schemaVersion"})
    expected = SCHEMA_VERSIONS[contract]
    if data["schemaVersion"] != expected:
        fail("$.schemaVersion", f"expected immutable version {expected!r}", f"use {expected!r} and migrate the complete shape")
    VALIDATORS[contract](data)
    try:
        canonical_data(data)
    except (TypeError, ValueError) as exc:
        fail("$", f"is not canonical JSON data ({exc})", "use only finite JSON values")
    return data


def validate_contract(contract: str, value: Mapping[str, Any]) -> dict[str, Any]:
    return normalize_contract(contract, value)


def canonical_json(value: Mapping[str, Any], contract: str | None = None) -> bytes:
    data = normalize_contract(contract, value) if contract else copy.deepcopy(dict(value))
    return canonical_data(data)


def _validator(contract: str) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    def validate(value: Mapping[str, Any]) -> dict[str, Any]:
        return normalize_contract(contract, value)
    validate.__name__ = f"validate_{contract.replace('-', '_')}"
    return validate


validate_run_spec = _validator("run-spec")
validate_wave_plan = _validator("wave-plan")
validate_run_state = _validator("run-state")
validate_host_capabilities = _validator("host-capabilities")
validate_worker_receipt = _validator("worker-receipt")
validate_worker_usage = _validator("worker-usage")
validate_retry_evidence = _validator("retry-evidence")
validate_benchmark_receipt = _validator("benchmark-receipt")
validate_benchmark_workloads = _validator("benchmark-workloads")
validate_benchmark_aggregate = _validator("benchmark-aggregate")
validate_benchmark_attempt_usage = _validator("benchmark-attempt-usage")
validate_benchmark_token_report = _validator("benchmark-token-report")
validate_outcome_gate_ledger = _validator("outcome-gate-ledger")
validate_run_spec_v2 = _validator("run-spec-v2")
validate_pipeline_plan = _validator("pipeline-plan")
validate_pipeline_state = _validator("pipeline-state")
validate_pipeline_event = _validator("pipeline-event")
validate_execution_bundle_v2 = _validator("execution-bundle-v2")
validate_dispatch_record = _validator("dispatch-record")


def validate_worker_usage_with_schema(
    schema: Mapping[str, Any], value: Mapping[str, Any]
) -> dict[str, Any]:
    """Require both the Python structural contract and declared schema semantics."""

    normalized = validate_worker_usage(value)
    validate_worker_usage_schema_semantics(schema, normalized)
    return normalized


def validate_benchmark_attempt_usage_with_schema(
    schema: Mapping[str, Any], value: Mapping[str, Any]
) -> dict[str, Any]:
    """Require Python validation plus the schema-declared sibling equality."""

    normalized = validate_benchmark_attempt_usage(value)
    validate_benchmark_attempt_usage_schema_semantics(schema, normalized)
    return normalized


def run_binding_digest(
    run_spec: Mapping[str, Any], wave_plan: Mapping[str, Any]
) -> str:
    """Digest the closed canonical {runSpec, wavePlan} immutable binding."""
    spec = validate_run_spec(run_spec)
    plan = validate_wave_plan(wave_plan)
    return canonical_digest({"runSpec": spec, "wavePlan": plan})


def validate_host_capabilities_at(
    host_capabilities: Mapping[str, Any], planning_timestamp: str
) -> dict[str, Any]:
    """Validate native capability evidence against an explicit planning time."""
    host = validate_host_capabilities(host_capabilities)
    planning = timestamp(planning_timestamp, "planningTimestamp")
    captured = timestamp(host["capturedAt"], "hostCapabilities.capturedAt")
    valid_until = timestamp(host["validUntil"], "hostCapabilities.validUntil")
    if planning < captured:
        fail("planningTimestamp", "precedes capability capture time", "use evidence captured no later than planning")
    if planning > valid_until:
        fail("hostCapabilities.validUntil", "capability evidence is stale at planningTimestamp", "capture fresh native capability evidence")
    return host


def validate_run_structure_bindings(
    run_spec: Mapping[str, Any],
    wave_plan: Mapping[str, Any],
    run_state: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """Check document structure/bindings without claiming execution readiness."""
    spec = validate_run_spec(run_spec)
    plan = validate_wave_plan(wave_plan)
    for field in ("runId", "baseSha", "integrationBranch", "integrationExpectedSha", "effortPolicyVersion"):
        if spec[field] != plan[field]:
            fail(f"wavePlan.{field}", f"does not match runSpec.{field}", "re-plan from the same immutable run spec")
    expected_input_digest = canonical_digest(spec)
    if plan["normalizedInputDigest"] != expected_input_digest:
        fail("wavePlan.normalizedInputDigest", "does not bind the canonical normalized run spec", "recompute SHA-256 over canonical validated run-spec bytes")
    expected_ids = [story["id"] for story in spec["stories"]]
    if [story["storyId"] for story in plan["stories"]] != expected_ids:
        fail("wavePlan.stories", "does not preserve ordered run-spec story IDs", "bind every run-spec story exactly once in order")
    ceiling = min(spec["hostConcurrencyCeiling"], spec["userConcurrencyCeiling"])
    if plan["concurrency"] > ceiling:
        fail("wavePlan.concurrency", "exceeds the run-spec host/user ceiling", f"use concurrency <= {ceiling}")
    if plan["mode"] == "sequential" and plan["concurrency"] != 1:
        fail("wavePlan.concurrency", "sequential mode requires concurrency 1", "set concurrency to 1")
    if plan["mode"] == "parallel" and plan["concurrency"] < 2:
        fail("wavePlan.concurrency", "parallel mode requires concurrency >= 2", "select sequential or raise the validated ceiling")
    if spec["mode"] != "auto" and plan["mode"] != spec["mode"]:
        fail("wavePlan.mode", "does not honor the explicit run-spec mode", "use the explicitly requested mode")
    validate_plan_safety(spec, plan)
    wave_by_story: dict[str, int] = {}
    for wave_index, wave in enumerate(plan["waves"]):
        for story_id in wave["storyIds"]:
            wave_by_story[story_id] = wave_index
    for story in spec["stories"]:
        story_wave = wave_by_story[story["id"]]
        for dependency in story["dependsOn"]:
            if wave_by_story[dependency] >= story_wave:
                fail(f"wavePlan.waves[{story_wave}].storyIds", f"story {story['id']!r} is not dependency-ready; {dependency!r} is not in an earlier wave", "move every dependency to an earlier wave")

    state = None
    if run_state is not None:
        state = validate_run_state(run_state)
        expected_binding_digest = run_binding_digest(spec, plan)
        if state["runBindingDigest"] != expected_binding_digest:
            fail("runState.runBindingDigest", "does not bind the canonical run spec and wave plan", "recompute SHA-256 over the canonical {runSpec, wavePlan} object")
        for field in ("runId", "baseSha", "integrationBranch"):
            if state[field] != plan[field]:
                fail(f"runState.{field}", f"does not match wavePlan.{field}", "load state belonging to the same immutable plan")
        if state["initialIntegrationSha"] != plan["integrationExpectedSha"]:
            fail("runState.initialIntegrationSha", "does not match planned integrationExpectedSha", "initialize state from the planned integration head")
        if len(state["waves"]) > len(plan["waves"]):
            fail("runState.waves", "contains more ledgers than the immutable plan", "retain only planned materialized waves")
        if state["state"] == "completed" and len(state["waves"]) != len(plan["waves"]):
            fail("runState.waves", "completed state is missing planned waves", "materialize and verify every planned wave")
        planned_branches = {story["storyId"]: story["branch"] for story in plan["stories"]}
        for index, (state_wave, plan_wave) in enumerate(zip(state["waves"], plan["waves"])):
            if [entry["storyId"] for entry in state_wave["branches"]] != plan_wave["storyIds"]:
                fail(f"runState.waves[{index}].branches", "does not match planned story order", "preserve every planned branch entry in order")
            for entry in state_wave["branches"]:
                if entry["branch"] != planned_branches[entry["storyId"]]:
                    fail(f"runState.waves[{index}].branches.{entry['storyId']}.branch", "does not match the planned immutable branch", "use the exact planned branch ID")
    return spec, plan, state


def validate_run_bindings(
    run_spec: Mapping[str, Any],
    wave_plan: Mapping[str, Any],
    run_state: Mapping[str, Any] | None = None,
    *,
    host_capabilities: Mapping[str, Any] | None = None,
    planning_timestamp: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """Validate execution-ready bindings using current native host evidence."""
    if host_capabilities is None:
        fail("hostCapabilities", "is required for execution-valid run bindings", "provide the current native capability document")
    if planning_timestamp is None:
        fail("planningTimestamp", "is required for execution-valid run bindings", "provide the explicit planning timestamp")
    spec, plan, state = validate_run_structure_bindings(run_spec, wave_plan, run_state)
    host = validate_host_capabilities_at(host_capabilities, planning_timestamp)
    if plan["hostEvidenceDigest"] != canonical_digest(host):
        fail("wavePlan.hostEvidenceDigest", "does not bind canonical host capabilities", "recompute the digest from the validated host document")
    if spec["exactModel"] != host["selectedModel"]:
        fail("runSpec.exactModel", "does not match host selectedModel", "plan with the exact selected native model")
    for field in ("hostConcurrencyCeiling", "userConcurrencyCeiling"):
        if spec[field] != host[field]:
            fail(f"runSpec.{field}", f"does not match hostCapabilities.{field}", "rebuild the run spec from the same capability snapshot")
    unsupported = [story["recommendedEffort"] for story in plan["stories"] if story["recommendedEffort"] not in host["supportedEfforts"]]
    if unsupported:
        fail("wavePlan.stories", f"contains unsupported effort values: {sorted(set(unsupported))}", "use only efforts proven by host capabilities")
    if plan["mode"] == "parallel":
        missing = [name for name, supported in host["supports"].items() if not supported]
        if missing:
            fail("hostCapabilities.supports", f"parallel plan lacks required support: {', '.join(sorted(missing))}", "select sequential or capture a capable host")
    return spec, plan, state


def validate_run_state_transition(
    previous_state: Mapping[str, Any], current_state: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate immutable identity and append-only blocker history across snapshots."""
    previous = validate_run_state(previous_state)
    current = validate_run_state(current_state)
    if current["previousState"] != previous["state"]:
        fail("currentState.previousState", "does not name previousState.state", "persist the actual predecessor state")
    for field in ("runId", "baseSha", "integrationBranch", "initialIntegrationSha", "runBindingDigest"):
        if current[field] != previous[field]:
            fail(f"currentState.{field}", "changed across a run-state transition", "retain the immutable run binding")
    prior_history = previous["blockerHistory"]
    current_history = current["blockerHistory"]
    if current_history[:len(prior_history)] != prior_history:
        fail("currentState.blockerHistory", "rewrites prior blocker records", "retain the exact prior history as an ordered prefix")
    expected_growth = 1 if current["state"] == "blocked" else 0
    if len(current_history) != len(prior_history) + expected_growth:
        fail("currentState.blockerHistory", "is not append-only for this transition", "append exactly one record when blocking and otherwise retain history unchanged")
    validate_transition_evidence(previous, current)
    return previous, current


def validate_benchmark_pair(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    first = validate_benchmark_receipt(left)
    second = validate_benchmark_receipt(right)
    if {first["arm"], second["arm"]} != {"sequential", "parallel"}:
        fail("$.arm", "paired receipts must contain exactly sequential and parallel", "provide one receipt from each planned arm")
    for field in ("workloadId", "pairNumber", "trialNumber", "warmup"):
        if first[field] != second[field]:
            fail(f"$.{field}", "paired receipts use mismatched immutable identifiers", "compare receipts from the same planned pair")
    if canonical_data(first["controls"]) != canonical_data(second["controls"]):
        differing = sorted(field for field in CONTROL_FIELDS if first["controls"].get(field) != second["controls"].get(field))
        fail("$.controls", f"benchmark arms are incomparable; mismatched controls: {', '.join(differing)}", "rerun both arms with byte-identical non-mode controls")
    return first, second


def validate_benchmark_aggregate_receipts(
    aggregate: Mapping[str, Any], receipts: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    normalized_aggregate = validate_benchmark_aggregate(aggregate)
    normalized_receipts = tuple(validate_benchmark_receipt(receipt) for receipt in receipts)
    bind_aggregate_receipts(normalized_aggregate, list(normalized_receipts))
    return normalized_aggregate, normalized_receipts
