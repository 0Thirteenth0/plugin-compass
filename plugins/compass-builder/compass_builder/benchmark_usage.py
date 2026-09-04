"""Synthetic, receipt-bound token telemetry for the paired benchmark."""

from __future__ import annotations

import copy
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping, Sequence

from .benchmark import _canonical_comparison
from ._benchmark_usage_models import (
    ATTEMPT_USAGE_VERSION, TOKEN_REPORT_VERSION,
)
from ._validation import (
    EFFORT_ORDER, canonical_digest, digest, fail, identifier, integer, run_id,
)
from .models import (
    validate_benchmark_aggregate, validate_benchmark_aggregate_receipts,
    validate_benchmark_attempt_usage,
    validate_benchmark_receipt, validate_benchmark_token_report,
    validate_worker_usage,
)


_LIFECYCLE_KINDS = {
    "worker-launch", "worker-completion", "worker-branch-import", "worker-usage",
}
_METRICS = (
    "retries", "interventions", "conflictsDetected", "conflictsAutoResolved",
    "conflictsManualResolved", "conflictsUnresolved", "scopeViolations",
    "staleHeadEvents", "timeouts", "checkFailures", "checkReruns",
    "repairDispatches", "manualEdits",
)
_BENCHMARK_STATUSES = ("green", "failed", "timed-out", "blocked", "incomparable")


def _six(numerator: int, denominator: int) -> str | None:
    if denominator == 0:
        return None
    return str(
        (Decimal(numerator) / Decimal(denominator)).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
    )


def _tokens(
    records: Sequence[Mapping[str, Any]], attempted_stories: int,
    successful_stories: int,
    *, failed_attempt_keys: set[tuple[str, str, int]] | None = None,
) -> dict[str, Any]:
    input_tokens = sum(int(record["usage"]["inputTokens"]) for record in records)
    cached_tokens = sum(int(record["usage"]["cachedInputTokens"]) for record in records)
    cache_write = sum(int(record["usage"]["cacheWriteInputTokens"]) for record in records)
    output_tokens = sum(int(record["usage"]["outputTokens"]) for record in records)
    reasoning_tokens = sum(int(record["usage"]["reasoningOutputTokens"]) for record in records)
    comparison = input_tokens + output_tokens
    retry = sum(
        int(record["usage"]["inputTokens"]) + int(record["usage"]["outputTokens"])
        for record in records if int(record["attempt"]) > 1
    )
    failed = sum(
        int(record["usage"]["inputTokens"]) + int(record["usage"]["outputTokens"])
        for record in records
        if (
            (record["runId"], record["storyId"], int(record["attempt"]))
            in failed_attempt_keys
            if failed_attempt_keys is not None
            else record["terminalStatus"] != "succeeded"
        )
    )
    return {
        "inputTokens": input_tokens,
        "cachedInputTokens": cached_tokens,
        "cacheWriteInputTokens": cache_write,
        "outputTokens": output_tokens,
        "reasoningOutputTokens": reasoning_tokens,
        "comparisonTokens": comparison,
        "cachedInputRatio": _six(cached_tokens, input_tokens),
        "reasoningOutputShare": _six(reasoning_tokens, output_tokens),
        "comparisonTokensPerAttemptedStory": _six(comparison, attempted_stories),
        "comparisonTokensPerSuccessfulStory": _six(comparison, successful_stories),
        "retryComparisonTokens": retry,
        "failedAttemptComparisonTokens": failed,
    }


def _launch(
    value: object, stories: set[str], expected_run_id: str,
) -> tuple[tuple[str, str, int], str] | None:
    if not isinstance(value, Mapping) or set(value) != {
        "runId", "storyId", "attempt", "launchDigest",
    }:
        return None
    try:
        normalized = {
            "runId": run_id(value["runId"], "$.runId"),
            "storyId": identifier(value["storyId"], "$.storyId"),
            "attempt": integer(value["attempt"], "$.attempt", minimum=1),
            "launchDigest": digest(value["launchDigest"], "$.launchDigest"),
        }
    except (TypeError, ValueError):
        return None
    if normalized["attempt"] > 2:
        return None
    if normalized["runId"] != expected_run_id or normalized["storyId"] not in stories:
        return None
    return (
        (normalized["runId"], normalized["storyId"], normalized["attempt"]),
        normalized["launchDigest"],
    )


def _lifecycle(
    kind: str, value: object, stories: set[str], expected_run_id: str,
) -> tuple[tuple[str, str, int], dict[str, Any]] | None:
    if not isinstance(value, Mapping):
        return None
    fields = {
        "worker-completion": {"runId", "storyId", "attempt", "launchDigest", "status", "headSha"},
        "worker-branch-import": {"runId", "storyId", "attempt", "launchDigest", "headSha"},
    }
    if kind not in fields or set(value) != fields[kind]:
        return None
    launch = _launch(
        {key: value[key] for key in ("runId", "storyId", "attempt", "launchDigest")},
        stories, expected_run_id,
    )
    if launch is None:
        return None
    item = copy.deepcopy(dict(value))
    if kind == "worker-completion":
        if item["status"] not in {"succeeded", "failed", "blocked", "timed-out", "transport-error"}:
            return None
        if item["status"] == "succeeded":
            if not isinstance(item["headSha"], str) or len(item["headSha"]) != 40:
                return None
        elif item["headSha"] is not None:
            return None
    elif not isinstance(item["headSha"], str) or len(item["headSha"]) != 40:
        return None
    if item["headSha"] is not None and any(character not in "0123456789abcdef" for character in item["headSha"]):
        return None
    return launch[0], item


def build_benchmark_attempt_usage(
    benchmark_receipt: Mapping[str, Any],
    controller_events: Sequence[tuple[str, Mapping[str, object]]],
    *,
    expected_run_id: str,
) -> dict[str, Any]:
    """Build one deterministic attempt record from public controller events only."""

    receipt = validate_benchmark_receipt(benchmark_receipt)
    expected_run_id = run_id(expected_run_id, "$.runId")
    stories = set(receipt["controls"]["orderedStories"])
    expected_efforts = {
        item["storyId"]: item["effort"]
        for item in receipt["controls"]["initialEfforts"]
    }
    reasons: set[str] = set()
    launches: dict[tuple[str, str, int], str] = {}
    usages: dict[tuple[str, str, int], dict[str, Any]] = {}
    completions: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    imports: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    phases: dict[tuple[str, str, int], str] = {}

    if not isinstance(controller_events, Sequence) or isinstance(controller_events, (str, bytes, bytearray)):
        raise TypeError("controller_events must be an ordered sequence of public event tuples")
    for event in controller_events:
        if not isinstance(event, tuple) or len(event) != 2:
            reasons.add("invalid-worker-lifecycle-evidence")
            continue
        kind, details = event
        if kind not in _LIFECYCLE_KINDS:
            continue
        if kind == "worker-launch":
            normalized = _launch(details, stories, expected_run_id)
            if normalized is None:
                reasons.add("invalid-worker-launch")
                continue
            key, launch_digest = normalized
            if key[2] == 2:
                prior_key = (key[0], key[1], 1)
                prior = completions.get(prior_key, [])
                if (
                    phases.get(prior_key) != "completion"
                    or len(prior) != 1
                    or prior[0]["status"] == "succeeded"
                ):
                    reasons.add("invalid-worker-lifecycle-evidence")
                    continue
            if key in launches:
                reasons.add("duplicate-worker-launch")
            else:
                launches[key] = launch_digest
                phases[key] = "launch"
            continue
        if kind == "worker-usage":
            try:
                record = validate_worker_usage(details)
            except (TypeError, ValueError):
                reasons.add("invalid-worker-usage")
                continue
            key = (record["runId"], record["storyId"], record["attempt"])
            initial_effort = expected_efforts.get(record["storyId"])
            effort_matches = (
                record["effort"] == initial_effort
                if record["attempt"] == 1
                else (
                    initial_effort in EFFORT_ORDER
                    and record["effort"] in EFFORT_ORDER
                    and EFFORT_ORDER.index(record["effort"])
                    > EFFORT_ORDER.index(initial_effort)
                )
            )
            if (
                record["runId"] != expected_run_id
                or record["storyId"] not in stories
                or record["exactModel"] != receipt["controls"]["exactModel"]
                or not effort_matches
            ):
                reasons.add("invalid-worker-usage")
                continue
            if key in usages:
                reasons.add("duplicate-worker-usage")
            elif (
                phases.get(key) != "launch"
                or launches.get(key) != record["launchDigest"]
            ):
                reasons.add("invalid-worker-lifecycle-evidence")
                if key not in launches:
                    reasons.add("orphan-worker-usage")
            else:
                usages[key] = record
                phases[key] = "usage"
            continue
        normalized = _lifecycle(kind, details, stories, expected_run_id)
        if normalized is None:
            reasons.add("invalid-worker-lifecycle-evidence")
            continue
        key, item = normalized
        target = completions if kind == "worker-completion" else imports
        expected_phase = "usage" if kind == "worker-completion" else "completion"
        if (
            phases.get(key) != expected_phase
            or launches.get(key) != item["launchDigest"]
        ):
            reasons.add("invalid-worker-lifecycle-evidence")
            continue
        target.setdefault(key, []).append(item)
        phases[key] = "completion" if kind == "worker-completion" else "import"

    if not launches:
        reasons.add("no-worker-launch-events")
    for key in usages:
        if key not in launches:
            reasons.add("orphan-worker-usage")
    for key in set(completions) | set(imports):
        if key not in launches:
            reasons.add("invalid-worker-lifecycle-evidence")

    workers: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    successful_stories: set[str] = set()
    for key in sorted(launches):
        worker_run_id, story_id, attempt = key
        launch_digest = launches[key]
        record = usages.get(key)
        if record is None:
            reasons.add("missing-worker-usage")
        elif record["launchDigest"] != launch_digest:
            reasons.add("orphan-worker-usage")
            record = None
        elif not record["observed"]:
            reasons.add("unavailable-worker-usage")

        completion_items = completions.get(key, [])
        import_items = imports.get(key, [])
        if len(completion_items) > 1 or len(import_items) > 1:
            reasons.add("invalid-worker-lifecycle-evidence")
        completion = completion_items[0] if len(completion_items) == 1 else None
        imported = import_items[0] if len(import_items) == 1 else None
        verified_integrated = False
        if completion is not None:
            if completion["launchDigest"] != launch_digest:
                reasons.add("invalid-worker-lifecycle-evidence")
            elif record is not None and completion["status"] != record["terminalStatus"]:
                reasons.add("invalid-worker-lifecycle-evidence")
            elif completion["status"] == "succeeded" and imported is not None:
                if (
                    imported["launchDigest"] == launch_digest
                    and imported["headSha"] == completion["headSha"]
                ):
                    verified_integrated = True
                    successful_stories.add(story_id)
                else:
                    reasons.add("invalid-worker-lifecycle-evidence")
            elif completion["status"] != "succeeded" and imported is not None:
                reasons.add("invalid-worker-lifecycle-evidence")
        elif imported is not None:
            reasons.add("invalid-worker-lifecycle-evidence")
        if receipt["terminalStatus"] == "green" and completion is None:
            reasons.add("invalid-worker-lifecycle-evidence")
        if receipt["terminalStatus"] == "green" and completion is not None and completion["status"] == "succeeded" and imported is None:
            reasons.add("invalid-worker-lifecycle-evidence")

        usage_digest = canonical_digest(record) if record is not None else None
        workers.append({
            "runId": worker_run_id, "storyId": story_id, "attempt": attempt,
            "launchDigest": launch_digest, "workerUsageDigest": usage_digest,
            "terminalStatus": record["terminalStatus"] if record is not None else None,
            "verifiedIntegrated": verified_integrated,
        })
        if record is not None:
            records.append(record)

    if receipt["terminalStatus"] == "green" and successful_stories != stories:
        reasons.add("invalid-worker-lifecycle-evidence")

    completeness = "complete" if not reasons else "incomplete"
    attempted_stories = len({item["storyId"] for item in workers})
    failed_attempt_keys = {
        (item["runId"], item["storyId"], item["attempt"])
        for item in workers if not item["verifiedIntegrated"]
    }
    summary_tokens = (
        _tokens(
            records, attempted_stories, len(successful_stories),
            failed_attempt_keys=failed_attempt_keys,
        )
        if completeness == "complete" else None
    )
    return validate_benchmark_attempt_usage({
        "schemaVersion": ATTEMPT_USAGE_VERSION,
        "runId": expected_run_id,
        "workloadId": receipt["workloadId"], "attemptId": receipt["attemptId"],
        "arm": receipt["arm"], "pairNumber": receipt["pairNumber"],
        "trialNumber": receipt["trialNumber"], "warmup": receipt["warmup"],
        "benchmarkReceiptDigest": canonical_digest(receipt),
        "completeness": completeness, "incompleteReasons": sorted(reasons),
        "workers": workers, "workerUsageRecords": records,
        "summary": {
            "workerAttemptCount": len(workers),
            "attemptedStoryCount": attempted_stories,
            "successfulStoryCount": len(successful_stories),
            "tokens": summary_tokens,
        },
    })


def _median(values: Sequence[int]) -> str:
    ordered = sorted(values)
    middle = len(ordered) // 2
    value = (
        Decimal(ordered[middle]) if len(ordered) % 2
        else (Decimal(ordered[middle - 1]) + Decimal(ordered[middle])) / Decimal(2)
    )
    return str(value)


def _first_pass(receipt: Mapping[str, Any]) -> bool:
    return all(
        receipt["metrics"][name] == 0
        for name in ("retries", "repairDispatches", "manualEdits", "checkReruns")
    )


def _sum_attempt_tokens(attempts: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    if any(item["completeness"] != "complete" for item in attempts):
        return None
    attempted = sum(int(item["summary"]["attemptedStoryCount"]) for item in attempts)
    successful = sum(int(item["summary"]["successfulStoryCount"]) for item in attempts)
    summaries = [item["summary"]["tokens"] for item in attempts]
    input_tokens = sum(item["inputTokens"] for item in summaries)
    cached_tokens = sum(item["cachedInputTokens"] for item in summaries)
    cache_write = sum(item["cacheWriteInputTokens"] for item in summaries)
    output_tokens = sum(item["outputTokens"] for item in summaries)
    reasoning_tokens = sum(item["reasoningOutputTokens"] for item in summaries)
    comparison = input_tokens + output_tokens
    return {
        "inputTokens": input_tokens,
        "cachedInputTokens": cached_tokens,
        "cacheWriteInputTokens": cache_write,
        "outputTokens": output_tokens,
        "reasoningOutputTokens": reasoning_tokens,
        "comparisonTokens": comparison,
        "cachedInputRatio": _six(cached_tokens, input_tokens),
        "reasoningOutputShare": _six(reasoning_tokens, output_tokens),
        "comparisonTokensPerAttemptedStory": _six(comparison, attempted),
        "comparisonTokensPerSuccessfulStory": _six(comparison, successful),
        "retryComparisonTokens": sum(item["retryComparisonTokens"] for item in summaries),
        "failedAttemptComparisonTokens": sum(
            item["failedAttemptComparisonTokens"] for item in summaries
        ),
    }


def _quality(receipts: Sequence[Mapping[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    statuses = {name: 0 for name in _BENCHMARK_STATUSES}
    metrics = {name: 0 for name in _METRICS}
    for receipt in receipts:
        statuses[receipt["terminalStatus"]] += 1
        for name in _METRICS:
            metrics[name] += int(receipt["metrics"][name])
    return statuses, metrics


def _bind_comparison(
    comparison: Mapping[str, Any], measured: Sequence[Mapping[str, Any]],
) -> None:
    if not isinstance(comparison, Mapping):
        raise ValueError("benchmark comparison must be an object")
    expected = _canonical_comparison(
        [item for item in measured if item["arm"] == "sequential"],
        [item for item in measured if item["arm"] == "parallel"],
    )
    if dict(comparison) != expected:
        raise ValueError("benchmark comparison decision evidence is detached")


def _build_benchmark_token_report(
    benchmark_aggregate: Mapping[str, Any],
    benchmark_receipts: Sequence[Mapping[str, Any]],
    attempt_usage_records: Sequence[Mapping[str, Any]],
    *,
    benchmark_comparison: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a measured report while retaining warm-up attempt provenance."""

    aggregate = validate_benchmark_aggregate(benchmark_aggregate)
    receipt_values = [validate_benchmark_receipt(item) for item in benchmark_receipts]
    receipt_by_attempt = {item["attemptId"]: item for item in receipt_values}
    if len(receipt_by_attempt) != len(receipt_values):
        raise ValueError("benchmark receipts contain duplicate attempt IDs")
    if set(receipt_by_attempt) != {item["attemptId"] for item in aggregate["attempts"]}:
        raise ValueError("benchmark receipts are missing or detached")
    ordered_receipts = [
        receipt_by_attempt[item["attemptId"]] for item in aggregate["attempts"]
    ]
    aggregate, receipts = validate_benchmark_aggregate_receipts(
        aggregate, ordered_receipts
    )
    normalized_attempts = [
        validate_benchmark_attempt_usage(item) for item in attempt_usage_records
    ]
    by_id = {item["attemptId"]: item for item in normalized_attempts}
    if len(by_id) != len(normalized_attempts):
        raise ValueError("benchmark attempt usage contains duplicate attempt IDs")
    if set(by_id) != {item["attemptId"] for item in aggregate["attempts"]}:
        raise ValueError("benchmark attempt usage is missing or detached")

    ordered_attempts: list[dict[str, Any]] = []
    for aggregate_attempt, receipt in zip(aggregate["attempts"], receipts):
        usage = by_id[aggregate_attempt["attemptId"]]
        for field in ("workloadId", "attemptId", "arm", "pairNumber"):
            if usage[field] != aggregate_attempt[field]:
                raise ValueError(f"benchmark attempt usage {field} is detached")
        if usage["warmup"] != receipt["warmup"] or usage["benchmarkReceiptDigest"] != canonical_digest(receipt):
            raise ValueError("benchmark attempt usage receipt binding is detached")
        initial_efforts = {
            item["storyId"]: item["effort"]
            for item in receipt["controls"]["initialEfforts"]
        }
        if any(
            record["attempt"] == 1
            and record["effort"] != initial_efforts.get(record["storyId"])
            for record in usage["workerUsageRecords"]
        ):
            raise ValueError("benchmark attempt usage effort is detached from receipt controls")
        ordered_attempts.append(usage)

    measured_receipts = [receipt for receipt in receipts if not receipt["warmup"]]
    measured_attempts = [item for item in ordered_attempts if not item["warmup"]]
    warmup_attempts = [item for item in ordered_attempts if item["warmup"]]
    measured_by_id = {item["attemptId"]: item for item in measured_attempts}
    receipt_by_id = {item["attemptId"]: item for item in measured_receipts}
    token_incomplete = any(item["completeness"] != "complete" for item in measured_attempts)
    reasons: set[str] = {"incomplete-attempt-usage"} if token_incomplete else set()
    comparison_value = copy.deepcopy(dict(benchmark_comparison)) if benchmark_comparison is not None else None
    if comparison_value is not None:
        _bind_comparison(comparison_value, measured_receipts)
    else:
        reasons.add("v1-comparison-unavailable")

    arm_summaries = []
    for arm in ("sequential", "parallel"):
        arm_receipts = [item for item in measured_receipts if item["arm"] == arm]
        arm_attempts = [measured_by_id[item["attemptId"]] for item in arm_receipts]
        statuses, metrics = _quality(arm_receipts)
        arm_summaries.append({
            "arm": arm,
            "measuredAttemptCount": len(arm_receipts),
            "warmupAttemptCount": sum(item["arm"] == arm for item in warmup_attempts),
            "medianElapsedMs": _median([item["elapsedMs"] for item in arm_receipts]),
            "firstPassCount": sum(_first_pass(item) for item in arm_receipts),
            "terminalStatusCounts": statuses, "metrics": metrics,
            "attemptedStoryCount": sum(item["summary"]["attemptedStoryCount"] for item in arm_attempts),
            "successfulStoryCount": sum(item["summary"]["successfulStoryCount"] for item in arm_attempts),
            "tokens": _sum_attempt_tokens(arm_attempts),
        })

    keys = sorted({(item["workloadId"], item["pairNumber"]) for item in measured_receipts})
    pair_summaries = []
    for workload_id, pair_number in keys:
        pair_receipts = [
            item for item in measured_receipts
            if item["workloadId"] == workload_id and item["pairNumber"] == pair_number
        ]
        if len(pair_receipts) != 2 or {item["arm"] for item in pair_receipts} != {"sequential", "parallel"}:
            raise ValueError("measured benchmark attempts are not exactly paired")
        seq_receipt = next(item for item in pair_receipts if item["arm"] == "sequential")
        par_receipt = next(item for item in pair_receipts if item["arm"] == "parallel")
        seq_usage = measured_by_id[seq_receipt["attemptId"]]
        par_usage = measured_by_id[par_receipt["attemptId"]]
        complete = seq_usage["completeness"] == par_usage["completeness"] == "complete"
        seq_tokens = copy.deepcopy(seq_usage["summary"]["tokens"]) if complete else None
        par_tokens = copy.deepcopy(par_usage["summary"]["tokens"]) if complete else None
        sequential_total = seq_tokens["comparisonTokens"] if seq_tokens else 0
        parallel_total = par_tokens["comparisonTokens"] if par_tokens else 0
        pair_summaries.append({
            "workloadId": workload_id, "pairNumber": pair_number,
            "sequentialAttemptUsageDigest": canonical_digest(seq_usage),
            "parallelAttemptUsageDigest": canonical_digest(par_usage),
            "sequentialElapsedMs": seq_receipt["elapsedMs"],
            "parallelElapsedMs": par_receipt["elapsedMs"],
            "elapsedDeltaMs": par_receipt["elapsedMs"] - seq_receipt["elapsedMs"],
            "tokenVerdict": "complete" if complete else "incomplete",
            "sequentialTokens": seq_tokens, "parallelTokens": par_tokens,
            "tokenDelta": parallel_total - sequential_total if complete else None,
            "tokenRatio": _six(parallel_total, sequential_total) if complete else None,
        })

    report = {
        "schemaVersion": TOKEN_REPORT_VERSION,
        "benchmarkAggregateDigest": canonical_digest(aggregate),
        "benchmarkComparisonDigest": canonical_digest(comparison_value) if comparison_value is not None else None,
        "benchmarkComparison": comparison_value,
        "warmupsIncludedInMeasuredComparison": False,
        "tokenVerdict": "incomplete" if token_incomplete else "complete",
        "incompleteReasons": sorted(reasons),
        "attemptUsage": [{
            "workloadId": item["workloadId"], "attemptId": item["attemptId"],
            "arm": item["arm"], "pairNumber": item["pairNumber"],
            "warmup": item["warmup"], "attemptUsageDigest": canonical_digest(item),
            "completeness": item["completeness"],
        } for item in ordered_attempts],
        "armSummaries": arm_summaries, "pairSummaries": pair_summaries,
    }
    return validate_benchmark_token_report(report)


def validate_benchmark_token_report_bindings(
    report: Mapping[str, Any],
    benchmark_aggregate: Mapping[str, Any],
    benchmark_receipts: Sequence[Mapping[str, Any]],
    attempt_usage_records: Sequence[Mapping[str, Any]],
    *,
    benchmark_comparison: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Rebind a closed report and every derived field to its source documents."""

    normalized = validate_benchmark_token_report(report)
    expected = _build_benchmark_token_report(
        benchmark_aggregate,
        benchmark_receipts,
        attempt_usage_records,
        benchmark_comparison=benchmark_comparison,
    )
    if normalized != expected:
        fail(
            "$.report",
            "does not equal the report derived from the bound aggregate, receipts, attempt usage, and comparison",
            "recompute the complete report from the exact ordered source documents",
        )
    return normalized


def build_benchmark_token_report(
    benchmark_aggregate: Mapping[str, Any],
    benchmark_receipts: Sequence[Mapping[str, Any]],
    attempt_usage_records: Sequence[Mapping[str, Any]],
    *,
    benchmark_comparison: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and cross-document validate a measured benchmark token report."""

    report = _build_benchmark_token_report(
        benchmark_aggregate,
        benchmark_receipts,
        attempt_usage_records,
        benchmark_comparison=benchmark_comparison,
    )
    return validate_benchmark_token_report_bindings(
        report,
        benchmark_aggregate,
        benchmark_receipts,
        attempt_usage_records,
        benchmark_comparison=benchmark_comparison,
    )


__all__ = [
    "build_benchmark_attempt_usage", "build_benchmark_token_report",
    "validate_benchmark_token_report_bindings",
]
