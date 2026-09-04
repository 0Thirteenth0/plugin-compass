from __future__ import annotations

import copy
import hashlib
import importlib
import inspect
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "plugins" / "compass-builder"
FIXTURES = ROOT / "tests" / "fixtures" / "compass_builder"
SCHEMAS = BUILDER / "schemas"
if str(BUILDER) not in sys.path:
    sys.path.insert(0, str(BUILDER))


def _public():
    return importlib.import_module("compass_builder")


def _api(name: str):
    module = _public()
    if not hasattr(module, name):
        raise AssertionError(f"missing public E1c API {name}")
    return getattr(module, name)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.valid.json").read_text(encoding="utf-8"))


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8") + b"\n"
    ).hexdigest()


def _receipt(
    *, arm: str = "sequential", pair: int = 1, warmup: bool = False,
    stories: tuple[str, ...] = ("alpha",),
) -> dict:
    value = _fixture("benchmark-receipt")
    value["controls"]["orderedStories"] = list(stories)
    value["controls"]["orderedStorySetDigest"] = _digest(list(stories))
    value["controls"]["initialEfforts"] = [
        item for item in value["controls"]["initialEfforts"]
        if item["storyId"] in stories
    ]
    value["controls"]["handoffDigests"] = [
        item for item in value["controls"]["handoffDigests"]
        if item["storyId"] in stories
    ]
    value["arm"] = arm
    value["pairNumber"] = 0 if warmup else pair
    value["trialNumber"] = 0 if warmup else pair
    value["warmup"] = warmup
    value["attemptId"] = (
        f"sample-warm-{'seq' if arm == 'sequential' else 'par'}"
        if warmup else
        f"sample-p{pair}-{'seq' if arm == 'sequential' else 'par'}"
    )
    if arm == "sequential":
        value["startedAt"] = "2026-09-01T12:00:00.000Z"
        value["endedAt"] = "2026-09-01T12:00:01.000Z"
        value["elapsedMs"] = 1000
    else:
        value["startedAt"] = "2026-09-01T12:00:00.000Z"
        value["endedAt"] = "2026-09-01T12:00:00.700Z"
        value["elapsedMs"] = 700
    return value


def _usage(
    story: str,
    *,
    attempt: int = 1,
    input_tokens: int = 100,
    cached_tokens: int = 40,
    output_tokens: int = 20,
    reasoning_tokens: int = 5,
    observed: bool = True,
    terminal_status: str = "succeeded",
) -> dict:
    value = _fixture("worker-usage")
    value.update(
        storyId=story,
        attempt=attempt,
        launchDigest="sha256:" + ("a" if attempt == 1 else "b") * 64,
        terminalStatus=terminal_status,
        observed=observed,
        unavailableReason=None if observed else "no-terminal-usage",
    )
    value["usage"] = (
        {
            "inputTokens": input_tokens,
            "cachedInputTokens": cached_tokens,
            "cacheWriteInputTokens": 0,
            "cacheWriteInputTokensPresent": False,
            "outputTokens": output_tokens,
            "reasoningOutputTokens": reasoning_tokens,
        }
        if observed else None
    )
    return value


def _events(records: list[dict], *, integrate: set[tuple[str, int]] | None = None):
    integrated = integrate if integrate is not None else {
        (record["storyId"], record["attempt"])
        for record in records if record["terminalStatus"] == "succeeded"
    }
    events: list[tuple[str, dict]] = []
    for record in reversed(records):
        identity = {
            "runId": record["runId"], "storyId": record["storyId"],
            "attempt": record["attempt"], "launchDigest": record["launchDigest"],
        }
        events.append(("worker-launch", identity))
        events.append(("worker-usage", copy.deepcopy(record)))
        head = ("c" if record["attempt"] == 1 else "d") * 40
        events.append(("worker-completion", {
            **identity, "status": record["terminalStatus"],
            "headSha": head if record["terminalStatus"] == "succeeded" else None,
        }))
        if (record["storyId"], record["attempt"]) in integrated:
            events.append(("worker-branch-import", {**identity, "headSha": head}))
    return events


def _attempt(
    receipt: dict, records: list[dict], *, events=None,
    integrate: set[tuple[str, int]] | None = None,
) -> dict:
    return _api("build_benchmark_attempt_usage")(
        receipt,
        _events(records, integrate=integrate) if events is None else events,
        expected_run_id=_fixture("worker-usage")["runId"],
    )


class BenchmarkAttemptUsageTests(unittest.TestCase):
    def test_single_worker_attempt_is_bound_complete_and_not_double_counted(self):
        receipt = _receipt()
        result = _attempt(receipt, [_usage("alpha")])
        self.assertEqual("compass-builder.benchmark-attempt-usage.v1", result["schemaVersion"])
        self.assertEqual(_digest(receipt), result["benchmarkReceiptDigest"])
        self.assertEqual("complete", result["completeness"])
        self.assertEqual([], result["incompleteReasons"])
        self.assertEqual(1, result["summary"]["workerAttemptCount"])
        self.assertEqual(1, result["summary"]["attemptedStoryCount"])
        self.assertEqual(1, result["summary"]["successfulStoryCount"])
        self.assertEqual({"runId": _usage("alpha")["runId"], "storyId": "alpha", "attempt": 1}, {
            key: result["workers"][0][key] for key in ("runId", "storyId", "attempt")
        })
        tokens = result["summary"]["tokens"]
        self.assertEqual(100, tokens["inputTokens"])
        self.assertEqual(20, tokens["outputTokens"])
        self.assertEqual(120, tokens["comparisonTokens"])
        self.assertEqual("0.400000", tokens["cachedInputRatio"])
        self.assertEqual("0.250000", tokens["reasoningOutputShare"])
        self.assertEqual("120.000000", tokens["comparisonTokensPerAttemptedStory"])
        self.assertEqual("120.000000", tokens["comparisonTokensPerSuccessfulStory"])
        self.assertEqual(0, tokens["retryComparisonTokens"])
        self.assertEqual(0, tokens["failedAttemptComparisonTokens"])

    def test_parallel_records_are_stably_ordered_and_deterministic(self):
        receipt = _receipt(arm="parallel", stories=("alpha", "beta"))
        alpha = _usage("alpha")
        beta = _usage(
            "beta", input_tokens=200, cached_tokens=0,
            output_tokens=100, reasoning_tokens=25,
        )
        beta["effort"] = "medium"
        first = _attempt(receipt, [beta, alpha])
        second = _attempt(receipt, [alpha, beta], events=_events([beta, alpha]))
        self.assertEqual(
            [("alpha", 1), ("beta", 1)],
            [(item["storyId"], item["attempt"]) for item in first["workers"]],
        )
        self.assertEqual(420, first["summary"]["tokens"]["comparisonTokens"])
        self.assertEqual(_digest(first), _digest(second))

    def test_two_worker_lifecycle_may_interleave_without_reordering_each_worker(self):
        receipt = _receipt(arm="parallel", stories=("alpha", "beta"))
        alpha, beta = _usage("alpha"), _usage("beta")
        beta["effort"] = "medium"
        alpha_events, beta_events = _events([alpha]), _events([beta])
        events = [
            alpha_events[0], beta_events[0],
            alpha_events[1], beta_events[1],
            alpha_events[2], beta_events[2],
            alpha_events[3], beta_events[3],
        ]
        result = _attempt(receipt, [alpha, beta], events=events)
        self.assertEqual("complete", result["completeness"])

    def test_each_worker_lifecycle_rejects_reordered_events(self):
        record = _usage("alpha")
        valid = _events([record])
        cases = {
            "usage-before-launch": [valid[1], valid[0], valid[2], valid[3]],
            "completion-before-usage": [valid[0], valid[2], valid[1], valid[3]],
            "import-before-completion": [valid[0], valid[1], valid[3], valid[2]],
            "fully-reversed": list(reversed(valid)),
        }
        for case, events in cases.items():
            with self.subTest(case=case):
                result = _attempt(_receipt(), [record], events=events)
                self.assertEqual("incomplete", result["completeness"])
                self.assertIn(
                    "invalid-worker-lifecycle-evidence",
                    result["incompleteReasons"],
                )

    def test_attempt_binds_explicit_run_authority_and_rejects_foreign_run(self):
        build = _api("build_benchmark_attempt_usage")
        self.assertIn("expected_run_id", inspect.signature(build).parameters)
        expected_run_id = _usage("alpha")["runId"]
        foreign = _usage("alpha")
        foreign["runId"] = "cb-foreign-1234567890abcdef"
        result = build(
            _receipt(), _events([foreign]), expected_run_id=expected_run_id,
        )
        self.assertEqual(expected_run_id, result["runId"])
        self.assertEqual("incomplete", result["completeness"])
        self.assertFalse(result["workers"])

    def test_usage_effort_must_match_receipt_initial_effort(self):
        record = _usage("alpha")
        record["effort"] = "ultra"
        result = _attempt(_receipt(), [record])
        self.assertEqual("incomplete", result["completeness"])
        self.assertIn("invalid-worker-usage", result["incompleteReasons"])

    def test_green_receipt_requires_every_ordered_story_lifecycle(self):
        receipt = _receipt(stories=("alpha", "beta"))
        result = _attempt(receipt, [_usage("alpha")])
        self.assertEqual("incomplete", result["completeness"])
        self.assertIn(
            "invalid-worker-lifecycle-evidence", result["incompleteReasons"]
        )

    def test_green_receipt_rejects_failed_or_unintegrated_worker(self):
        failed = _usage("alpha", terminal_status="failed")
        failed_result = _attempt(_receipt(), [failed], integrate=set())
        self.assertEqual("incomplete", failed_result["completeness"])
        unintegrated_result = _attempt(
            _receipt(), [_usage("alpha")], integrate=set()
        )
        self.assertEqual("incomplete", unintegrated_result["completeness"])

    def test_retry_and_failed_attempt_overhead_preserve_every_launch(self):
        first = _usage(
            "alpha", attempt=1, input_tokens=50, cached_tokens=0,
            output_tokens=10, reasoning_tokens=0, terminal_status="failed",
        )
        retry = _usage("alpha", attempt=2)
        retry["effort"] = "medium"
        result = _attempt(_receipt(), [retry, first], integrate={("alpha", 2)})
        self.assertEqual("complete", result["completeness"])
        self.assertEqual(
            [("alpha", 1), ("alpha", 2)],
            [(item["storyId"], item["attempt"]) for item in result["workers"]],
        )
        tokens = result["summary"]["tokens"]
        self.assertEqual(180, tokens["comparisonTokens"])
        self.assertEqual(120, tokens["retryComparisonTokens"])
        self.assertEqual(60, tokens["failedAttemptComparisonTokens"])
        self.assertEqual(1, result["summary"]["successfulStoryCount"])

    def test_retry_requires_ordered_unsuccessful_prior_attempt(self):
        first_failed = _usage("alpha", terminal_status="failed")
        retry = _usage("alpha", attempt=2)
        retry["effort"] = "medium"
        valid = _events([retry, first_failed])
        cases = {
            "missing-attempt-one": _events([retry]),
            "retry-before-prior-completion": [
                valid[0], valid[1], valid[3], valid[4], valid[2],
                valid[5], valid[6],
            ],
        }
        first_succeeded = _usage("alpha")
        cases["retry-after-success"] = _events([retry, first_succeeded])
        for case, events in cases.items():
            with self.subTest(case=case):
                result = _attempt(_receipt(), [], events=events)
                self.assertEqual("incomplete", result["completeness"])
                self.assertIn(
                    "invalid-worker-lifecycle-evidence",
                    result["incompleteReasons"],
                )

    def test_retry_effort_must_strictly_increase(self):
        cases = []
        first = _usage("alpha", terminal_status="failed")
        same = _usage("alpha", attempt=2)
        cases.append((_receipt(), first, same))

        medium_receipt = _receipt()
        medium_receipt["controls"]["initialEfforts"][0]["effort"] = "medium"
        medium_first = _usage("alpha", terminal_status="failed")
        medium_first["effort"] = "medium"
        lower = _usage("alpha", attempt=2)
        cases.append((medium_receipt, medium_first, lower))

        for receipt, attempt_one, attempt_two in cases:
            with self.subTest(
                initial=attempt_one["effort"], retry=attempt_two["effort"]
            ):
                result = _attempt(receipt, [attempt_two, attempt_one])
                self.assertEqual("incomplete", result["completeness"])

    def test_attempt_shape_requires_contiguous_story_attempts(self):
        value = _fixture("benchmark-attempt-usage")
        value["workers"][0]["attempt"] = 2
        value["workerUsageRecords"][0]["attempt"] = 2
        value["workers"][0]["workerUsageDigest"] = _digest(
            value["workerUsageRecords"][0]
        )
        value["summary"]["tokens"]["retryComparisonTokens"] = 120
        with self.assertRaises(_public().ContractValidationError):
            _api("validate_benchmark_attempt_usage")(value)

    def test_attempt_shape_rejects_implausible_retry_status_and_effort(self):
        first = _usage("alpha", terminal_status="failed")
        retry = _usage("alpha", attempt=2)
        retry["effort"] = "medium"
        valid = _attempt(_receipt(), [retry, first])
        cases = []

        same_effort = copy.deepcopy(valid)
        same_effort["workerUsageRecords"][1]["effort"] = "low"
        same_effort["workers"][1]["workerUsageDigest"] = _digest(
            same_effort["workerUsageRecords"][1]
        )
        cases.append(same_effort)

        succeeded_first = copy.deepcopy(valid)
        succeeded_first["workerUsageRecords"][0]["terminalStatus"] = "succeeded"
        succeeded_first["workers"][0]["terminalStatus"] = "succeeded"
        succeeded_first["workers"][0]["workerUsageDigest"] = _digest(
            succeeded_first["workerUsageRecords"][0]
        )
        cases.append(succeeded_first)

        for value in cases:
            with self.subTest(value=value["workerUsageRecords"]):
                with self.assertRaises(_public().ContractValidationError):
                    _api("validate_benchmark_attempt_usage")(value)

    def test_failed_attempt_overhead_includes_succeeded_work_not_integrated(self):
        receipt = _receipt()
        receipt["terminalStatus"] = "blocked"
        receipt["finalGreenSha"] = None
        result = _attempt(receipt, [_usage("alpha")], integrate=set())
        self.assertEqual("complete", result["completeness"])
        self.assertEqual(0, result["summary"]["successfulStoryCount"])
        self.assertEqual(
            120,
            result["summary"]["tokens"]["failedAttemptComparisonTokens"],
        )

    def test_missing_unavailable_invalid_duplicate_and_orphan_usage_are_incomplete(self):
        valid = _usage("alpha")
        identity = {
            "runId": valid["runId"], "storyId": "alpha", "attempt": 1,
            "launchDigest": valid["launchDigest"],
        }
        cases = {
            "no-worker-launch-events": [],
            "missing-worker-usage": [("worker-launch", identity)],
            "unavailable-worker-usage": _events([_usage("alpha", observed=False)]),
            "duplicate-worker-usage": _events([valid]) + [("worker-usage", valid)],
            "orphan-worker-usage": [("worker-usage", valid)],
            "invalid-worker-usage": _events([valid]) + [("worker-usage", {"bad": True})],
        }
        for reason, events in cases.items():
            with self.subTest(reason=reason):
                result = _attempt(_receipt(), [], events=events)
                self.assertEqual("incomplete", result["completeness"])
                self.assertIn(reason, result["incompleteReasons"])
                self.assertIsNone(result["summary"]["tokens"])

    def test_verified_integrated_story_requires_exact_completion_and_import_identity(self):
        record = _usage("alpha")
        events = _events([record])
        for kind, details in events:
            if kind == "worker-branch-import":
                details["headSha"] = "e" * 40
        result = _attempt(_receipt(), [record], events=events)
        self.assertEqual(0, result["summary"]["successfulStoryCount"])
        self.assertIn("invalid-worker-lifecycle-evidence", result["incompleteReasons"])

    def test_contracts_and_schemas_are_public_closed_and_canonical(self):
        result = _attempt(_receipt(), [_usage("alpha")])
        validate = _api("validate_benchmark_attempt_usage")
        self.assertEqual(result, validate(result))
        self.assertEqual(
            result,
            json.loads(_public().canonical_json(result, "benchmark-attempt-usage")),
        )
        schema = json.loads(
            (SCHEMAS / "benchmark-attempt-usage.schema.json").read_text(encoding="utf-8")
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        for field in ("workers", "workerUsageRecords"):
            self.assertIn("maxItems", schema["properties"][field])
        bad = copy.deepcopy(result)
        bad["extra"] = True
        with self.assertRaises(_public().ContractValidationError):
            validate(bad)

    def test_attempt_validator_recomputes_totals_and_ratios_from_bound_records(self):
        result = _attempt(_receipt(), [_usage("alpha")])
        validate = _api("validate_benchmark_attempt_usage")
        tampered = copy.deepcopy(result)
        tampered["summary"]["tokens"]["inputTokens"] += 1
        tampered["summary"]["tokens"]["comparisonTokens"] += 1
        with self.assertRaises(_public().ContractValidationError):
            validate(tampered)
        tampered = copy.deepcopy(result)
        tampered["summary"]["tokens"]["cachedInputRatio"] = "0.410000"
        with self.assertRaises(_public().ContractValidationError):
            validate(tampered)

    def test_attempt_validator_rejects_dangling_non_null_worker_usage_digest(self):
        result = _attempt(_receipt(), [_usage("alpha")])
        result["completeness"] = "incomplete"
        result["incompleteReasons"] = ["missing-worker-usage"]
        result["workerUsageRecords"] = []
        result["summary"]["tokens"] = None

        with self.assertRaisesRegex(
            _public().ContractValidationError, "workerUsageRecords"
        ):
            _api("validate_benchmark_attempt_usage")(result)

    def test_worker_usage_record_exactly_matches_worker_launch_and_status(self):
        mutations = ("launch-digest", "terminal-status", "retry-predecessor-status")
        for mutation in mutations:
            aggregate, receipts, attempts, comparison = BenchmarkTokenReportTests()._matched()
            target_index = 2
            if mutation == "retry-predecessor-status":
                first = _usage("alpha", terminal_status="failed")
                retry = _usage("alpha", attempt=2)
                retry["effort"] = "medium"
                attempts[target_index] = _attempt(
                    receipts[target_index], [retry, first]
                )
            report = _api("build_benchmark_token_report")(
                aggregate, receipts, attempts, benchmark_comparison=comparison,
            )
            tampered_attempts = copy.deepcopy(attempts)
            attempt = tampered_attempts[target_index]
            record_index = 0
            if mutation == "launch-digest":
                attempt["workerUsageRecords"][record_index]["launchDigest"] = (
                    "sha256:" + "c" * 64
                )
            elif mutation == "terminal-status":
                attempt["workerUsageRecords"][record_index]["terminalStatus"] = "failed"
            else:
                self.assertEqual("failed", attempt["workers"][0]["terminalStatus"])
                attempt["workerUsageRecords"][0]["terminalStatus"] = "succeeded"
            attempt["workers"][record_index]["workerUsageDigest"] = _digest(
                attempt["workerUsageRecords"][record_index]
            )

            forged_report = copy.deepcopy(report)
            attempt_digest = _digest(attempt)
            next(
                item for item in forged_report["attemptUsage"]
                if item["attemptId"] == attempt["attemptId"]
            )["attemptUsageDigest"] = attempt_digest
            pair = next(
                item for item in forged_report["pairSummaries"]
                if item["workloadId"] == attempt["workloadId"]
                and item["pairNumber"] == attempt["pairNumber"]
            )
            pair[
                "sequentialAttemptUsageDigest"
                if attempt["arm"] == "sequential"
                else "parallelAttemptUsageDigest"
            ] = attempt_digest

            checks = {
                "attempt": lambda: _api("validate_benchmark_attempt_usage")(attempt),
                "report-build": lambda: _api("build_benchmark_token_report")(
                    aggregate, receipts, tampered_attempts,
                    benchmark_comparison=comparison,
                ),
                "report-bind": lambda: _api(
                    "validate_benchmark_token_report_bindings"
                )(
                    forged_report, aggregate, receipts, tampered_attempts,
                    benchmark_comparison=comparison,
                ),
            }
            for check, action in checks.items():
                with self.subTest(mutation=mutation, check=check):
                    with self.assertRaises(_public().ContractValidationError):
                        action()

    def test_attempt_summary_story_bounds_match_schema(self):
        value = _fixture("benchmark-attempt-usage")
        value["completeness"] = "incomplete"
        value["incompleteReasons"] = ["missing-worker-usage"]
        value["workerUsageRecords"] = []
        value["workers"] = [{
            "runId": value["runId"], "storyId": f"story-{index:03d}",
            "attempt": 1, "launchDigest": "sha256:" + "a" * 64,
            "workerUsageDigest": None, "terminalStatus": None,
            "verifiedIntegrated": False,
        } for index in range(129)]
        value["summary"] = {
            "workerAttemptCount": 129, "attemptedStoryCount": 129,
            "successfulStoryCount": 0, "tokens": None,
        }
        with self.assertRaises(_public().ContractValidationError):
            _api("validate_benchmark_attempt_usage")(value)

    def test_attempt_schema_semantics_require_exact_trial_pair_binding(self):
        schema = json.loads(
            (SCHEMAS / "benchmark-attempt-usage.schema.json").read_text(
                encoding="utf-8"
            )
        )
        extension = schema["x-compassBuilderSemanticConstraints"]
        self.assertEqual(
            {
                "schemaVersion": "compass-builder.semantic-constraints.v1",
                "rules": [{
                    "operator": "equal",
                    "left": "$.trialNumber",
                    "right": "$.pairNumber",
                }],
            },
            extension,
        )
        measured = _attempt(_receipt(), [_usage("alpha")])
        measured["trialNumber"] = 2
        for validator in (
            lambda value: _api("validate_benchmark_attempt_usage_schema_semantics")(
                schema, value
            ),
            lambda value: _api("validate_benchmark_attempt_usage_with_schema")(
                schema, value
            ),
        ):
            with self.subTest(validator=validator):
                with self.assertRaisesRegex(
                    _public().ContractValidationError, "trialNumber"
                ):
                    validator(measured)

        warmup = _attempt(_receipt(warmup=True), [_usage("alpha")])
        warmup["pairNumber"] = 1
        warmup["trialNumber"] = 1
        with self.assertRaisesRegex(
            _public().ContractValidationError, "pairNumber"
        ):
            _api("validate_benchmark_attempt_usage_with_schema")(schema, warmup)


class BenchmarkTokenReportTests(unittest.TestCase):
    def _matched(self):
        from tests.test_builder_models import bound_aggregate_and_receipts

        aggregate, receipts = bound_aggregate_and_receipts()
        attempts = []
        for receipt in receipts:
            arm = receipt["arm"]
            receipt.update(_receipt(
                arm=arm, pair=max(1, receipt["pairNumber"]), warmup=receipt["warmup"]
            ))
            record = _usage(
                "alpha",
                input_tokens=100 if arm == "sequential" else 120,
                cached_tokens=40 if arm == "sequential" else 60,
                output_tokens=20 if arm == "sequential" else 24,
                reasoning_tokens=5 if arm == "sequential" else 6,
            )
            attempt = _attempt(receipt, [record])
            attempts.append(attempt)
        aggregate["workloadControls"][0]["controls"] = copy.deepcopy(
            receipts[0]["controls"]
        )
        aggregate["controlsDigest"] = _digest(aggregate["workloadControls"])
        for aggregate_attempt, receipt in zip(aggregate["attempts"], receipts):
            aggregate_attempt["receiptDigest"] = _digest(receipt)
        comparison = {
            "schemaVersion": "compass-builder.benchmark-comparison.v1",
            "thresholdPercent": "20.00", "graduated": True,
            "workloads": [{
                "workloadId": "sample", "pairCount": 5,
                "medianSequentialMs": "1000", "medianParallelMs": "700",
                "improvementPercent": "30.00", "firstPassSequential": 5,
                "firstPassParallel": 5, "interventionsSequential": 0,
                "interventionsParallel": 0,
                "blockingSafetyMetrics": {
                    name: 0 for name in (
                        "timeouts", "staleHeadEvents", "conflictsManualResolved",
                        "conflictsUnresolved", "scopeViolations", "manualEdits",
                        "repairDispatches",
                    )
                },
                "eligible": True, "reasons": [],
            }],
        }
        return aggregate, receipts, attempts, comparison

    def test_report_binds_existing_comparison_and_excludes_warmups(self):
        aggregate, receipts, attempts, comparison = self._matched()
        report = _api("build_benchmark_token_report")(
            aggregate, receipts, attempts, benchmark_comparison=comparison,
        )
        self.assertEqual("compass-builder.benchmark-token-report.v1", report["schemaVersion"])
        self.assertEqual(_digest(aggregate), report["benchmarkAggregateDigest"])
        self.assertEqual(_digest(comparison), report["benchmarkComparisonDigest"])
        self.assertEqual(comparison, report["benchmarkComparison"])
        self.assertEqual("complete", report["tokenVerdict"])
        self.assertFalse(report["warmupsIncludedInMeasuredComparison"])
        arms = {item["arm"]: item for item in report["armSummaries"]}
        self.assertEqual(5, arms["sequential"]["measuredAttemptCount"])
        self.assertEqual(1, arms["sequential"]["warmupAttemptCount"])
        self.assertEqual(600, arms["sequential"]["tokens"]["comparisonTokens"])
        self.assertEqual(720, arms["parallel"]["tokens"]["comparisonTokens"])
        self.assertEqual(5, len(report["pairSummaries"]))
        for pair in report["pairSummaries"]:
            self.assertEqual(24, pair["tokenDelta"])
            self.assertEqual("1.200000", pair["tokenRatio"])
            self.assertEqual(-300, pair["elapsedDeltaMs"])

    def test_report_is_incomplete_when_any_measured_usage_is_missing(self):
        aggregate, receipts, attempts, comparison = self._matched()
        broken = copy.deepcopy(attempts[2])
        broken["completeness"] = "incomplete"
        broken["incompleteReasons"] = ["missing-worker-usage"]
        broken["summary"]["tokens"] = None
        attempts[2] = _api("validate_benchmark_attempt_usage")(broken)
        report = _api("build_benchmark_token_report")(
            aggregate, receipts, attempts, benchmark_comparison=comparison,
        )
        self.assertEqual("incomplete", report["tokenVerdict"])
        self.assertIn("incomplete-attempt-usage", report["incompleteReasons"])
        self.assertIsNone(report["armSummaries"][0]["tokens"])
        self.assertTrue(any(pair["tokenVerdict"] == "incomplete" for pair in report["pairSummaries"]))

    def test_report_contract_schema_and_order_are_deterministic(self):
        aggregate, receipts, attempts, comparison = self._matched()
        build = _api("build_benchmark_token_report")
        report = build(
            aggregate, list(reversed(receipts)), list(reversed(attempts)),
            benchmark_comparison=comparison,
        )
        self.assertEqual(report, _api("validate_benchmark_token_report")(report))
        schema = json.loads(
            (SCHEMAS / "benchmark-token-report.schema.json").read_text(encoding="utf-8")
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(
            [item["attemptId"] for item in aggregate["attempts"]],
            [item["attemptId"] for item in report["attemptUsage"]],
        )

    def test_report_without_v1_comparison_keeps_receipt_bound_time_and_quality(self):
        aggregate, receipts, attempts, _comparison = self._matched()
        receipts[2]["metrics"]["retries"] = 1
        receipts[2]["metrics"]["interventions"] = 1
        aggregate["attempts"][2]["receiptDigest"] = _digest(receipts[2])
        attempts[2] = _attempt(receipts[2], [_usage("alpha")])
        report = _api("build_benchmark_token_report")(
            aggregate, receipts, attempts, benchmark_comparison=None,
        )
        self.assertIsNone(report["benchmarkComparison"])
        self.assertEqual("complete", report["tokenVerdict"])
        self.assertIn("v1-comparison-unavailable", report["incompleteReasons"])
        sequential = report["armSummaries"][0]
        self.assertEqual("1000", sequential["medianElapsedMs"])
        self.assertEqual(1, sequential["metrics"]["retries"])
        self.assertEqual(1, sequential["metrics"]["interventions"])
        self.assertEqual(4, sequential["firstPassCount"])

    def test_report_validator_binds_embedded_v1_comparison_digest(self):
        aggregate, receipts, attempts, comparison = self._matched()
        report = _api("build_benchmark_token_report")(
            aggregate, receipts, attempts, benchmark_comparison=comparison,
        )
        report["benchmarkComparison"]["graduated"] = False
        with self.assertRaises(_public().ContractValidationError):
            _api("validate_benchmark_token_report")(report)

    def test_report_schema_forbids_complete_verdict_with_token_incompleteness(self):
        schema = json.loads(
            (SCHEMAS / "benchmark-token-report.schema.json").read_text(
                encoding="utf-8"
            )
        )
        token_reasons = {
            "incomplete-attempt-usage", "missing-attempt-usage",
            "invalid-attempt-usage",
        }
        verdict_rules = [
            rule for rule in schema["allOf"]
            if rule.get("if", {}).get("properties", {}).get("tokenVerdict")
            == {"const": "complete"}
        ]
        self.assertEqual(1, len(verdict_rules))
        forbidden = set(
            verdict_rules[0]["then"]["properties"]["incompleteReasons"]
            ["items"]["not"]["enum"]
        )
        self.assertEqual(token_reasons, forbidden)

        report = _fixture("benchmark-token-report")
        for reason in sorted(token_reasons):
            with self.subTest(reason=reason):
                value = copy.deepcopy(report)
                value["incompleteReasons"] = [reason]
                with self.assertRaisesRegex(
                    _public().ContractValidationError, "tokenVerdict"
                ):
                    _api("validate_benchmark_token_report")(value)
        self.assertEqual(
            report,
            _api("validate_benchmark_token_report")(report),
        )

    def test_report_schema_requires_token_incompleteness_for_incomplete_verdict(self):
        schema = json.loads(
            (SCHEMAS / "benchmark-token-report.schema.json").read_text(
                encoding="utf-8"
            )
        )
        rule = next(
            item for item in schema["allOf"]
            if item.get("if", {}).get("properties", {}).get("tokenVerdict")
            == {"const": "complete"}
        )
        token_reasons = [
            "incomplete-attempt-usage", "missing-attempt-usage",
            "invalid-attempt-usage",
        ]
        self.assertEqual(
            {
                "properties": {
                    "incompleteReasons": {"contains": {"enum": token_reasons}}
                }
            },
            rule.get("else"),
        )
        declared = set(
            rule["else"]["properties"]["incompleteReasons"]
            ["contains"]["enum"]
        )
        for reasons in ([], ["v1-comparison-unavailable"]):
            with self.subTest(reasons=reasons):
                self.assertFalse(any(reason in declared for reason in reasons))
                value = _fixture("benchmark-token-report")
                value["tokenVerdict"] = "incomplete"
                value["incompleteReasons"] = reasons
                with self.assertRaisesRegex(
                    _public().ContractValidationError, "tokenVerdict"
                ):
                    _api("validate_benchmark_token_report")(value)

    def test_report_binding_rejects_all_derived_and_document_mutation_families(self):
        aggregate, receipts, attempts, comparison = self._matched()
        report = _api("build_benchmark_token_report")(
            aggregate, receipts, attempts, benchmark_comparison=comparison,
        )
        bind = _api("validate_benchmark_token_report_bindings")
        self.assertEqual(
            report,
            bind(
                report, aggregate, receipts, attempts,
                benchmark_comparison=comparison,
            ),
        )

        mutations = {}
        value = copy.deepcopy(report)
        value["armSummaries"][0]["tokens"]["cachedInputRatio"] = "0.500000"
        mutations["ratios"] = value
        value = copy.deepcopy(report)
        value["armSummaries"][0]["tokens"]["retryComparisonTokens"] = 1
        mutations["totals"] = value
        value = copy.deepcopy(report)
        value["armSummaries"][0]["attemptedStoryCount"] += 1
        mutations["counts"] = value
        value = copy.deepcopy(report)
        value["armSummaries"][0]["medianElapsedMs"] = "999"
        mutations["median"] = value
        value = copy.deepcopy(report)
        value["armSummaries"][0]["terminalStatusCounts"]["green"] -= 1
        value["armSummaries"][0]["terminalStatusCounts"]["failed"] += 1
        mutations["status"] = value
        value = copy.deepcopy(report)
        value["armSummaries"][0]["metrics"]["conflictsDetected"] = 1
        mutations["metrics"] = value
        value = copy.deepcopy(report)
        value["pairSummaries"][0]["sequentialElapsedMs"] = 999
        value["pairSummaries"][0]["elapsedDeltaMs"] = -299
        mutations["pair elapsed"] = value
        value = copy.deepcopy(report)
        value["pairSummaries"][0]["tokenRatio"] = "1.100000"
        mutations["pair token ratio"] = value
        value = copy.deepcopy(report)
        value["pairSummaries"][0]["parallelTokens"]["inputTokens"] += 1
        value["pairSummaries"][0]["parallelTokens"]["comparisonTokens"] += 1
        value["pairSummaries"][0]["tokenDelta"] += 1
        mutations["pair token delta"] = value
        value = copy.deepcopy(report)
        value["pairSummaries"][0]["tokenVerdict"] = "incomplete"
        for field in (
            "sequentialTokens", "parallelTokens", "tokenDelta", "tokenRatio",
        ):
            value["pairSummaries"][0][field] = None
        mutations["pair token verdict"] = value
        value = copy.deepcopy(report)
        value["pairSummaries"][0]["sequentialAttemptUsageDigest"] = (
            "sha256:" + "9" * 64
        )
        mutations["pair digest"] = value
        value = copy.deepcopy(report)
        value["attemptUsage"].reverse()
        mutations["attempt refs/order"] = value
        value = copy.deepcopy(report)
        value["benchmarkAggregateDigest"] = "sha256:" + "8" * 64
        mutations["aggregate digest"] = value
        value = copy.deepcopy(report)
        value["benchmarkComparison"]["graduated"] = False
        value["benchmarkComparisonDigest"] = _digest(value["benchmarkComparison"])
        mutations["comparison/digest"] = value
        for field, replacement in (
            ("improvementPercent", "99.99"),
            ("eligible", False),
            ("reasons", ["forged reason"]),
        ):
            value = copy.deepcopy(report)
            value["benchmarkComparison"]["workloads"][0][field] = replacement
            value["benchmarkComparisonDigest"] = _digest(
                value["benchmarkComparison"]
            )
            mutations[f"comparison {field}"] = value

        for family, value in mutations.items():
            with self.subTest(family=family):
                with self.assertRaises(_public().ContractValidationError):
                    bind(
                        value, aggregate, receipts, attempts,
                        benchmark_comparison=comparison,
                    )

    def test_v1_comparison_binding_rejects_decision_field_mutations(self):
        aggregate, receipts, attempts, comparison = self._matched()
        mutations = []
        for field, replacement in (
            ("improvementPercent", "99.99"),
            ("eligible", False),
            ("reasons", ["forged reason"]),
        ):
            value = copy.deepcopy(comparison)
            value["workloads"][0][field] = replacement
            mutations.append((field, value))
        value = copy.deepcopy(comparison)
        value["graduated"] = False
        mutations.append(("graduated", value))
        for field, value in mutations:
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    _api("build_benchmark_token_report")(
                        aggregate, receipts, attempts,
                    benchmark_comparison=value,
                )

    def test_report_binding_rejects_attempt_one_effort_detached_from_receipt(self):
        aggregate, receipts, attempts, comparison = self._matched()
        attempts[2]["workerUsageRecords"][0]["effort"] = "medium"
        attempts[2]["workers"][0]["workerUsageDigest"] = _digest(
            attempts[2]["workerUsageRecords"][0]
        )
        attempts[2] = _api("validate_benchmark_attempt_usage")(attempts[2])
        with self.assertRaises(ValueError):
            _api("build_benchmark_token_report")(
                aggregate, receipts, attempts,
                benchmark_comparison=comparison,
            )

    def test_signed_deltas_reject_boolean_values(self):
        validate = _api("validate_benchmark_token_report")
        for field in ("tokenDelta", "elapsedDeltaMs"):
            with self.subTest(field=field):
                value = _fixture("benchmark-token-report")
                value["pairSummaries"][0][field] = False
                with self.assertRaisesRegex(
                    _public().ContractValidationError, field
                ):
                    validate(value)

    def test_report_scalar_bounds_and_median_format_match_schema(self):
        validate = _api("validate_benchmark_token_report")
        maximum = 9_007_199_254_740_991
        aggregate, receipts, attempts, comparison = self._matched()
        valid = _api("build_benchmark_token_report")(
            aggregate, receipts, attempts, benchmark_comparison=comparison,
        )
        valid["armSummaries"][0]["metrics"]["retries"] = maximum
        valid["armSummaries"][0]["terminalStatusCounts"]["green"] = maximum
        valid["armSummaries"][0]["medianElapsedMs"] = "1.5"
        valid["benchmarkComparison"]["workloads"][0][
            "blockingSafetyMetrics"
        ]["timeouts"] = maximum
        valid["benchmarkComparisonDigest"] = _digest(valid["benchmarkComparison"])
        self.assertEqual(valid, validate(valid))
        cases = []
        for target in ("metrics", "terminalStatusCounts"):
            for bad in (False, -1, maximum + 1):
                value = _fixture("benchmark-token-report")
                field = "retries" if target == "metrics" else "green"
                value["armSummaries"][0][target][field] = bad
                cases.append((f"{target}.{field}", value))
        for bad in ("not-a-number", "01", "1.0", "1.50"):
            value = _fixture("benchmark-token-report")
            value["armSummaries"][0]["medianElapsedMs"] = bad
            cases.append(("medianElapsedMs", value))
        value = copy.deepcopy(valid)
        value["benchmarkComparison"]["workloads"][0][
            "blockingSafetyMetrics"
        ]["timeouts"] = maximum + 1
        value["benchmarkComparisonDigest"] = _digest(value["benchmarkComparison"])
        cases.append(("blockingSafetyMetrics.timeouts", value))
        for field, value in cases:
            with self.subTest(field=field, value=value["armSummaries"][0]):
                with self.assertRaises(_public().ContractValidationError):
                    validate(value)

    def test_comparison_reasons_match_schema_string_bounds(self):
        aggregate, receipts, attempts, comparison = self._matched()
        report = _api("build_benchmark_token_report")(
            aggregate, receipts, attempts, benchmark_comparison=comparison,
        )
        validate = _api("validate_benchmark_token_report")
        for reason in ("x", "x" * 4096):
            value = copy.deepcopy(report)
            value["benchmarkComparison"]["workloads"][0]["reasons"] = [reason]
            value["benchmarkComparisonDigest"] = _digest(value["benchmarkComparison"])
            self.assertEqual(value, validate(value))
        for reason in (False, 1, "", "x" * 4097):
            with self.subTest(reason=reason):
                value = copy.deepcopy(report)
                value["benchmarkComparison"]["workloads"][0]["reasons"] = [reason]
                value["benchmarkComparisonDigest"] = _digest(
                    value["benchmarkComparison"]
                )
                with self.assertRaises(_public().ContractValidationError):
                    validate(value)


if __name__ == "__main__":
    unittest.main()
