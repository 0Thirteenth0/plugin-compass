"""Benchmark receipt, workload, aggregate, and accounting validation."""

from __future__ import annotations

from typing import Any, Mapping

from ._limits import MAX_ATTEMPTS, MAX_COMMANDS, MAX_PAIRS, MAX_STORIES, MAX_WORKLOADS
from ._validation import (
    EFFORTS, array, boolean, canonical_data, canonical_digest, digest, enum, fail,
    identifier, integer, object_, sha, string, strings, timestamp,
)


CONTROL_FIELDS = {
    "fixtureDigest", "specDigest", "startSha", "orderedStories", "orderedStorySetDigest",
    "acceptanceChecks", "acceptanceCheckDigest", "exactModel", "effortPolicyVersion",
    "initialEfforts", "handoffDigests", "nonModePlanDigest", "controllerVersion",
    "promptVersion", "codexVersion", "pythonVersion", "gitVersion", "os", "cpuCount",
    "concurrencyCeiling", "timeoutMs", "toolchainDigest", "environmentDigest",
}
METRIC_FIELDS = {
    "retries", "interventions", "conflictsDetected", "conflictsAutoResolved",
    "conflictsManualResolved", "conflictsUnresolved", "scopeViolations", "staleHeadEvents",
    "timeouts", "checkFailures", "checkReruns", "repairDispatches", "manualEdits",
}
TERMINAL_STATUSES = {"green", "failed", "timed-out", "blocked", "incomparable"}


def validate_controls(value: Any, path: str) -> dict[str, Any]:
    controls = object_(value, path, CONTROL_FIELDS)
    digest(controls["fixtureDigest"], f"{path}.fixtureDigest")
    digest(controls["specDigest"], f"{path}.specDigest")
    sha(controls["startSha"], f"{path}.startSha")
    stories = strings(controls["orderedStories"], f"{path}.orderedStories", minimum=1, maximum=64, items_maximum=MAX_STORIES)
    for index, story_id in enumerate(stories):
        identifier(story_id, f"{path}.orderedStories[{index}]")
    digest(controls["orderedStorySetDigest"], f"{path}.orderedStorySetDigest")
    expected_story_digest = canonical_digest(stories)
    if controls["orderedStorySetDigest"] != expected_story_digest:
        fail(f"{path}.orderedStorySetDigest", "does not bind orderedStories", "recompute SHA-256 over canonical orderedStories")
    checks = strings(controls["acceptanceChecks"], f"{path}.acceptanceChecks", minimum=1, items_maximum=MAX_COMMANDS)
    digest(controls["acceptanceCheckDigest"], f"{path}.acceptanceCheckDigest")
    expected_check_digest = canonical_digest(checks)
    if controls["acceptanceCheckDigest"] != expected_check_digest:
        fail(f"{path}.acceptanceCheckDigest", "does not bind acceptanceChecks", "recompute SHA-256 over canonical acceptanceChecks")
    if string(controls["exactModel"], f"{path}.exactModel", maximum=160) == "inherit":
        fail(f"{path}.exactModel", "must be exact", "record the model ID")
    for name in ("effortPolicyVersion", "controllerVersion", "promptVersion", "codexVersion", "pythonVersion", "gitVersion", "os"):
        string(controls[name], f"{path}.{name}", maximum=256)
    effort_ids: list[str] = []
    for index, item in enumerate(array(controls["initialEfforts"], f"{path}.initialEfforts", minimum=1, maximum=MAX_STORIES)):
        item_path = f"{path}.initialEfforts[{index}]"
        object_(item, item_path, {"storyId", "effort"})
        effort_ids.append(identifier(item["storyId"], f"{item_path}.storyId"))
        enum(item["effort"], f"{item_path}.effort", EFFORTS)
    if effort_ids != stories:
        fail(f"{path}.initialEfforts", "is missing, extra, duplicate, or reordered", "provide one closed effort record per ordered story")
    handoff_ids: list[str] = []
    for index, item in enumerate(array(controls["handoffDigests"], f"{path}.handoffDigests", minimum=1, maximum=MAX_STORIES)):
        item_path = f"{path}.handoffDigests[{index}]"
        object_(item, item_path, {"storyId", "digest"})
        handoff_ids.append(identifier(item["storyId"], f"{item_path}.storyId"))
        digest(item["digest"], f"{item_path}.digest")
    if handoff_ids != stories:
        fail(f"{path}.handoffDigests", "is missing, extra, duplicate, or reordered", "provide one closed handoff record per ordered story")
    for name in ("nonModePlanDigest", "toolchainDigest", "environmentDigest"):
        digest(controls[name], f"{path}.{name}")
    integer(controls["cpuCount"], f"{path}.cpuCount", minimum=1)
    integer(controls["concurrencyCeiling"], f"{path}.concurrencyCeiling", minimum=1)
    integer(controls["timeoutMs"], f"{path}.timeoutMs", minimum=1)
    return controls


def _ledger(value: Any, path: str) -> dict[str, Any]:
    ledger = object_(value, path, {"terminalHash", "firstSequence", "lastSequence"})
    digest(ledger["terminalHash"], f"{path}.terminalHash")
    first = integer(ledger["firstSequence"], f"{path}.firstSequence", minimum=1)
    last = integer(ledger["lastSequence"], f"{path}.lastSequence", minimum=1)
    if last < first:
        fail(f"{path}.lastSequence", "precedes firstSequence", "record an ordered inclusive sequence range")
    return ledger


def validate_benchmark_receipt_shape(data: dict[str, Any]) -> None:
    object_(data, "$", {
        "schemaVersion", "workloadId", "attemptId", "arm", "pairNumber", "trialNumber",
        "warmup", "controls", "startedAt", "endedAt", "elapsedMs", "metrics", "eventLedger",
        "finalGreenSha", "terminalStatus",
    })
    identifier(data["workloadId"], "$.workloadId")
    identifier(data["attemptId"], "$.attemptId")
    enum(data["arm"], "$.arm", {"sequential", "parallel"})
    pair = integer(data["pairNumber"], "$.pairNumber")
    trial = integer(data["trialNumber"], "$.trialNumber")
    warmup = boolean(data["warmup"], "$.warmup")
    if warmup and (pair != 0 or trial != 0):
        fail("$.warmup", "warm-ups must use pairNumber/trialNumber zero", "set both indices to 0")
    if not warmup and (pair < 1 or trial != pair):
        fail("$.trialNumber", "measured attempts require matching positive pair/trial numbers", "use the planned 1-based pair number for both")
    validate_controls(data["controls"], "$.controls")
    started_at = timestamp(data["startedAt"], "$.startedAt")
    ended_at = timestamp(data["endedAt"], "$.endedAt")
    if ended_at <= started_at:
        fail("$.endedAt", "must be later than startedAt", "record terminal time after launch time")
    elapsed_ms = integer(data["elapsedMs"], "$.elapsedMs", minimum=1)
    if elapsed_ms != int((ended_at - started_at).total_seconds() * 1000):
        fail("$.elapsedMs", "does not match the recorded start/end interval", "record exact integer milliseconds for the same timing boundary")
    metrics = object_(data["metrics"], "$.metrics", METRIC_FIELDS)
    for name in METRIC_FIELDS:
        integer(metrics[name], f"$.metrics.{name}")
    resolved = metrics["conflictsAutoResolved"] + metrics["conflictsManualResolved"] + metrics["conflictsUnresolved"]
    if metrics["conflictsDetected"] != resolved:
        fail("$.metrics.conflictsDetected", "does not equal resolved plus unresolved conflict accounting", "account for every detected conflict exactly once")
    _ledger(data["eventLedger"], "$.eventLedger")
    status = enum(data["terminalStatus"], "$.terminalStatus", TERMINAL_STATUSES)
    final_sha = sha(data["finalGreenSha"], "$.finalGreenSha", nullable=True)
    if status == "green" and final_sha is None:
        fail("$.finalGreenSha", "is required for green status", "record the controller-verified final SHA")
    if status != "green" and final_sha is not None:
        fail("$.finalGreenSha", "must be null unless terminalStatus is green", "clear an unverified final SHA")
    if status == "timed-out" and metrics["timeouts"] < 1:
        fail("$.metrics.timeouts", "must record a timeout for timed-out status", "increment the timeout metric")
    if status == "green" and any(metrics[name] for name in ("conflictsUnresolved", "scopeViolations", "staleHeadEvents", "timeouts")):
        fail("$.terminalStatus", "green is inconsistent with blocking safety metrics", "use the appropriate non-green terminal status")


def validate_workloads_shape(data: dict[str, Any]) -> None:
    object_(data, "$", {"schemaVersion", "pairCount", "workloads"})
    pair_count = integer(data["pairCount"], "$.pairCount", minimum=5)
    if pair_count > MAX_PAIRS:
        fail("$.pairCount", f"must be <= {MAX_PAIRS}", "use the versioned MVP pair-count bound")
    workload_ids: list[str] = []
    global_attempts: list[str] = []
    for workload_index, workload in enumerate(array(data["workloads"], "$.workloads", minimum=1, maximum=MAX_WORKLOADS)):
        path = f"$.workloads[{workload_index}]"
        object_(workload, path, {"workloadId", "fixtureDigest", "baseSha", "specDigest", "warmups", "pairs"})
        workload_ids.append(identifier(workload["workloadId"], f"{path}.workloadId"))
        digest(workload["fixtureDigest"], f"{path}.fixtureDigest")
        sha(workload["baseSha"], f"{path}.baseSha")
        digest(workload["specDigest"], f"{path}.specDigest")
        warmups = array(workload["warmups"], f"{path}.warmups", minimum=2)
        if len(warmups) != 2:
            fail(f"{path}.warmups", "must contain exactly two warm-up records", "provide sequential then parallel")
        planned: list[str] = []
        for index, warmup in enumerate(warmups):
            warmup_path = f"{path}.warmups[{index}]"
            object_(warmup, warmup_path, {"arm", "attemptId"})
            expected_arm = "sequential" if index == 0 else "parallel"
            if enum(warmup["arm"], f"{warmup_path}.arm", {"sequential", "parallel"}) != expected_arm:
                fail(f"{warmup_path}.arm", "warm-ups must be ordered sequential then parallel", f"set arm to {expected_arm!r}")
            planned.append(identifier(warmup["attemptId"], f"{warmup_path}.attemptId"))
        pairs = array(workload["pairs"], f"{path}.pairs", minimum=pair_count, maximum=MAX_PAIRS)
        if len(pairs) != pair_count:
            fail(f"{path}.pairs", "does not equal pairCount", f"provide exactly {pair_count} pairs")
        for pair_index, pair in enumerate(pairs):
            pair_path = f"{path}.pairs[{pair_index}]"
            object_(pair, pair_path, {"pairNumber", "arms"})
            if integer(pair["pairNumber"], f"{pair_path}.pairNumber", minimum=1) != pair_index + 1:
                fail(f"{pair_path}.pairNumber", "does not match ordered position", f"set it to {pair_index + 1}")
            arms = array(pair["arms"], f"{pair_path}.arms", minimum=2)
            if len(arms) != 2:
                fail(f"{pair_path}.arms", "must contain exactly two arms", "provide sequential and parallel once each")
            expected = ["sequential", "parallel"] if pair_index % 2 == 0 else ["parallel", "sequential"]
            actual: list[str] = []
            for arm_index, arm in enumerate(arms):
                arm_path = f"{pair_path}.arms[{arm_index}]"
                object_(arm, arm_path, {"arm", "attemptId"})
                actual.append(enum(arm["arm"], f"{arm_path}.arm", {"sequential", "parallel"}))
                planned.append(identifier(arm["attemptId"], f"{arm_path}.attemptId"))
            if actual != expected:
                fail(f"{pair_path}.arms", "is missing, duplicate, or not in alternating planned arm order", f"use ordered arms {expected}")
        if len(set(planned)) != len(planned):
            fail(path, "contains duplicate planned attempt IDs", "assign every warm-up and measured attempt a unique ID")
        global_attempts.extend(planned)
    if len(set(workload_ids)) != len(workload_ids):
        fail("$.workloads", "contains duplicate workload IDs", "assign unique workload IDs")
    if len(set(global_attempts)) != len(global_attempts):
        fail("$.workloads", "reuses an attempt ID across workloads", "make every planned attempt ID globally unique")


def planned_attempts(manifest: Mapping[str, Any]) -> list[tuple[str, str, str, int]]:
    result: list[tuple[str, str, str, int]] = []
    for workload in manifest["workloads"]:
        for warmup in workload["warmups"]:
            result.append((workload["workloadId"], warmup["attemptId"], warmup["arm"], 0))
        for pair in workload["pairs"]:
            for arm in pair["arms"]:
                result.append((workload["workloadId"], arm["attemptId"], arm["arm"], pair["pairNumber"]))
    return result


def validate_aggregate_shape(data: dict[str, Any]) -> None:
    object_(data, "$", {
        "schemaVersion", "workloadManifest", "workloadManifestDigest", "workloadControls",
        "controlsDigest", "eventLedger", "attempts",
    })
    manifest = data["workloadManifest"]
    if not isinstance(manifest, dict):
        fail("$.workloadManifest", "must embed the workload manifest", "provide a benchmark-workloads object")
    if manifest.get("schemaVersion") != "compass-builder.benchmark-workloads.v1":
        fail("$.workloadManifest.schemaVersion", "has an unsupported embedded version", "embed benchmark-workloads.v1")
    validate_workloads_shape(manifest)
    digest(data["workloadManifestDigest"], "$.workloadManifestDigest")
    if data["workloadManifestDigest"] != canonical_digest(manifest):
        fail("$.workloadManifestDigest", "does not bind the embedded canonical manifest", "recompute the canonical SHA-256 digest")
    workload_ids = [workload["workloadId"] for workload in manifest["workloads"]]
    controls_by_id: dict[str, dict[str, Any]] = {}
    control_ids: list[str] = []
    for index, item in enumerate(array(data["workloadControls"], "$.workloadControls", minimum=1, maximum=MAX_WORKLOADS)):
        path = f"$.workloadControls[{index}]"
        object_(item, path, {"workloadId", "controls"})
        workload_id = identifier(item["workloadId"], f"{path}.workloadId")
        control_ids.append(workload_id)
        controls = validate_controls(item["controls"], f"{path}.controls")
        controls_by_id[workload_id] = controls
    if control_ids != workload_ids:
        fail("$.workloadControls", "is missing, extra, duplicate, or reordered", "provide one control set per workload in manifest order")
    for workload in manifest["workloads"]:
        controls = controls_by_id[workload["workloadId"]]
        if (controls["fixtureDigest"], controls["startSha"], controls["specDigest"]) != (workload["fixtureDigest"], workload["baseSha"], workload["specDigest"]):
            fail(f"$.workloadControls.{workload['workloadId']}", "does not match workload fixture/base/spec identifiers", "bind controls to the same fixture digest, base SHA, and spec digest")
    digest(data["controlsDigest"], "$.controlsDigest")
    if data["controlsDigest"] != canonical_digest(data["workloadControls"]):
        fail("$.controlsDigest", "does not bind workloadControls", "recompute SHA-256 over canonical workloadControls")
    _ledger(data["eventLedger"], "$.eventLedger")
    actual: list[tuple[str, str, str, int]] = []
    receipt_digests: list[str] = []
    for index, attempt in enumerate(array(data["attempts"], "$.attempts", minimum=1, maximum=MAX_ATTEMPTS)):
        path = f"$.attempts[{index}]"
        object_(attempt, path, {"workloadId", "attemptId", "arm", "pairNumber", "receiptDigest", "terminalStatus"})
        actual.append((
            identifier(attempt["workloadId"], f"{path}.workloadId"),
            identifier(attempt["attemptId"], f"{path}.attemptId"),
            enum(attempt["arm"], f"{path}.arm", {"sequential", "parallel"}),
            integer(attempt["pairNumber"], f"{path}.pairNumber"),
        ))
        receipt_digests.append(digest(attempt["receiptDigest"], f"{path}.receiptDigest"))
        enum(attempt["terminalStatus"], f"{path}.terminalStatus", TERMINAL_STATUSES)
    if actual != planned_attempts(manifest):
        fail("$.attempts", "is missing, extra, duplicate, or reordered relative to the planned manifest", "account for every planned attempt exactly once in manifest order")
    if len(set(receipt_digests)) != len(receipt_digests):
        fail("$.attempts", "reuses a receipt digest for different attempts", "bind each attempt to its own canonical receipt")


def bind_aggregate_receipts(aggregate: Mapping[str, Any], receipts: list[Mapping[str, Any]]) -> None:
    attempts = aggregate["attempts"]
    if len(receipts) != len(attempts):
        fail("receipts", "count does not match planned aggregate attempts", "provide one receipt per attempt in order")
    controls = {item["workloadId"]: item["controls"] for item in aggregate["workloadControls"]}
    seen_digests: set[str] = set()
    for index, (attempt, receipt) in enumerate(zip(attempts, receipts)):
        path = f"receipts[{index}]"
        receipt_digest = canonical_digest(receipt)
        if receipt_digest in seen_digests:
            fail(path, "duplicates a receipt used by another attempt", "provide the distinct receipt for this attempt")
        seen_digests.add(receipt_digest)
        if receipt_digest != attempt["receiptDigest"]:
            fail(path, "canonical digest does not match aggregate receiptDigest", "recompute and bind the exact receipt bytes")
        for field in ("workloadId", "attemptId", "arm", "pairNumber", "terminalStatus"):
            if receipt[field] != attempt[field]:
                fail(f"{path}.{field}", f"does not match aggregate attempt {field}", "bind the receipt to the same planned attempt")
        expected_warmup = attempt["pairNumber"] == 0
        if receipt["warmup"] != expected_warmup:
            fail(f"{path}.warmup", "does not match planned warm-up status", "use pair zero only for planned warm-ups")
        if canonical_data(receipt["controls"]) != canonical_data(controls[attempt["workloadId"]]):
            fail(f"{path}.controls", "does not match aggregate workload controls", "rerun with byte-identical immutable controls")
