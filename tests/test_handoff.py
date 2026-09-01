from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests.test_decision import PLUGIN_ROOT
from plugin_compass.handoff import build_handoff
from plugin_compass.cli import main


def task_spec():
    return {
        "schema_version": "plugin-compass.agent-task.v1",
        "task_id": "check_schema",
        "task": "Check the fixture output against its JSON Schema; report discrepancies.",
        "context": "Read schemas/ and tests/ only. Do not edit files or launch other agents.",
        "selected_model": "inherit",
        "supported_efforts": ["low", "medium", "high", "xhigh"],
        "support_evidence": "Current native dispatch tool schema, checked this turn.",
        "assessment": {"complexity": "low", "ambiguity": "low", "risk": "low", "validation_strength": "decisive", "rationale": "Single schema comparison with explicit fixtures and a decisive validator."},
        "acceptance_checks": ["Validate each fixture against the schema and report actual validator results."],
        "delegation_authorized": True,
        "delegation_worthwhile": True,
    }


class HandoffTests(unittest.TestCase):
    def test_low_risk_task_uses_low_and_inherits_model(self):
        output = build_handoff(task_spec())
        self.assertEqual("proposed", output["status"])
        self.assertEqual("low", output["recommended_effort"])
        self.assertNotIn("model", output["dispatch_arguments"])
        self.assertEqual("none", output["dispatch_arguments"]["fork_turns"])
        self.assertEqual("proposal_only", output["enforcement"])

    def test_risk_ambiguity_and_weak_validation_raise_floor(self):
        for field, value in (("risk", "high"), ("ambiguity", "high"), ("complexity", "high"), ("validation_strength", "partial")):
            with self.subTest(field=field):
                spec = task_spec()
                spec["assessment"][field] = value
                self.assertEqual("high", build_handoff(spec)["recommended_effort"])
        spec = task_spec()
        spec["assessment"]["complexity"] = "medium"
        self.assertEqual("medium", build_handoff(spec)["recommended_effort"])

    def test_missing_medium_uses_next_supported_effort_without_changing_model(self):
        spec = task_spec()
        spec["selected_model"] = "user-selected-model"
        spec["supported_efforts"] = ["low", "high"]
        spec["assessment"]["complexity"] = "medium"
        output = build_handoff(spec)
        self.assertEqual("high", output["recommended_effort"])
        self.assertEqual("user-selected-model", output["dispatch_arguments"]["model"])

    def test_gates_never_emit_dispatch_arguments(self):
        cases = [("delegation_authorized", False, "needs_authorization"), ("delegation_worthwhile", False, "keep_local"), ("supported_efforts", ["low"], "needs_supported_effort")]
        for key, value, expected in cases:
            spec = task_spec()
            spec[key] = value
            spec["assessment"]["risk"] = "high"
            output = build_handoff(spec)
            self.assertEqual(expected, output["status"])
            self.assertIsNone(output["dispatch_arguments"])
        spec = task_spec()
        spec["assessment"]["validation_strength"] = "none"
        self.assertEqual("needs_validation", build_handoff(spec)["status"])

    def test_retry_is_diagnosed_bounded_and_same_model(self):
        spec = task_spec()
        spec["selected_model"] = "same-model"
        spec["previous_attempt"] = {"selected_model": "same-model", "reasoning_effort": "low", "reasoning_retries": 0, "failure_kind": "reasoning", "failed_evidence": ["Validator error at fixture A property x"]}
        self.assertEqual("medium", build_handoff(spec)["recommended_effort"])
        for kind in ("tool", "permission", "missing_input", "unknown"):
            spec["previous_attempt"]["failure_kind"] = kind
            self.assertEqual("needs_diagnosis", build_handoff(spec)["status"])
        spec["previous_attempt"]["failure_kind"] = "reasoning"
        spec["previous_attempt"]["reasoning_retries"] = 1
        self.assertEqual("needs_review", build_handoff(spec)["status"])
        spec["previous_attempt"]["selected_model"] = "different-model"
        with self.assertRaises(ValueError):
            build_handoff(spec)

    def test_retry_cannot_inherit_a_possibly_changed_parent_model(self):
        spec = task_spec()
        spec["previous_attempt"] = {"selected_model": "inherit", "reasoning_effort": "low", "reasoning_retries": 0, "failure_kind": "reasoning", "failed_evidence": ["failure evidence"]}
        with self.assertRaisesRegex(ValueError, "exact model ID"):
            build_handoff(spec)

    def test_malformed_or_incomplete_inputs_are_rejected(self):
        for key, value in (("acceptance_checks", []), ("support_evidence", ""), ("supported_efforts", ["guess"]), ("supported_efforts", ["low", "low"]), ("delegation_authorized", "true"), ("task_id", "../../bad"), ("previous_attempt", None)):
            spec = task_spec()
            spec[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                build_handoff(spec)
        spec = task_spec()
        spec["unexpected"] = True
        with self.assertRaises(ValueError):
            build_handoff(spec)

    def test_deterministic_no_input_mutation(self):
        spec = task_spec()
        before = copy.deepcopy(spec)
        first = build_handoff(spec)
        self.assertEqual(before, spec)
        spec["supported_efforts"].reverse()
        self.assertEqual(first, build_handoff(spec))

    def test_handoff_cli_never_discovers_or_executes_plugins(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task.json"
            path.write_text(json.dumps(task_spec()), encoding="utf-8")
            with patch("subprocess.run", side_effect=AssertionError("no execution")):
                stdout, stderr = io.StringIO(), io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = main(["handoff", "--task-file", str(path), "--format", "json"])
        self.assertEqual(0, code, stderr.getvalue())
        self.assertEqual("proposed", json.loads(stdout.getvalue())["status"])

    def test_handoff_cli_gate_uses_exit_four_and_no_dispatch_arguments(self):
        spec = task_spec()
        spec["delegation_authorized"] = False
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["handoff", "--task-file", str(path), "--format", "json"])
        self.assertEqual(4, code, stderr.getvalue())
        output = json.loads(stdout.getvalue())
        self.assertEqual("needs_authorization", output["status"])
        self.assertIsNone(output["dispatch_arguments"])


if __name__ == "__main__":
    unittest.main()
