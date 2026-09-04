"""Closed E1 benchmark token-telemetry contract validation."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping

from ._usage_models import (
    MAX_TOKEN_COUNT,
    SEMANTIC_EXTENSION,
    SEMANTIC_SCHEMA_VERSION,
    TERMINAL_STATUSES as WORKER_STATUSES,
    validate_worker_usage_shape,
)
from ._validation import (
    EFFORT_ORDER, array, boolean, canonical_digest, digest, enum, fail,
    identifier, integer, object_, run_id,
)


ATTEMPT_USAGE_VERSION = "compass-builder.benchmark-attempt-usage.v1"
TOKEN_REPORT_VERSION = "compass-builder.benchmark-token-report.v1"
MAX_WORKERS_PER_ATTEMPT = 256
MAX_BENCHMARK_ATTEMPTS = 16_384
INCOMPLETE_REASONS = {
    "no-worker-launch-events", "invalid-worker-launch", "duplicate-worker-launch",
    "missing-worker-usage", "unavailable-worker-usage", "duplicate-worker-usage",
    "orphan-worker-usage", "invalid-worker-usage",
    "invalid-worker-lifecycle-evidence",
}
REPORT_INCOMPLETE_REASONS = {
    "incomplete-attempt-usage", "missing-attempt-usage",
    "invalid-attempt-usage", "v1-comparison-unavailable",
}
METRIC_FIELDS = {
    "retries", "interventions", "conflictsDetected", "conflictsAutoResolved",
    "conflictsManualResolved", "conflictsUnresolved", "scopeViolations",
    "staleHeadEvents", "timeouts", "checkFailures", "checkReruns",
    "repairDispatches", "manualEdits",
}
BENCHMARK_STATUSES = {"green", "failed", "timed-out", "blocked", "incomparable"}
_RATIO_RE = re.compile(r"^(?:0|[1-9][0-9]*)\.[0-9]{6}$")
_MEDIAN_RE = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.5)?$")
_TOKEN_FIELDS = {
    "inputTokens", "cachedInputTokens", "cacheWriteInputTokens", "outputTokens",
    "reasoningOutputTokens", "comparisonTokens", "cachedInputRatio",
    "reasoningOutputShare", "comparisonTokensPerAttemptedStory",
    "comparisonTokensPerSuccessfulStory", "retryComparisonTokens",
    "failedAttemptComparisonTokens",
}
_ATTEMPT_SEMANTIC_RULE = (
    "equal", "$.trialNumber", "$.pairNumber",
)


def _count(value: Any, path: str) -> int:
    result = integer(value, path)
    if result > MAX_TOKEN_COUNT:
        fail(path, "exceeds the interoperable integer bound", "record a JSON safe-integer count")
    return result


def _ratio(value: Any, path: str, *, nullable: bool = True) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not _RATIO_RE.fullmatch(value):
        fail(path, "must be a non-negative decimal with six fractional digits", "use a canonical six-place decimal string or null")
    try:
        if Decimal(value) < 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        fail(path, "must be a finite non-negative decimal", "use a canonical finite ratio")
    return value


def _six(numerator: int, denominator: int) -> str | None:
    if denominator == 0:
        return None
    return str(
        (Decimal(numerator) / Decimal(denominator)).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
    )


def _tokens(value: Any, path: str) -> dict[str, Any]:
    tokens = object_(value, path, _TOKEN_FIELDS)
    for field in (
        "inputTokens", "cachedInputTokens", "cacheWriteInputTokens", "outputTokens",
        "reasoningOutputTokens", "comparisonTokens", "retryComparisonTokens",
        "failedAttemptComparisonTokens",
    ):
        _count(tokens[field], f"{path}.{field}")
    if tokens["comparisonTokens"] != tokens["inputTokens"] + tokens["outputTokens"]:
        fail(f"{path}.comparisonTokens", "must equal inputTokens + outputTokens", "do not double-count cached input or reasoning output")
    if tokens["cachedInputTokens"] > tokens["inputTokens"]:
        fail(f"{path}.cachedInputTokens", "cannot exceed inputTokens", "treat cached input as an input component")
    if tokens["reasoningOutputTokens"] > tokens["outputTokens"]:
        fail(f"{path}.reasoningOutputTokens", "cannot exceed outputTokens", "treat reasoning output as an output component")
    for field in (
        "cachedInputRatio", "reasoningOutputShare",
        "comparisonTokensPerAttemptedStory", "comparisonTokensPerSuccessfulStory",
    ):
        _ratio(tokens[field], f"{path}.{field}")
    return tokens


def _reasons(value: Any, path: str, allowed: set[str]) -> list[str]:
    reasons = [enum(item, f"{path}[{index}]", allowed) for index, item in enumerate(array(value, path, maximum=32))]
    if reasons != sorted(set(reasons)):
        fail(path, "must be sorted and duplicate-free", "sort unique machine-readable reasons")
    return reasons


def validate_benchmark_attempt_usage_shape(data: dict[str, Any]) -> None:
    object_(data, "$", {
        "schemaVersion", "runId", "workloadId", "attemptId", "arm", "pairNumber",
        "trialNumber", "warmup", "benchmarkReceiptDigest", "completeness",
        "incompleteReasons", "workers", "workerUsageRecords", "summary",
    })
    expected_run_id = run_id(data["runId"], "$.runId")
    identifier(data["workloadId"], "$.workloadId")
    identifier(data["attemptId"], "$.attemptId")
    enum(data["arm"], "$.arm", {"sequential", "parallel"})
    pair = integer(data["pairNumber"], "$.pairNumber")
    trial = integer(data["trialNumber"], "$.trialNumber")
    warmup = boolean(data["warmup"], "$.warmup")
    if (pair, trial) != ((0, 0) if warmup else (pair, pair)) or (not warmup and pair < 1):
        fail("$.pairNumber", "is inconsistent with warmup/trialNumber", "use zero for warmups and the positive pair number otherwise")
    digest(data["benchmarkReceiptDigest"], "$.benchmarkReceiptDigest")
    completeness = enum(data["completeness"], "$.completeness", {"complete", "incomplete"})
    reasons = _reasons(data["incompleteReasons"], "$.incompleteReasons", INCOMPLETE_REASONS)
    if (completeness == "complete") != (not reasons):
        fail("$.completeness", "does not agree with incompleteReasons", "use complete only with an empty reason list")

    workers = array(data["workers"], "$.workers", maximum=MAX_WORKERS_PER_ATTEMPT)
    worker_keys: list[tuple[str, str, int]] = []
    worker_digests: dict[tuple[str, str, int], str | None] = {}
    worker_launch_digests: dict[tuple[str, str, int], str] = {}
    worker_statuses: dict[tuple[str, str, int], str | None] = {}
    worker_integrated: dict[tuple[str, str, int], bool] = {}
    integrated_worker_keys: set[tuple[str, str, int]] = set()
    successful_stories: set[str] = set()
    for index, raw in enumerate(workers):
        path = f"$.workers[{index}]"
        worker = object_(raw, path, {
            "runId", "storyId", "attempt", "launchDigest", "workerUsageDigest",
            "terminalStatus", "verifiedIntegrated",
        })
        key = (
            run_id(worker["runId"], f"{path}.runId"),
            identifier(worker["storyId"], f"{path}.storyId"),
            integer(worker["attempt"], f"{path}.attempt", minimum=1),
        )
        if key[0] != expected_run_id:
            fail(f"{path}.runId", "must match the attempt runId authority", "bind every worker to the benchmark bundle run")
        if key[2] > 2:
            fail(f"{path}.attempt", "must be worker attempt 1 or 2", "preserve the bounded launch attempt")
        launch_digest = digest(worker["launchDigest"], f"{path}.launchDigest")
        usage_digest = digest(worker["workerUsageDigest"], f"{path}.workerUsageDigest", nullable=True)
        status = worker["terminalStatus"]
        if status is not None:
            enum(status, f"{path}.terminalStatus", WORKER_STATUSES)
        if (usage_digest is None) != (status is None):
            fail(path, "usage digest and terminal status must be present together", "bind both to the finalized usage record or set both null")
        verified = boolean(worker["verifiedIntegrated"], f"{path}.verifiedIntegrated")
        if verified and status != "succeeded":
            fail(f"{path}.verifiedIntegrated", "requires a succeeded worker status", "bind a matching successful completion and branch import")
        if verified:
            successful_stories.add(key[1])
            integrated_worker_keys.add(key)
        worker_keys.append(key)
        worker_digests[key] = usage_digest
        worker_launch_digests[key] = launch_digest
        worker_statuses[key] = status
        worker_integrated[key] = verified
    if worker_keys != sorted(worker_keys) or len(set(worker_keys)) != len(worker_keys):
        fail("$.workers", "must be uniquely sorted by runId/storyId/attempt", "preserve every launch identity in deterministic order")
    worker_key_set = set(worker_keys)
    if any(
        attempt == 2 and (worker_run_id, story_id, 1) not in worker_key_set
        for worker_run_id, story_id, attempt in worker_keys
    ):
        fail("$.workers", "must contain contiguous attempts [1] or [1, 2] per story", "preserve the complete ordered retry chain")

    records = array(data["workerUsageRecords"], "$.workerUsageRecords", maximum=MAX_WORKERS_PER_ATTEMPT)
    record_keys: list[tuple[str, str, int]] = []
    records_by_key: dict[tuple[str, str, int], dict[str, Any]] = {}
    for index, record in enumerate(records):
        path = f"$.workerUsageRecords[{index}]"
        if not isinstance(record, dict):
            fail(path, "must be a worker-usage object", "provide the finalized public usage record")
        validate_worker_usage_shape(record)
        key = (record["runId"], record["storyId"], record["attempt"])
        record_keys.append(key)
        records_by_key[key] = record
        if key not in worker_digests or worker_digests[key] != canonical_digest(record):
            fail(path, "is not bound to exactly one worker launch", "bind its canonical digest to the matching worker entry")
        if record["launchDigest"] != worker_launch_digests[key]:
            fail(f"{path}.launchDigest", "does not match the worker launch", "preserve the exact launch digest in both bound records")
        if record["terminalStatus"] != worker_statuses[key]:
            fail(f"{path}.terminalStatus", "does not match the worker status", "preserve the exact terminal status in both bound records")
    if record_keys != sorted(record_keys) or len(set(record_keys)) != len(record_keys):
        fail("$.workerUsageRecords", "must be uniquely sorted by runId/storyId/attempt", "sort every distinct attempt record")
    expected_record_keys = {
        key for key, usage_digest in worker_digests.items()
        if usage_digest is not None
    }
    if set(record_keys) != expected_record_keys:
        fail(
            "$.workerUsageRecords",
            "must exactly match every worker with a non-null workerUsageDigest",
            "embed each digest-bound usage record exactly once; omit usage only with null digest and status",
        )
    for worker_run_id, story_id, attempt in worker_keys:
        if attempt != 2:
            continue
        prior_key = (worker_run_id, story_id, 1)
        prior_record = records_by_key.get(prior_key)
        retry_record = records_by_key.get((worker_run_id, story_id, 2))
        if (
            worker_statuses.get(prior_key) in {None, "succeeded"}
            or worker_integrated.get(prior_key, False)
            or prior_record is None
        ):
            fail("$.workers", "attempt 2 requires one completed unsuccessful attempt 1", "preserve the plausible public retry chain")
        if retry_record is not None and (
            EFFORT_ORDER.index(retry_record["effort"])
            <= EFFORT_ORDER.index(prior_record["effort"])
        ):
            fail("$.workerUsageRecords", "attempt 2 effort must strictly exceed attempt 1", "preserve the canonical retry effort escalation")

    summary = object_(data["summary"], "$.summary", {
        "workerAttemptCount", "attemptedStoryCount", "successfulStoryCount", "tokens",
    })
    if integer(summary["workerAttemptCount"], "$.summary.workerAttemptCount") != len(workers):
        fail("$.summary.workerAttemptCount", "does not match workers", "count every preserved worker launch")
    attempted = len({key[1] for key in worker_keys})
    attempted_count = integer(summary["attemptedStoryCount"], "$.summary.attemptedStoryCount")
    if attempted_count > 128:
        fail("$.summary.attemptedStoryCount", "exceeds the schema story bound", "record at most 128 distinct benchmark stories")
    if attempted_count != attempted:
        fail("$.summary.attemptedStoryCount", "does not match distinct launched stories", "count distinct story identities")
    successful_count = integer(summary["successfulStoryCount"], "$.summary.successfulStoryCount")
    if successful_count > 128:
        fail("$.summary.successfulStoryCount", "exceeds the schema story bound", "record at most 128 successful benchmark stories")
    if successful_count != len(successful_stories):
        fail("$.summary.successfulStoryCount", "does not match verified/integrated lifecycle evidence", "count distinct exactly imported successful stories")
    if completeness == "complete":
        tokens = _tokens(summary["tokens"], "$.summary.tokens")
        if len(records) != len(workers) or any(not record["observed"] for record in records):
            fail("$.completeness", "complete telemetry lacks one observed record per launch", "mark the record incomplete")
        input_tokens = sum(record["usage"]["inputTokens"] for record in records)
        cached_tokens = sum(record["usage"]["cachedInputTokens"] for record in records)
        cache_write = sum(record["usage"]["cacheWriteInputTokens"] for record in records)
        output_tokens = sum(record["usage"]["outputTokens"] for record in records)
        reasoning_tokens = sum(record["usage"]["reasoningOutputTokens"] for record in records)
        comparison = input_tokens + output_tokens
        expected = {
            "inputTokens": input_tokens,
            "cachedInputTokens": cached_tokens,
            "cacheWriteInputTokens": cache_write,
            "outputTokens": output_tokens,
            "reasoningOutputTokens": reasoning_tokens,
            "comparisonTokens": comparison,
            "cachedInputRatio": _six(cached_tokens, input_tokens),
            "reasoningOutputShare": _six(reasoning_tokens, output_tokens),
            "comparisonTokensPerAttemptedStory": _six(comparison, attempted),
            "comparisonTokensPerSuccessfulStory": _six(comparison, len(successful_stories)),
            "retryComparisonTokens": sum(
                record["usage"]["inputTokens"] + record["usage"]["outputTokens"]
                for record in records if record["attempt"] > 1
            ),
            "failedAttemptComparisonTokens": sum(
                record["usage"]["inputTokens"] + record["usage"]["outputTokens"]
                for record in records
                if (record["runId"], record["storyId"], record["attempt"])
                not in integrated_worker_keys
            ),
        }
        if tokens != expected:
            fail("$.summary.tokens", "does not equal the bound worker usage records", "recompute all totals, component ratios, and overhead from the exact records")
    elif summary["tokens"] is not None:
        fail("$.summary.tokens", "must be null for incomplete telemetry", "do not expose partial totals as complete evidence")


def _metrics(value: Any, path: str) -> None:
    metrics = object_(value, path, METRIC_FIELDS)
    for field in METRIC_FIELDS:
        _count(metrics[field], f"{path}.{field}")


def _comparison(value: Any, path: str) -> None:
    comparison = object_(value, path, {"schemaVersion", "thresholdPercent", "graduated", "workloads"})
    if comparison["schemaVersion"] != "compass-builder.benchmark-comparison.v1" or comparison["thresholdPercent"] != "20.00":
        fail(path, "is not the immutable v1 comparison output", "bind the existing benchmark-comparison.v1 result unchanged")
    boolean(comparison["graduated"], f"{path}.graduated")
    for index, raw in enumerate(array(comparison["workloads"], f"{path}.workloads", minimum=1, maximum=64)):
        item_path = f"{path}.workloads[{index}]"
        item = object_(raw, item_path, {
            "workloadId", "pairCount", "medianSequentialMs", "medianParallelMs",
            "improvementPercent", "firstPassSequential", "firstPassParallel",
            "interventionsSequential", "interventionsParallel",
            "blockingSafetyMetrics", "eligible", "reasons",
        })
        identifier(item["workloadId"], f"{item_path}.workloadId")
        integer(item["pairCount"], f"{item_path}.pairCount", minimum=5)
        for field in ("medianSequentialMs", "medianParallelMs", "improvementPercent"):
            if not isinstance(item[field], str):
                fail(f"{item_path}.{field}", "must retain the v1 decimal string", "copy the v1 comparison output")
        for field in ("firstPassSequential", "firstPassParallel", "interventionsSequential", "interventionsParallel"):
            integer(item[field], f"{item_path}.{field}")
        # v1 intentionally exposes only the blocking subset, not all controller metrics.
        blocking = object_(item["blockingSafetyMetrics"], f"{item_path}.blockingSafetyMetrics", {
            "timeouts", "staleHeadEvents", "conflictsManualResolved", "conflictsUnresolved",
            "scopeViolations", "manualEdits", "repairDispatches",
        })
        for field in blocking:
            _count(blocking[field], f"{item_path}.blockingSafetyMetrics.{field}")
        boolean(item["eligible"], f"{item_path}.eligible")
        for reason_index, reason in enumerate(
            array(item["reasons"], f"{item_path}.reasons", maximum=32)
        ):
            reason_path = f"{item_path}.reasons[{reason_index}]"
            if not isinstance(reason, str) or not 1 <= len(reason) <= 4096:
                fail(reason_path, "must be a 1-4096 character string", "copy the exact v1 comparison reason")


def validate_benchmark_token_report_shape(data: dict[str, Any]) -> None:
    object_(data, "$", {
        "schemaVersion", "benchmarkAggregateDigest", "benchmarkComparisonDigest",
        "benchmarkComparison", "warmupsIncludedInMeasuredComparison", "tokenVerdict",
        "incompleteReasons", "attemptUsage", "armSummaries", "pairSummaries",
    })
    digest(data["benchmarkAggregateDigest"], "$.benchmarkAggregateDigest")
    comparison_digest = digest(data["benchmarkComparisonDigest"], "$.benchmarkComparisonDigest", nullable=True)
    comparison = data["benchmarkComparison"]
    if comparison is None:
        if comparison_digest is not None:
            fail("$.benchmarkComparisonDigest", "must be null when comparison is unavailable", "clear the detached digest")
    else:
        if comparison_digest is None:
            fail("$.benchmarkComparisonDigest", "is required with embedded comparison", "bind the canonical v1 comparison digest")
        _comparison(comparison, "$.benchmarkComparison")
        if comparison_digest != canonical_digest(comparison):
            fail("$.benchmarkComparisonDigest", "does not bind the embedded v1 comparison", "recompute the canonical comparison digest")
    if boolean(data["warmupsIncludedInMeasuredComparison"], "$.warmupsIncludedInMeasuredComparison"):
        fail("$.warmupsIncludedInMeasuredComparison", "must remain false", "mirror v1 by excluding warmups")
    verdict = enum(data["tokenVerdict"], "$.tokenVerdict", {"complete", "incomplete"})
    reasons = _reasons(data["incompleteReasons"], "$.incompleteReasons", REPORT_INCOMPLETE_REASONS)
    if (verdict == "complete") != (not [item for item in reasons if item != "v1-comparison-unavailable"]):
        fail("$.tokenVerdict", "does not agree with token incompleteness", "mark incomplete when any measured attempt usage is missing or invalid")
    refs = array(data["attemptUsage"], "$.attemptUsage", minimum=1, maximum=MAX_BENCHMARK_ATTEMPTS)
    seen_attempts: set[str] = set()
    for index, raw in enumerate(refs):
        path = f"$.attemptUsage[{index}]"
        item = object_(raw, path, {"workloadId", "attemptId", "arm", "pairNumber", "warmup", "attemptUsageDigest", "completeness"})
        identifier(item["workloadId"], f"{path}.workloadId")
        attempt_id = identifier(item["attemptId"], f"{path}.attemptId")
        if attempt_id in seen_attempts:
            fail("$.attemptUsage", "contains duplicate attempt IDs", "bind every benchmark attempt exactly once")
        seen_attempts.add(attempt_id)
        enum(item["arm"], f"{path}.arm", {"sequential", "parallel"})
        integer(item["pairNumber"], f"{path}.pairNumber")
        boolean(item["warmup"], f"{path}.warmup")
        digest(item["attemptUsageDigest"], f"{path}.attemptUsageDigest")
        enum(item["completeness"], f"{path}.completeness", {"complete", "incomplete"})
    for index, raw in enumerate(array(data["armSummaries"], "$.armSummaries", minimum=2, maximum=2)):
        path = f"$.armSummaries[{index}]"
        item = object_(raw, path, {
            "arm", "measuredAttemptCount", "warmupAttemptCount", "medianElapsedMs",
            "firstPassCount", "terminalStatusCounts", "metrics", "attemptedStoryCount",
            "successfulStoryCount", "tokens",
        })
        expected = "sequential" if index == 0 else "parallel"
        if enum(item["arm"], f"{path}.arm", {"sequential", "parallel"}) != expected:
            fail(f"{path}.arm", "arm summaries must be sequential then parallel", "restore deterministic arm order")
        integer(item["measuredAttemptCount"], f"{path}.measuredAttemptCount", minimum=1)
        integer(item["warmupAttemptCount"], f"{path}.warmupAttemptCount")
        if not isinstance(item["medianElapsedMs"], str) or not _MEDIAN_RE.fullmatch(item["medianElapsedMs"]):
            fail(f"{path}.medianElapsedMs", "must be a canonical integer or half-integer string", "record the exact measured receipt median")
        integer(item["firstPassCount"], f"{path}.firstPassCount")
        statuses = object_(item["terminalStatusCounts"], f"{path}.terminalStatusCounts", BENCHMARK_STATUSES)
        for field in statuses:
            _count(statuses[field], f"{path}.terminalStatusCounts.{field}")
        _metrics(item["metrics"], f"{path}.metrics")
        integer(item["attemptedStoryCount"], f"{path}.attemptedStoryCount")
        integer(item["successfulStoryCount"], f"{path}.successfulStoryCount")
        if item["tokens"] is not None:
            _tokens(item["tokens"], f"{path}.tokens")
    pairs = array(data["pairSummaries"], "$.pairSummaries", minimum=1, maximum=MAX_BENCHMARK_ATTEMPTS)
    pair_keys: list[tuple[str, int]] = []
    for index, raw in enumerate(pairs):
        path = f"$.pairSummaries[{index}]"
        item = object_(raw, path, {
            "workloadId", "pairNumber", "sequentialAttemptUsageDigest",
            "parallelAttemptUsageDigest", "sequentialElapsedMs", "parallelElapsedMs",
            "elapsedDeltaMs", "tokenVerdict", "sequentialTokens", "parallelTokens",
            "tokenDelta", "tokenRatio",
        })
        pair_keys.append((identifier(item["workloadId"], f"{path}.workloadId"), integer(item["pairNumber"], f"{path}.pairNumber", minimum=1)))
        digest(item["sequentialAttemptUsageDigest"], f"{path}.sequentialAttemptUsageDigest")
        digest(item["parallelAttemptUsageDigest"], f"{path}.parallelAttemptUsageDigest")
        sequential_ms = integer(item["sequentialElapsedMs"], f"{path}.sequentialElapsedMs", minimum=1)
        parallel_ms = integer(item["parallelElapsedMs"], f"{path}.parallelElapsedMs", minimum=1)
        delta = item["elapsedDeltaMs"]
        if isinstance(delta, bool) or not isinstance(delta, int) or delta != parallel_ms - sequential_ms:
            fail(f"{path}.elapsedDeltaMs", "must equal parallel minus sequential milliseconds", "derive the paired elapsed delta")
        pair_verdict = enum(item["tokenVerdict"], f"{path}.tokenVerdict", {"complete", "incomplete"})
        for field in ("sequentialTokens", "parallelTokens"):
            if item[field] is not None:
                _tokens(item[field], f"{path}.{field}")
        if pair_verdict == "complete":
            if item["sequentialTokens"] is None or item["parallelTokens"] is None:
                fail(path, "complete pair requires both token summaries", "supply both complete measured attempts")
            expected_delta = item["parallelTokens"]["comparisonTokens"] - item["sequentialTokens"]["comparisonTokens"]
            if type(item["tokenDelta"]) is not int or item["tokenDelta"] != expected_delta:
                fail(f"{path}.tokenDelta", "must equal parallel minus sequential comparison tokens", "derive the paired token delta")
            _ratio(item["tokenRatio"], f"{path}.tokenRatio")
        elif any(item[field] is not None for field in ("sequentialTokens", "parallelTokens", "tokenDelta", "tokenRatio")):
            fail(path, "incomplete pair must not expose partial token comparison", "set token values to null")
    if pair_keys != sorted(pair_keys) or len(set(pair_keys)) != len(pair_keys):
        fail("$.pairSummaries", "must be unique and sorted by workload/pair", "restore stable matched-pair order")


def validate_benchmark_attempt_usage_schema_semantics(
    schema: Mapping[str, Any], record: Mapping[str, Any]
) -> None:
    """Evaluate the schema-declared trial/pair sibling equality without dependencies."""

    if not isinstance(schema, Mapping):
        fail("$schema", "must be an object mapping", "load the benchmark-attempt-usage JSON Schema object")
    extension_path = f"$schema.{SEMANTIC_EXTENSION}"
    extension = object_(
        schema.get(SEMANTIC_EXTENSION), extension_path, {"schemaVersion", "rules"}
    )
    if extension["schemaVersion"] != SEMANTIC_SCHEMA_VERSION:
        fail(
            f"{extension_path}.schemaVersion",
            f"must be {SEMANTIC_SCHEMA_VERSION!r}",
            "use the supported immutable semantic rule version",
        )
    rules = array(
        extension["rules"], f"{extension_path}.rules", minimum=1, maximum=1
    )
    rule = object_(
        rules[0], f"{extension_path}.rules[0]", {"operator", "left", "right"}
    )
    declared = (rule["operator"], rule["left"], rule["right"])
    if declared != _ATTEMPT_SEMANTIC_RULE:
        fail(
            f"{extension_path}.rules",
            "must declare exact trialNumber/pairNumber equality",
            "restore the single versioned equal sibling rule",
        )
    if not isinstance(record, Mapping):
        fail("$", "must be an object mapping", "provide a benchmark-attempt-usage record")
    trial = integer(record.get("trialNumber"), "$.trialNumber")
    pair = integer(record.get("pairNumber"), "$.pairNumber")
    if trial != pair:
        fail(
            "$.trialNumber",
            "must equal $.pairNumber",
            "bind measured trials to their positive pair number and warmups to zero",
        )


__all__ = [
    "ATTEMPT_USAGE_VERSION", "INCOMPLETE_REASONS", "REPORT_INCOMPLETE_REASONS",
    "TOKEN_REPORT_VERSION", "validate_benchmark_attempt_usage_shape",
    "validate_benchmark_attempt_usage_schema_semantics",
    "validate_benchmark_token_report_shape",
]
