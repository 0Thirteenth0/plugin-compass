"""Fail-closed paired benchmark comparison and ledger verification."""

from __future__ import annotations

import hashlib
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Mapping, Sequence

from .models import (
    canonical_json, validate_benchmark_pair, validate_benchmark_receipt,
)


THRESHOLD_PERCENT = Decimal("20")
GENESIS_HASH = "sha256:" + "0" * 64
EVENT_KINDS = {
    "attempt-start", "worker-launch", "worker-completion", "retry",
    "repair-request", "input-request", "check-rerun", "timeout",
    "ref-status-observation", "external-git-mutation", "attempt-completion",
}
BLOCKING_EVENTS = {"repair-request", "input-request", "external-git-mutation"}


class ComparisonError(ValueError):
    """Benchmark evidence is incomplete, incomparable, or unsafe to calculate."""


def _event_hash(event: Mapping[str, object]) -> str:
    unsigned = {key: value for key, value in event.items() if key != "eventHash"}
    return "sha256:" + hashlib.sha256(canonical_json(unsigned)).hexdigest()


def validate_event_ledger(
    events: Sequence[Mapping[str, object]],
    receipts: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    """Validate a contiguous append-only chain and every receipt terminal binding."""

    normalized: list[dict[str, object]] = []
    previous = GENESIS_HASH
    for index, raw in enumerate(events):
        if not isinstance(raw, Mapping):
            raise ComparisonError(f"ledger event {index + 1} is not an object")
        event = dict(raw)
        required = {"sequence", "previousHash", "eventHash", "kind", "details"}
        if set(event) != required:
            raise ComparisonError(f"ledger event {index + 1} has an open or incomplete field set")
        if type(event["sequence"]) is not int or event["sequence"] != index + 1:
            raise ComparisonError("event ledger contains a gap, duplicate, or reordered sequence")
        if event["previousHash"] != previous:
            raise ComparisonError("event ledger previousHash chain is broken")
        if event["kind"] not in EVENT_KINDS or not isinstance(event["details"], dict):
            raise ComparisonError("event ledger contains an unsupported event")
        expected = _event_hash(event)
        if event["eventHash"] != expected:
            raise ComparisonError("event ledger hash does not bind its canonical event")
        previous = expected
        normalized.append(event)
    if not normalized:
        raise ComparisonError("event ledger is missing")
    if any(event["kind"] in BLOCKING_EVENTS for event in normalized):
        raise ComparisonError("manual input, repair, or external Git mutation blocks comparison")
    for receipt in receipts:
        descriptor = receipt["eventLedger"]
        first, last = descriptor["firstSequence"], descriptor["lastSequence"]
        if first < 1 or last > len(normalized):
            raise ComparisonError("receipt ledger range is outside the supplied ledger")
        interval = normalized[first - 1:last]
        if interval[-1]["eventHash"] != descriptor["terminalHash"]:
            raise ComparisonError("receipt terminal hash does not match terminal ledger coverage")
        attempt_id = receipt["attemptId"]
        if any(event["details"].get("attemptId") != attempt_id for event in interval):
            raise ComparisonError("receipt ledger range includes an unaccounted attempt event")
        kinds = [event["kind"] for event in interval]
        if not kinds or kinds[0] != "attempt-start" or kinds[-1] != "attempt-completion":
            raise ComparisonError("receipt ledger range lacks exact attempt terminal coverage")
        if any(kind in BLOCKING_EVENTS for kind in kinds):
            raise ComparisonError("manual input, repair, or external Git mutation blocks comparison")
    return tuple(normalized)


def _median(values: Sequence[int]) -> Decimal:
    ordered = sorted(values)
    count = len(ordered)
    if not count:
        raise ComparisonError("paired benchmark has no measured duration")
    middle = count // 2
    if count % 2:
        return Decimal(ordered[middle])
    return (Decimal(ordered[middle - 1]) + Decimal(ordered[middle])) / Decimal(2)


def _first_pass(receipt: Mapping[str, object]) -> bool:
    metrics = receipt["metrics"]
    return all(
        metrics[name] == 0
        for name in ("retries", "repairDispatches", "manualEdits", "checkReruns")
    )


def _normalize_arm(
    values: Iterable[Mapping[str, object]], arm: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    receipts = [validate_benchmark_receipt(item) for item in values]
    if any(item["arm"] != arm for item in receipts):
        raise ComparisonError(f"{arm} input contains a different scheduling arm")
    if len({item["attemptId"] for item in receipts}) != len(receipts):
        raise ComparisonError(f"{arm} input contains duplicate attempt IDs")
    measured = [item for item in receipts if not item["warmup"]]
    if len(measured) < 5:
        raise ComparisonError("comparison requires at least five measured receipts per arm")
    if any(item["terminalStatus"] != "green" for item in measured):
        raise ComparisonError("failed, timed-out, blocked, or incomparable arms block comparison")
    return receipts, measured


def compare(
    sequential: Iterable[Mapping[str, object]],
    parallel: Iterable[Mapping[str, object]],
    *,
    sequential_events: Sequence[Mapping[str, object]],
    parallel_events: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Compare equal measured pairs without dropping any completed trial."""

    seq_all, seq = _normalize_arm(sequential, "sequential")
    par_all, par = _normalize_arm(parallel, "parallel")
    validate_event_ledger(sequential_events, seq_all)
    validate_event_ledger(parallel_events, par_all)
    seq_by_key = {(item["workloadId"], item["pairNumber"]): item for item in seq}
    par_by_key = {(item["workloadId"], item["pairNumber"]): item for item in par}
    if len(seq_by_key) != len(seq) or len(par_by_key) != len(par):
        raise ComparisonError("an arm contains duplicate workload/pair receipts")
    if set(seq_by_key) != set(par_by_key):
        raise ComparisonError("sequential and parallel trial sets are unequal")
    workload_ids = sorted({key[0] for key in seq_by_key})
    reports: list[dict[str, object]] = []
    graduated = True
    for workload_id in workload_ids:
        keys = sorted(key for key in seq_by_key if key[0] == workload_id)
        expected_pairs = list(range(1, len(keys) + 1))
        if [key[1] for key in keys] != expected_pairs or len(keys) < 5:
            raise ComparisonError("workload pair numbers are incomplete or non-contiguous")
        seq_items, par_items = [], []
        for key in keys:
            sequential_receipt, parallel_receipt = validate_benchmark_pair(
                seq_by_key[key], par_by_key[key]
            )
            seq_items.append(sequential_receipt)
            par_items.append(parallel_receipt)
        reference = canonical_json(seq_items[0]["controls"])
        if any(canonical_json(item["controls"]) != reference for item in seq_items + par_items):
            raise ComparisonError("immutable controls drifted across measured pairs")
        seq_median = _median([item["elapsedMs"] for item in seq_items])
        par_median = _median([item["elapsedMs"] for item in par_items])
        improvement = (seq_median - par_median) / seq_median * Decimal(100)
        seq_first = sum(_first_pass(item) for item in seq_items)
        par_first = sum(_first_pass(item) for item in par_items)
        seq_interventions = sum(item["metrics"]["interventions"] for item in seq_items)
        par_interventions = sum(item["metrics"]["interventions"] for item in par_items)
        blocking = {
            name: sum(item["metrics"][name] for item in seq_items + par_items)
            for name in (
                "timeouts", "staleHeadEvents", "conflictsManualResolved",
                "conflictsUnresolved", "scopeViolations", "manualEdits",
                "repairDispatches",
            )
        }
        reasons: list[str] = []
        if improvement < THRESHOLD_PERCENT:
            reasons.append("parallel median improvement is below 20%")
        if par_first < seq_first:
            reasons.append("parallel first-pass acceptance is lower")
        if par_interventions > seq_interventions:
            reasons.append("parallel human interventions increased")
        if any(blocking.values()):
            reasons.append("a blocking safety metric is non-zero")
        eligible = not reasons
        graduated = graduated and eligible
        reports.append({
            "workloadId": workload_id, "pairCount": len(keys),
            "medianSequentialMs": str(seq_median), "medianParallelMs": str(par_median),
            "improvementPercent": str(
                improvement.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            ),
            "firstPassSequential": seq_first,
            "firstPassParallel": par_first,
            "interventionsSequential": seq_interventions,
            "interventionsParallel": par_interventions,
            "blockingSafetyMetrics": blocking, "eligible": eligible, "reasons": reasons,
        })
    return {
        "schemaVersion": "compass-builder.benchmark-comparison.v1",
        "thresholdPercent": "20.00", "graduated": graduated,
        "workloads": reports,
    }


__all__ = [
    "ComparisonError", "GENESIS_HASH", "THRESHOLD_PERCENT", "compare",
    "validate_event_ledger",
]
