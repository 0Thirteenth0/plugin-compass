from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "plugins" / "compass-builder"
PLUGIN_COMPASS = ROOT / "plugins" / "plugin-compass"
if str(BUILDER) not in sys.path:
    sys.path.insert(0, str(BUILDER))

from compass_builder.handoff import (  # noqa: E402
    HandoffError, bind_handoff, build_agent_task, invoke_handoff,
    resolve_plugin_compass,
)
from compass_builder.cli import main  # noqa: E402


def load(name: str) -> dict:
    path = ROOT / "tests" / "fixtures" / "compass_builder" / f"{name}.valid.json"
    return json.loads(path.read_text(encoding="utf-8"))


def proposal(task: dict, effort: str = "low") -> dict:
    return {
        "schema_version": "plugin-compass.handoff.v1",
        "decision_id": "handoff-test",
        "status": "proposed",
        "objective": "fastest_verified_completion",
        "enforcement": "proposal_only",
        "evidence_basis": "caller_supplied_task_and_host_capabilities",
        "task_id": task["task_id"],
        "selected_model": task["selected_model"],
        "supported_efforts": task["supported_efforts"],
        "support_evidence": task["support_evidence"],
        "assessment": task["assessment"],
        "acceptance_checks": task["acceptance_checks"],
        "recommended_effort": effort,
        "rationale": "Bounded test proposal.",
        "dispatch_tool": "collaboration.spawn_agent",
        "dispatch_arguments": {
            "task_name": task["task_id"], "message": "Bounded task.",
            "fork_turns": "none", "reasoning_effort": effort,
            "model": task["selected_model"],
        },
        "max_reasoning_retries": 1,
        "previous_attempt": None,
        "validation_owner": "Controller validates.",
        "limitations": "Proposal only.",
    }


class HandoffTests(unittest.TestCase):
    def setUp(self):
        spec, host = load("run-spec"), load("host-capabilities")
        self.host = host
        self.task = build_agent_task(spec["stories"][0], host, index=0)

    def test_agent_task_is_derived_only_from_declared_story_and_host_fields(self):
        self.assertEqual("plugin-compass.agent-task.v1", self.task["schema_version"])
        self.assertEqual(self.host["selectedModel"], self.task["selected_model"])
        self.assertIn("Do not launch other agents", self.task["context"])
        self.assertEqual("low", self.task["assessment"]["complexity"])
        spec = load("run-spec")
        review = copy.deepcopy(spec["stories"][0])
        review.update({
            "acceptanceChecks": [], "validationCommands": [],
            "independentReviewPath": "Review the alpha rubric independently.",
        })
        review_task = build_agent_task(review, self.host, index=0)
        self.assertEqual(
            ["Independent review: Review the alpha rubric independently."],
            review_task["acceptance_checks"],
        )

    def test_explicit_and_authoritative_inventory_resolution(self):
        self.assertEqual(PLUGIN_COMPASS.resolve(), resolve_plugin_compass(explicit_root=PLUGIN_COMPASS))
        inventory = {
            "installed": [{
                "name": "plugin-compass", "installed": True, "enabled": True,
                "source": {"path": "plugins/plugin-compass"},
            }]
        }
        self.assertEqual(
            PLUGIN_COMPASS.resolve(),
            resolve_plugin_compass(inventory=inventory, inventory_base=ROOT),
        )
        inventory["installed"][0]["enabled"] = False
        with self.assertRaisesRegex(HandoffError, "one enabled"):
            resolve_plugin_compass(inventory=inventory, inventory_base=ROOT)

    def test_transport_invokes_only_public_handoff_command(self):
        observed = []

        def runner(argv):
            observed.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, json.dumps(proposal(self.task)), "")

        output = invoke_handoff(PLUGIN_COMPASS, self.task, runner=runner)
        self.assertEqual("proposed", output["status"])
        self.assertEqual(1, len(observed))
        self.assertEqual("handoff", observed[0][2])
        self.assertNotIn("install", observed[0])

    def test_real_public_handoff_accepts_maximum_bounded_check_mapping(self):
        spec = load("run-spec")
        story = copy.deepcopy(spec["stories"][0])
        story["acceptanceChecks"] = [f"Acceptance check {index}" for index in range(32)]
        story["validationCommands"] = [f"python check_{index}.py" for index in range(32)]
        task = build_agent_task(story, self.host, index=0)
        self.assertEqual(32, len(task["acceptance_checks"]))
        self.assertIn("validation=sha256:", task["support_evidence"])
        output = invoke_handoff(PLUGIN_COMPASS, task)
        bound = bind_handoff(task, output, self.host)
        self.assertEqual("low", bound["recommendedEffort"])

    def test_cli_reports_strict_inventory_decode_failure_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            spec_path = Path(directory) / "spec.json"
            native_path = Path(directory) / "native.json"
            spec_path.write_text("{}", encoding="utf-8")
            native_path.write_text("{}", encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("compass_builder.cli.run_doctor", return_value={}), patch(
                "compass_builder.cli.subprocess.run",
                side_effect=UnicodeError("invalid inventory encoding"),
            ), redirect_stdout(stdout), redirect_stderr(stderr):
                code = main([
                    "plan", "--repo", str(ROOT), "--spec", str(spec_path),
                    "--native-capabilities", str(native_path), "--mode", "auto",
                ])
        self.assertEqual(4, code)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("invalid inventory encoding", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_binding_is_deterministic_and_covers_target_task(self):
        output = proposal(self.task)
        first = bind_handoff(self.task, output, self.host)
        self.assertEqual(first, bind_handoff(self.task, copy.deepcopy(output), self.host))
        changed = copy.deepcopy(self.task)
        changed["context"] += " More bounded context."
        second = bind_handoff(changed, proposal(changed), self.host)
        self.assertNotEqual(first["targetTaskDigest"], second["targetTaskDigest"])
        self.assertNotEqual(first["handoffDigest"], second["handoffDigest"])

    def test_gated_mismatched_or_unsupported_handoff_fails_closed(self):
        bad = proposal(self.task)
        bad["status"] = "needs_validation"
        with self.assertRaisesRegex(HandoffError, "status"):
            bind_handoff(self.task, bad, self.host)
        bad = copy.deepcopy(proposal(self.task))
        bad["assessment"]["risk"] = "high"
        with self.assertRaisesRegex(HandoffError, "assessment"):
            bind_handoff(self.task, bad, self.host)
        bad = proposal(self.task, "ultra")
        with self.assertRaisesRegex(HandoffError, "unsupported effort"):
            bind_handoff(self.task, bad, self.host)
        incomplete = proposal(self.task)
        del incomplete["decision_id"]
        with self.assertRaisesRegex(HandoffError, "closed v1"):
            bind_handoff(self.task, incomplete, self.host)


if __name__ == "__main__":
    unittest.main()
