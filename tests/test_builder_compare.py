from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "plugins" / "compass-builder"
if str(BUILDER) not in sys.path:
    sys.path.insert(0, str(BUILDER))

from compass_builder.benchmark import (  # noqa: E402
    ComparisonError, GENESIS_HASH, compare,
)
from compass_builder.models import canonical_json  # noqa: E402


def _hash(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def _controls() -> dict[str, object]:
    fixture = json.loads((
        ROOT / "tests" / "fixtures" / "compass_builder" /
        "benchmark-receipt.valid.json"
    ).read_text(encoding="utf-8"))
    return fixture["controls"]


def _metrics() -> dict[str, int]:
    return {
        "retries": 0, "interventions": 0, "conflictsDetected": 0,
        "conflictsAutoResolved": 0, "conflictsManualResolved": 0,
        "conflictsUnresolved": 0, "scopeViolations": 0,
        "staleHeadEvents": 0, "timeouts": 0, "checkFailures": 0,
        "checkReruns": 0, "repairDispatches": 0, "manualEdits": 0,
    }


def _event(previous: str, sequence: int, kind: str, attempt_id: str):
    value = {
        "sequence": sequence, "previousHash": previous, "kind": kind,
        "details": {"attemptId": attempt_id},
    }
    value["eventHash"] = _hash(value)
    return value


def arm(arm_name: str, durations: list[int]):
    receipts, events = [], []
    previous = GENESIS_HASH
    for pair, elapsed in enumerate(durations, 1):
        attempt_id = f"sample-p{pair}-{'seq' if arm_name == 'sequential' else 'par'}"
        first = len(events) + 1
        for kind in ("attempt-start", "worker-launch", "attempt-completion"):
            event = _event(previous, len(events) + 1, kind, attempt_id)
            events.append(event)
            previous = event["eventHash"]
        started = datetime(2026, 9, 1, 12, pair, tzinfo=timezone.utc)
        ended = started + timedelta(milliseconds=elapsed)
        receipts.append({
            "schemaVersion": "compass-builder.benchmark-receipt.v1",
            "workloadId": "sample", "attemptId": attempt_id, "arm": arm_name,
            "pairNumber": pair, "trialNumber": pair, "warmup": False,
            "controls": copy.deepcopy(_controls()),
            "startedAt": started.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "endedAt": ended.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "elapsedMs": elapsed, "metrics": _metrics(),
            "eventLedger": {
                "firstSequence": first, "lastSequence": len(events),
                "terminalHash": previous,
            },
            "finalGreenSha": "b" * 40, "terminalStatus": "green",
        })
    return receipts, events


class BuilderComparisonTests(unittest.TestCase):
    def test_comparison_v1_full_output_remains_byte_stable(self):
        sequential, seq_events = arm("sequential", [1000] * 5)
        parallel, par_events = arm("parallel", [800] * 5)
        result = compare(
            sequential, parallel,
            sequential_events=seq_events, parallel_events=par_events,
        )
        self.assertEqual({
            "schemaVersion": "compass-builder.benchmark-comparison.v1",
            "thresholdPercent": "20.00", "graduated": True,
            "workloads": [{
                "workloadId": "sample", "pairCount": 5,
                "medianSequentialMs": "1000", "medianParallelMs": "800",
                "improvementPercent": "20.00", "firstPassSequential": 5,
                "firstPassParallel": 5, "interventionsSequential": 0,
                "interventionsParallel": 0,
                "blockingSafetyMetrics": {
                    "timeouts": 0, "staleHeadEvents": 0,
                    "conflictsManualResolved": 0, "conflictsUnresolved": 0,
                    "scopeViolations": 0, "manualEdits": 0,
                    "repairDispatches": 0,
                },
                "eligible": True, "reasons": [],
            }],
        }, result)

    def test_exact_twenty_percent_uses_unrounded_decimal_and_graduates(self):
        sequential, seq_events = arm("sequential", [1000] * 5)
        parallel, par_events = arm("parallel", [800] * 5)
        result = compare(
            sequential, parallel,
            sequential_events=seq_events, parallel_events=par_events,
        )
        self.assertTrue(result["graduated"])
        self.assertEqual("20.00", result["workloads"][0]["improvementPercent"])

    def test_display_rounding_never_promotes_below_threshold(self):
        sequential, seq_events = arm("sequential", [19999] * 5)
        parallel, par_events = arm("parallel", [16000] * 5)
        result = compare(
            sequential, parallel,
            sequential_events=seq_events, parallel_events=par_events,
        )
        self.assertEqual("20.00", result["workloads"][0]["improvementPercent"])
        self.assertFalse(result["graduated"])

    def test_safety_precedes_large_speed_improvement(self):
        sequential, seq_events = arm("sequential", [1000] * 5)
        parallel, par_events = arm("parallel", [100] * 5)
        parallel[0]["metrics"].update(
            interventions=1, conflictsDetected=1, conflictsManualResolved=1,
        )
        result = compare(
            sequential, parallel,
            sequential_events=seq_events, parallel_events=par_events,
        )
        report = result["workloads"][0]
        self.assertFalse(result["graduated"])
        self.assertIn("blocking safety metric", "; ".join(report["reasons"]))

    def test_lower_first_pass_acceptance_blocks_graduation(self):
        sequential, seq_events = arm("sequential", [1000] * 5)
        parallel, par_events = arm("parallel", [100] * 5)
        parallel[0]["metrics"]["retries"] = 1
        result = compare(
            sequential, parallel,
            sequential_events=seq_events, parallel_events=par_events,
        )
        self.assertFalse(result["graduated"])
        self.assertIn("first-pass", "; ".join(result["workloads"][0]["reasons"]))

    def test_mismatched_controls_and_unequal_trials_fail_closed(self):
        sequential, seq_events = arm("sequential", [1000] * 5)
        parallel, par_events = arm("parallel", [700] * 5)
        parallel[0]["controls"]["timeoutMs"] += 1
        with self.assertRaises(ValueError):
            compare(
                sequential, parallel,
                sequential_events=seq_events, parallel_events=par_events,
            )
        parallel, par_events = arm("parallel", [700] * 4)
        with self.assertRaises(ComparisonError):
            compare(
                sequential, parallel,
                sequential_events=seq_events, parallel_events=par_events,
            )

    def test_ledger_gap_hash_mismatch_and_input_request_fail_closed(self):
        sequential, seq_events = arm("sequential", [1000] * 5)
        parallel, par_events = arm("parallel", [700] * 5)
        broken = copy.deepcopy(par_events)
        broken[1]["sequence"] = 9
        with self.assertRaisesRegex(ComparisonError, "gap"):
            compare(
                sequential, parallel,
                sequential_events=seq_events, parallel_events=broken,
            )
        blocked = copy.deepcopy(par_events)
        blocked[1]["kind"] = "input-request"
        for index in range(1, len(blocked)):
            blocked[index]["previousHash"] = blocked[index - 1]["eventHash"]
            blocked[index]["eventHash"] = _hash({
                key: value for key, value in blocked[index].items() if key != "eventHash"
            })
        for receipt in parallel:
            last = receipt["eventLedger"]["lastSequence"]
            receipt["eventLedger"]["terminalHash"] = blocked[last - 1]["eventHash"]
        with self.assertRaisesRegex(ComparisonError, "manual input"):
            compare(
                sequential, parallel,
                sequential_events=seq_events, parallel_events=blocked,
            )


if __name__ == "__main__":
    unittest.main()
