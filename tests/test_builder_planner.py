from __future__ import annotations

import copy
import json
import sys
import unittest
from hashlib import sha256
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "plugins" / "compass-builder"
PLUGIN_COMPASS = ROOT / "plugins" / "plugin-compass"
if str(BUILDER) not in sys.path:
    sys.path.insert(0, str(BUILDER))

from compass_builder.models import canonical_json  # noqa: E402
from compass_builder.planner import PlanningError, build_plan  # noqa: E402


def load(name: str) -> dict:
    path = ROOT / "tests" / "fixtures" / "compass_builder" / f"{name}.valid.json"
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value: dict) -> str:
    return "sha256:" + sha256(canonical_json(value)).hexdigest()


def provider(task: dict) -> dict:
    effort = {"low": "low", "medium": "medium", "high": "high"}[task["assessment"]["complexity"]]
    return {
        "schema_version": "plugin-compass.handoff.v1",
        "decision_id": "handoff-" + task["task_id"],
        "status": "proposed", "objective": "fastest_verified_completion",
        "enforcement": "proposal_only", "task_id": task["task_id"],
        "evidence_basis": "caller_supplied_task_and_host_capabilities",
        "selected_model": task["selected_model"],
        "supported_efforts": task["supported_efforts"],
        "support_evidence": task["support_evidence"],
        "assessment": task["assessment"], "acceptance_checks": task["acceptance_checks"],
        "recommended_effort": effort, "rationale": "Bounded test proposal.",
        "dispatch_tool": "collaboration.spawn_agent",
        "dispatch_arguments": {
            "task_name": task["task_id"], "message": "Bounded task.",
            "fork_turns": "none", "reasoning_effort": effort,
            "model": task["selected_model"],
        },
        "max_reasoning_retries": 1, "previous_attempt": None,
        "validation_owner": "Controller validates.", "limitations": "Proposal only.",
    }


def report(spec: dict, *, clean=True, supports=None) -> dict:
    host = load("host-capabilities")
    host["selectedModel"] = spec["exactModel"]
    host["hostConcurrencyCeiling"] = spec["hostConcurrencyCeiling"]
    host["userConcurrencyCeiling"] = spec["userConcurrencyCeiling"]
    if supports is not None:
        host["supports"] = supports
    return {
        "hostCapabilities": host,
        "hostEvidenceDigest": digest(host),
        "planningTimestamp": "2026-09-01T12:01:00Z",
        "resolvedBaseSha": spec["baseSha"],
        "workingTreeClean": clean,
    }


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.spec = load("run-spec")

    def plan(self, spec=None, doctor=None, mode="auto", **kwargs):
        value = spec or self.spec
        return build_plan(
            value, doctor or report(value), requested_mode=mode,
            plugin_compass_root=PLUGIN_COMPASS, handoff_provider=provider, **kwargs,
        )

    def test_auto_parallel_plan_is_deterministic_and_dependency_waves_are_ordered(self):
        spec = copy.deepcopy(self.spec)
        spec["stories"][0]["complexity"] = "medium"
        first = self.plan(spec)
        second = self.plan(copy.deepcopy(spec), doctor=copy.deepcopy(report(spec)))
        self.assertEqual(canonical_json(first), canonical_json(second))
        self.assertEqual("parallel", first["mode"])
        self.assertEqual(2, first["concurrency"])
        self.assertEqual([["alpha", "beta"]], [wave["storyIds"] for wave in first["waves"]])

    def test_auto_uses_sequential_when_coordination_value_is_too_low(self):
        plan = self.plan()
        self.assertEqual("sequential", plan["mode"])
        self.assertEqual(1, plan["concurrency"])
        self.assertIn("coordination benefit", plan["reasons"][0])

    def test_policy_gates_the_same_two_story_wave_that_the_plan_emits(self):
        spec = copy.deepcopy(self.spec)
        gamma = copy.deepcopy(spec["stories"][0])
        gamma.update({"id": "gamma", "title": "Gamma", "writeScopes": ["src/gamma"]})
        spec["stories"].append(gamma)
        spec["hostConcurrencyCeiling"] = 3
        spec["userConcurrencyCeiling"] = 3
        plan = self.plan(spec, doctor=report(spec))
        self.assertEqual("sequential", plan["mode"])
        self.assertIn("coordination benefit", plan["reasons"][0])

    def test_explicit_sequential_remains_available_when_parallel_host_gate_fails(self):
        support = {name: False for name in load("host-capabilities")["supports"]}
        plan = self.plan(doctor=report(self.spec, clean=False, supports=support), mode="sequential")
        self.assertEqual("sequential", plan["mode"])

    def test_decisive_independent_review_is_actionable_for_parallel_planning(self):
        spec = copy.deepcopy(self.spec)
        for story in spec["stories"]:
            story["complexity"] = "medium"
        spec["stories"][0]["acceptanceChecks"] = []
        spec["stories"][0]["validationCommands"] = []
        spec["stories"][0]["independentReviewPath"] = "Independent reviewer runs the alpha acceptance rubric."
        plan = self.plan(spec, mode="parallel")
        self.assertEqual("parallel", plan["mode"])

    def test_wave_order_never_skips_a_blocked_earlier_story(self):
        spec = copy.deepcopy(self.spec)
        gamma = copy.deepcopy(spec["stories"][0])
        gamma.update({"id": "gamma", "title": "Gamma", "dependsOn": [], "writeScopes": ["src/gamma"]})
        spec["stories"][1]["dependsOn"] = ["alpha"]
        spec["stories"].append(gamma)
        plan = self.plan(spec, mode="sequential")
        self.assertEqual([["alpha"], ["beta"], ["gamma"]], [wave["storyIds"] for wave in plan["waves"]])

    def test_auto_checks_later_prospective_waves_before_selecting_parallel(self):
        spec = copy.deepcopy(self.spec)
        for story in spec["stories"]:
            story["complexity"] = "medium"
        gamma = copy.deepcopy(spec["stories"][0])
        gamma.update({
            "id": "gamma", "title": "Gamma", "dependsOn": ["alpha", "beta"],
            "writeScopes": ["src/gamma"], "complexity": "medium",
            "sharedState": {"mode": "mutates", "description": "Updates shared generated state."},
        })
        spec["stories"].append(gamma)
        plan = self.plan(spec)
        self.assertEqual("sequential", plan["mode"])
        self.assertTrue(any("shared state" in reason for reason in plan["reasons"]))

    def test_every_parallel_gate_fails_closed_without_unauthorized_fallback(self):
        cases = []
        one = copy.deepcopy(self.spec); one["stories"] = one["stories"][:1]
        cases.append(("fewer", one, report(one), {}))
        cases.append(("dirty", self.spec, report(self.spec, clean=False), {}))
        support = copy.deepcopy(load("host-capabilities")["supports"]); support["worktrees"] = False
        cases.append(("support", self.spec, report(self.spec, supports=support), {}))
        shared = copy.deepcopy(self.spec); shared["stories"][0]["sharedState"]["mode"] = "mutates"
        cases.append(("shared", shared, report(shared), {}))
        weak = copy.deepcopy(self.spec); weak["stories"][0]["validationStrength"] = "partial"
        cases.append(("decisive", weak, report(weak), {}))
        overlap = copy.deepcopy(self.spec); overlap["stories"][1]["writeScopes"] = ["SRC/ALPHA/child"]
        cases.append(("overlap", overlap, report(overlap), {}))
        cases.append(("prior", self.spec, report(self.spec), {"prior_wave_failed": True}))
        ceiling = copy.deepcopy(self.spec); ceiling["hostConcurrencyCeiling"] = 1
        cases.append(("ceiling", ceiling, report(ceiling), {}))
        cases.append(("benefit", self.spec, report(self.spec), {}))
        for label, spec, doctor, kwargs in cases:
            with self.subTest(label=label), self.assertRaisesRegex(PlanningError, "explicit parallel is unavailable"):
                self.plan(spec, doctor=doctor, mode="parallel", **kwargs)

    def test_unknown_and_cyclic_dependencies_fail_before_planning(self):
        unknown = copy.deepcopy(self.spec)
        unknown["stories"][1]["dependsOn"] = ["missing"]
        with self.assertRaisesRegex(PlanningError, "unknown"):
            self.plan(unknown, doctor=report(unknown))
        cyclic = copy.deepcopy(self.spec)
        cyclic["stories"][0]["dependsOn"] = ["beta"]
        cyclic["stories"][1]["dependsOn"] = ["alpha"]
        with self.assertRaisesRegex(PlanningError, "cycle"):
            self.plan(cyclic, doctor=report(cyclic))

    def test_failed_gated_mismatched_or_unsupported_handoff_blocks_all_modes(self):
        def gated(task):
            output = provider(task); output["status"] = "needs_validation"; return output

        with self.assertRaisesRegex(PlanningError, "handoff failed closed"):
            build_plan(
                self.spec, report(self.spec), requested_mode="sequential",
                plugin_compass_root=PLUGIN_COMPASS, handoff_provider=gated,
            )
        def unsupported(task):
            output = provider(task); output["recommended_effort"] = "ultra"; return output

        with self.assertRaisesRegex(PlanningError, "unsupported effort"):
            build_plan(
                self.spec, report(self.spec), requested_mode="auto",
                plugin_compass_root=PLUGIN_COMPASS, handoff_provider=unsupported,
            )

    def test_explicit_run_spec_mode_cannot_be_silently_overridden(self):
        spec = copy.deepcopy(self.spec); spec["mode"] = "parallel"
        with self.assertRaisesRegex(PlanningError, "does not match"):
            self.plan(spec, doctor=report(spec), mode="sequential")


if __name__ == "__main__":
    unittest.main()
