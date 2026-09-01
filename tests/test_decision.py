from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "plugin-compass"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from plugin_compass.adapters.codex import discover_plugins  # noqa: E402
from plugin_compass.adapters.drskill import load_report as load_drskill_report  # noqa: E402
from plugin_compass.adapters.hol import load_report as load_hol_report  # noqa: E402
from plugin_compass.decision import build_recommendation_plan, tokenize  # noqa: E402
from plugin_compass.metadata import enrich_plugins  # noqa: E402
from plugin_compass.rendering import render_json, render_markdown  # noqa: E402
from plugin_compass.repository import inspect_repository  # noqa: E402


TASK = "choose the smallest evidence-backed Codex plugin and skill set"


def fixture_inputs():
    plugins = enrich_plugins(
        discover_plugins(inventory_file=FIXTURES / "codex_plugins.json")
    )
    drskill_findings, drskill_evidence = load_drskill_report(
        FIXTURES / "drskill_findings.jsonl"
    )
    clean_findings, clean_evidence = load_hol_report(FIXTURES / "hol_clean.json")
    blocked_findings, blocked_evidence = load_hol_report(FIXTURES / "hol_blocked.json")
    return (
        plugins,
        drskill_findings + clean_findings + blocked_findings,
        drskill_evidence + clean_evidence + blocked_evidence,
    )


class DecisionTests(unittest.TestCase):
    def test_generic_routing_words_do_not_create_capability_matches(self) -> None:
        self.assertEqual((), tokenize("choose before new"))

    def test_end_to_end_decisions_apply_hard_gates_and_overlap(self) -> None:
        plugins, findings, evidence = fixture_inputs()
        repository = inspect_repository(PLUGIN_ROOT)
        plan = build_recommendation_plan(
            plugins,
            repository,
            TASK,
            findings=findings,
            external_evidence=evidence,
            host_platform="windows",
        )
        assessments = {item.plugin_id: item for item in plan.assessments}

        self.assertEqual("Use now", assessments["specialist@fixture"].classification)
        self.assertEqual(
            "Blocked or untrusted", assessments["broad-suite@fixture"].classification
        )
        self.assertIn(
            "target-specific HOL report has unresolved high or critical findings",
            assessments["broad-suite@fixture"].hard_gates,
        )
        self.assertEqual(
            "Unknown or insufficient evidence",
            assessments["risky-exec@fixture"].classification,
        )
        self.assertEqual(
            "Blocked or untrusted", assessments["memory-plugin@fixture"].classification
        )
        self.assertEqual(
            "Irrelevant to this project",
            assessments["disabled-docs@fixture"].classification,
        )

        self.assertEqual(1, len(plan.overlap_groups))
        self.assertEqual("specialist@fixture", plan.overlap_groups[0].winner)
        self.assertEqual(["specialist@fixture"], [item.plugin_id for item in plan.recommendations])
        self.assertIn("plugin-selection-specialist", plan.generated_prompt)

    def test_drskill_static_false_positive_is_visible_not_dismissed(self) -> None:
        plugins, findings, evidence = fixture_inputs()
        plan = build_recommendation_plan(
            plugins,
            inspect_repository(PLUGIN_ROOT),
            TASK,
            findings=findings,
            external_evidence=evidence,
            host_platform="windows",
        )
        states = {
            finding.check_id: triage.state
            for finding, triage in (
                (finding, next(item for item in plan.triage if item.finding_id == finding.finding_id))
                for finding in plan.findings
            )
        }
        self.assertEqual("suspected-false-positive", states["injection-credential-read"])
        self.assertEqual("credible", states["FIXTURE_HIGH_RISK"])

    def test_hol_trust_requires_the_exact_target_root(self) -> None:
        plugins, findings, evidence = fixture_inputs()
        mismatched_evidence = tuple(
            replace(item, target_root="C:\\different\\specialist")
            if item.kind == "hol-report" and item.subject == "specialist"
            else item
            for item in evidence
        )
        plan = build_recommendation_plan(
            plugins,
            inspect_repository(PLUGIN_ROOT),
            TASK,
            findings=findings,
            external_evidence=mismatched_evidence,
            host_platform="windows",
        )
        specialist = next(
            item for item in plan.assessments if item.plugin_id == "specialist@fixture"
        )
        self.assertEqual("unknown", specialist.dimensions["trust_and_security"])

    def test_windows_and_wsl_roots_match_for_the_same_target(self) -> None:
        plugins, findings, evidence = fixture_inputs()
        specialist = next(item for item in plugins if item.plugin_id == "specialist@fixture")
        windows_root = specialist.source_root or ""
        drive, suffix = windows_root.split(":", 1)
        wsl_root = f"/mnt/{drive.casefold()}{suffix.replace(chr(92), '/')}"
        cross_platform_evidence = tuple(
            replace(item, target_root=wsl_root)
            if item.kind == "hol-report" and item.subject == "specialist"
            else item
            for item in evidence
        )
        plan = build_recommendation_plan(
            plugins,
            inspect_repository(PLUGIN_ROOT),
            TASK,
            findings=findings,
            external_evidence=cross_platform_evidence,
            host_platform="windows",
        )
        assessment = next(
            item for item in plan.assessments if item.plugin_id == "specialist@fixture"
        )
        self.assertEqual("reviewed", assessment.dimensions["trust_and_security"])

    def test_explicit_repository_prohibition_is_a_hard_gate(self) -> None:
        plugins, findings, evidence = fixture_inputs()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "AGENTS.md").write_text(
                "Do not use the plugin specialist.", encoding="utf-8"
            )
            plan = build_recommendation_plan(
                plugins,
                inspect_repository(root),
                TASK,
                findings=findings,
                external_evidence=evidence,
                host_platform="windows",
            )
        specialist = next(
            item for item in plan.assessments if item.plugin_id == "specialist@fixture"
        )
        self.assertEqual("Blocked or untrusted", specialist.classification)
        self.assertIn("repository policy prohibits this plugin", specialist.hard_gates)

    def test_empty_repository_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context = inspect_repository(Path(temporary))
        self.assertTrue(context.exists)
        self.assertTrue(context.empty)
        self.assertFalse(context.has_authority_system)

    def test_exact_optimizer_route_does_not_unblock_its_parent_plugin(self) -> None:
        plugins, findings, evidence = fixture_inputs()
        plan = build_recommendation_plan(
            plugins,
            inspect_repository(PLUGIN_ROOT),
            "optimize token cost and budget",
            findings=findings,
            external_evidence=evidence,
            host_platform="windows",
            optimization_goal="cost",
        )
        assessment = next(
            item
            for item in plan.assessments
            if item.plugin_id == "claude-code-skills@fixture"
        )

        self.assertEqual("Blocked or untrusted", assessment.classification)
        self.assertNotIn(
            "claude-code-skills@fixture",
            [item.plugin_id for item in plan.recommendations],
        )
        self.assertEqual(1, len(plan.invocation_routes))
        route = plan.invocation_routes[0]
        self.assertEqual(
            "claude-code-skills:llm-cost-optimizer",
            route.capability_name,
        )
        self.assertEqual("Codex", route.invoker)
        self.assertTrue(route.evidence_refs)
        self.assertLessEqual(len(route.evidence_refs), 3)
        self.assertEqual((), plan.recommendations)
        self.assertIn("Codex invokes it", plan.generated_prompt)
        self.assertIsNone(plan.scheduling_guidance)

    def test_speed_and_negated_cost_text_do_not_invoke_the_cost_optimizer(self) -> None:
        plugins, findings, evidence = fixture_inputs()
        plan = build_recommendation_plan(
            plugins, inspect_repository(PLUGIN_ROOT),
            "Choose model reasoning effort for subagents: speed and correctness, not cost",
            findings=findings, external_evidence=evidence, host_platform="windows",
        )
        self.assertEqual("speed", plan.optimization_goal)
        self.assertEqual((), plan.invocation_routes)
        guidance = plan.scheduling_guidance
        self.assertIsNotNone(guidance)
        self.assertEqual("fastest_verified_completion", guidance.objective)
        self.assertEqual("advisory", guidance.enforcement)
        self.assertEqual({"low", "medium", "high", "above_high"}, set(guidance.effort_bands))
        self.assertTrue({"acceptance_checks", "chosen_effort", "escalation_trigger"} <= set(guidance.decision_fields))
        self.assertNotIn("llm-cost-optimizer", plan.generated_prompt)

    def test_speed_guidance_does_not_depend_on_a_cost_plugin(self) -> None:
        plan = build_recommendation_plan(
            (), inspect_repository(PLUGIN_ROOT), "choose reasoning effort for each agent",
        )
        self.assertIsNotNone(plan.scheduling_guidance)
        self.assertEqual((), plan.invocation_routes)

    def test_cost_skill_cannot_leak_through_general_recommendations_in_speed_mode(self) -> None:
        plugins, _, _ = fixture_inputs()
        cost_plugin = next(item for item in plugins if item.name == "claude-code-skills")
        repository = replace(
            inspect_repository(PLUGIN_ROOT), has_authority_system=False,
            prohibited_plugins=(),
        )
        plan = build_recommendation_plan(
            (cost_plugin,), repository, "workload speed not cost", host_platform="windows",
        )
        self.assertEqual("Use now", plan.assessments[0].classification)
        self.assertEqual((), plan.recommendations)
        self.assertEqual((), plan.invocation_routes)
        self.assertIsNotNone(plan.scheduling_guidance)

    def test_scheduling_outputs_remain_deterministic_under_input_reordering(self) -> None:
        plugins, findings, evidence = fixture_inputs()
        repository = inspect_repository(PLUGIN_ROOT)
        for goal in ("speed", "cost"):
            with self.subTest(goal=goal):
                options = dict(host_platform="windows", optimization_goal=goal)
                forward = build_recommendation_plan(
                    plugins, repository, "choose reasoning effort for subagents",
                    findings=findings, external_evidence=evidence, **options,
                )
                reverse = build_recommendation_plan(
                    reversed(plugins), repository, "choose reasoning effort for subagents",
                    findings=reversed(findings), external_evidence=reversed(evidence), **options,
                )
                self.assertEqual(render_json(forward), render_json(reverse))
                self.assertEqual(render_markdown(forward), render_markdown(reverse))
                self.assertEqual(forward.generated_prompt, reverse.generated_prompt)

    def test_disabled_cost_plugin_is_not_routed(self) -> None:
        plugins, _, _ = fixture_inputs()
        plan = build_recommendation_plan(
            [replace(item, enabled=False) for item in plugins],
            inspect_repository(PLUGIN_ROOT), "optimize cost", optimization_goal="cost",
        )
        self.assertEqual((), plan.invocation_routes)

    def test_invalid_optimization_goal_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported optimization goal"):
            build_recommendation_plan((), inspect_repository(PLUGIN_ROOT), "task", optimization_goal="unknown")

    def test_optimizer_route_is_absent_for_unrelated_tasks(self) -> None:
        plugins, findings, evidence = fixture_inputs()
        plan = build_recommendation_plan(
            plugins,
            inspect_repository(PLUGIN_ROOT),
            "write repository documentation",
            findings=findings,
            external_evidence=evidence,
            host_platform="windows",
        )

        self.assertEqual((), plan.invocation_routes)
        self.assertIsNone(plan.scheduling_guidance)

    def test_rendering_is_stable_when_finding_input_is_reordered(self) -> None:
        plugins, findings, evidence = fixture_inputs()
        repository = inspect_repository(PLUGIN_ROOT)
        forward = build_recommendation_plan(
            plugins,
            repository,
            TASK,
            findings=findings,
            external_evidence=evidence,
            host_platform="windows",
        )
        reverse = build_recommendation_plan(
            tuple(reversed(plugins)),
            repository,
            TASK,
            findings=tuple(reversed(findings)),
            external_evidence=tuple(reversed(evidence)),
            host_platform="windows",
        )
        self.assertEqual(render_json(forward), render_json(reverse))
        self.assertEqual(render_markdown(forward), render_markdown(reverse))
        self.assertEqual(forward.generated_prompt, reverse.generated_prompt)


if __name__ == "__main__":
    unittest.main()
