from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "plugin-compass"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from plugin_compass.skill_models import SkillRecord  # noqa: E402


def skill(
    name: str,
    description: str,
    *,
    source_type: str = "standalone-project",
    source_identity: str = "project:fixture",
    trust_status: str = "not_assessed",
    metadata_status: str = "complete",
    readiness_status: str = "files_present",
) -> SkillRecord:
    relative_path = f"{source_identity.replace(':', '-')}/{name}/SKILL.md"
    return SkillRecord.create(
        name=name,
        description=description,
        path=f"C:/fixture/{relative_path}",
        relative_path=relative_path,
        source_type=source_type,
        source_identity=source_identity,
        trust_status=trust_status,
        metadata_status=metadata_status,
        readiness_status=readiness_status,
    )


def decision_module():
    module_name = "plugin_compass.skill_decision"
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        raise AssertionError("source-neutral skill decision owner is not implemented")
    return importlib.import_module(module_name)


class SkillDecisionTests(unittest.TestCase):
    def test_exact_minimal_cover_beats_greedy_wide_left_right_counterexample(self) -> None:
        module = decision_module()
        wide = skill("wide", "alpha beta gamma delta", source_identity="project:wide")
        left = skill("left", "alpha beta epsilon", source_identity="project:left")
        right = skill("right", "gamma delta zeta", source_identity="project:right")

        result = module.build_skill_decision(
            [wide, left, right], "alpha beta gamma delta epsilon zeta",
        )

        self.assertEqual(
            {left.qualified_identity, right.qualified_identity},
            {item.qualified_identity for item in result.recommendations},
        )
        self.assertEqual(2, len(result.recommendations))

    def test_exact_cover_candidate_bound_requires_explicit_selection(self) -> None:
        module = decision_module()
        self.assertTrue(hasattr(module, "EXACT_COVER_MAX_CANDIDATES"))
        records = [
            skill("first", "alpha beta", source_identity="project:first"),
            skill("second", "gamma delta", source_identity="project:second"),
            skill("third", "epsilon zeta", source_identity="project:third"),
        ]
        with patch.object(module, "EXACT_COVER_MAX_CANDIDATES", 2):
            with self.assertRaisesRegex(ValueError, "--select-skill"):
                module.build_skill_decision(
                    records, "alpha beta gamma delta epsilon zeta",
                )

    def test_exact_cover_search_state_bound_requires_explicit_selection(self) -> None:
        module = decision_module()
        self.assertTrue(hasattr(module, "EXACT_COVER_MAX_SEARCH_STATES"))
        records = [
            skill("first", "alpha beta", source_identity="project:first"),
            skill("second", "gamma delta", source_identity="project:second"),
        ]
        with patch.object(module, "EXACT_COVER_MAX_SEARCH_STATES", 1):
            with self.assertRaisesRegex(ValueError, "--select-skill"):
                module.build_skill_decision(records, "alpha beta gamma delta")

    def test_ambiguity_serialization_is_stable_across_python_hash_seeds(self) -> None:
        script = f"""
import json
import sys
sys.path.insert(0, {str(PLUGIN_ROOT)!r})
from plugin_compass.skill_decision import build_skill_decision
from plugin_compass.skill_models import SkillRecord
records = {{
    SkillRecord.create(
        name='review', description='Review changes.', path='C:/Alpha/review/SKILL.md',
        relative_path='review/SKILL.md', source_type='standalone-project',
        source_identity='Alpha', metadata_status='complete', readiness_status='files_present',
    ),
    SkillRecord.create(
        name='review', description='Review changes.', path='C:/alpha/review/SKILL.md',
        relative_path='review/SKILL.md', source_type='standalone-project',
        source_identity='alpha', metadata_status='complete', readiness_status='files_present',
    ),
}}
result = build_skill_decision(records, 'review changes', requested_skills=['review'])
print(json.dumps(result.to_dict()['ambiguities'][0]['candidates']))
"""
        outputs = []
        for seed in ("1", "2", "7", "19"):
            environment = dict(os.environ)
            environment["PYTHONHASHSEED"] = seed
            completed = subprocess.run(
                [sys.executable, "-c", script],
                check=False,
                capture_output=True,
                text=True,
                shell=False,
                env=environment,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            outputs.append(json.loads(completed.stdout))
        expected = sorted(outputs[0], key=lambda value: (value.casefold(), value))
        self.assertTrue(all(output == expected for output in outputs), outputs)

    def test_combined_minimal_coverage_and_ranking_are_input_order_independent(self) -> None:
        module = decision_module()
        packaged = skill(
            "review",
            "Review Python code.",
            source_type="plugin",
            source_identity="reviewer@fixture",
        )
        standalone = skill(
            "python-security-review",
            "Review Python security code.",
            source_identity="project:security",
            trust_status="trusted",
        )

        forward = module.build_skill_decision(
            [packaged, standalone], "review Python security code",
        )
        reverse = module.build_skill_decision(
            [standalone, packaged], "review Python security code",
        )

        self.assertEqual(forward.to_dict(), reverse.to_dict())
        self.assertEqual(
            [standalone.qualified_identity],
            [item.qualified_identity for item in forward.recommendations],
        )

    def test_untrusted_unready_and_malformed_skills_are_not_recommended(self) -> None:
        module = decision_module()
        records = [
            skill("unsafe", "Unsafe review.", trust_status="untrusted"),
            skill("missing", "Missing review.", readiness_status="missing_files"),
            skill("broken", "Broken review.", metadata_status="malformed"),
        ]

        result = module.build_skill_decision(records, "unsafe missing broken review")

        self.assertEqual((), result.recommendations)
        assessments = {item.name: item for item in result.assessments}
        self.assertIn("skill trust status is untrusted", assessments["unsafe"].hard_gates)
        self.assertIn("skill readiness is missing_files", assessments["missing"].hard_gates)
        self.assertIn("skill metadata is malformed", assessments["broken"].hard_gates)

    def test_incomplete_frontmatter_records_are_degraded_and_never_selectable(self) -> None:
        from plugin_compass.adapters.standalone import (
            ConfiguredSkillRoot,
            discover_standalone_skills,
        )

        module = decision_module()
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F4 ") as temporary:
            root = Path(temporary) / "Incomplete Skills"
            name_only = root / "name-only" / "SKILL.md"
            description_only = root / "description-only" / "SKILL.md"
            name_only.parent.mkdir(parents=True)
            description_only.parent.mkdir(parents=True)
            name_only.write_text(
                "---\nname: name-only\n---\n",
                encoding="utf-8",
            )
            description_only.write_text(
                "---\ndescription: Description-only review capability.\n---\n",
                encoding="utf-8",
            )

            discovery = discover_standalone_skills([
                ConfiguredSkillRoot(
                    root, "standalone-project", "project:incomplete",
                )
            ])

        automatic = module.build_skill_decision(
            discovery.skills, "name only description review capability",
        )
        self.assertEqual((), automatic.recommendations)
        for record in discovery.skills:
            with self.subTest(selection=record.qualified_identity):
                exact = module.build_skill_decision(
                    discovery.skills,
                    "unrelated task",
                    requested_skills=[record.qualified_identity],
                )
                self.assertEqual((), exact.recommendations)

        self.assertEqual("degraded", discovery.status)
        self.assertEqual(
            ["partial", "partial"],
            sorted(record.metadata_status for record in discovery.skills),
        )
        self.assertEqual(
            ["skill-metadata-incomplete", "skill-metadata-incomplete"],
            sorted(item.code for item in discovery.diagnostics),
        )
        for assessment in automatic.assessments:
            self.assertIn("skill metadata is partial", assessment.hard_gates)

    def test_ambiguous_bare_name_selects_none_and_names_sorted_candidates(self) -> None:
        module = decision_module()
        first = skill("review", "Review changes.", source_identity="project:zeta")
        second = skill(
            "Review", "Review changes.", source_type="standalone-user",
            source_identity="user:alpha",
        )

        result = module.build_skill_decision(
            [first, second], "review changes", requested_skills=["review"],
        )

        self.assertEqual((), result.recommendations)
        self.assertEqual(1, len(result.ambiguities))
        self.assertEqual(
            sorted([first.qualified_identity, second.qualified_identity], key=str.casefold),
            list(result.ambiguities[0].candidates),
        )

    def test_exact_qualified_identity_resolves_duplicate_name(self) -> None:
        module = decision_module()
        first = skill("review", "Review changes.", source_identity="project:zeta")
        second = skill(
            "review", "Review changes.", source_type="standalone-user",
            source_identity="user:alpha",
        )

        result = module.build_skill_decision(
            [first, second],
            "unrelated task",
            requested_skills=[second.qualified_identity],
        )

        self.assertEqual((), result.ambiguities)
        self.assertEqual(
            [second.qualified_identity],
            [item.qualified_identity for item in result.recommendations],
        )

    def test_ineligible_exact_identity_is_not_substituted_by_same_name_sibling(self) -> None:
        module = decision_module()
        blocked = skill(
            "review", "Review changes.", source_identity="project:blocked",
            trust_status="untrusted",
        )
        eligible = skill(
            "review", "Review changes.", source_type="standalone-user",
            source_identity="user:eligible",
        )

        result = module.build_skill_decision(
            [blocked, eligible],
            "review changes",
            requested_skills=[blocked.qualified_identity],
        )

        self.assertEqual((), result.recommendations)
        blocked_assessment = next(
            item for item in result.assessments if item.skill_id == blocked.skill_id
        )
        self.assertEqual("Blocked or untrusted", blocked_assessment.classification)

    def test_unknown_selection_fails_closed(self) -> None:
        module = decision_module()
        with self.assertRaisesRegex(ValueError, "unknown skill selection"):
            module.build_skill_decision(
                [skill("review", "Review changes.")],
                "review changes",
                requested_skills=["missing"],
            )

    def test_standalone_exact_selection_suppresses_legacy_plugin_coverage(self) -> None:
        from plugin_compass.adapters.codex import discover_plugins
        from plugin_compass.decision import build_recommendation_plan
        from plugin_compass.metadata import enrich_plugins
        from plugin_compass.repository import inspect_repository

        fixtures = REPOSITORY_ROOT / "tests" / "fixtures"
        plugins = enrich_plugins(
            discover_plugins(inventory_file=fixtures / "codex_plugins.json")
        )
        self.assertIn(
            "standalone_skills",
            inspect.signature(build_recommendation_plan).parameters,
        )
        standalone = skill(
            "plugin-selection-specialist",
            "Choose the smallest evidence-backed Codex plugin and skill set.",
            source_identity="project:preferred",
            trust_status="trusted",
        )

        plan = build_recommendation_plan(
            plugins,
            inspect_repository(PLUGIN_ROOT),
            "choose the smallest evidence-backed Codex plugin and skill set",
            standalone_skills=[standalone],
            requested_skills=[standalone.qualified_identity],
        )

        self.assertEqual("plugin-compass.plan.v5", plan.schema_version)
        self.assertEqual(
            [standalone.qualified_identity],
            [item.qualified_identity for item in plan.skill_recommendations],
        )
        self.assertNotIn(
            "plugin-selection-specialist",
            {
                name
                for recommendation in plan.recommendations
                for name in recommendation.capability_names
            },
        )

    def test_gated_exact_standalone_request_suppresses_legacy_plugin_coverage(self) -> None:
        from plugin_compass.adapters.codex import discover_plugins
        from plugin_compass.decision import build_recommendation_plan
        from plugin_compass.metadata import enrich_plugins
        from plugin_compass.repository import inspect_repository

        fixtures = REPOSITORY_ROOT / "tests" / "fixtures"
        plugins = enrich_plugins(
            discover_plugins(inventory_file=fixtures / "codex_plugins.json")
        )
        untrusted = skill(
            "plugin-selection-specialist",
            "Choose the smallest evidence-backed Codex plugin and skill set.",
            source_identity="project:untrusted",
            trust_status="untrusted",
        )

        plan = build_recommendation_plan(
            plugins,
            inspect_repository(PLUGIN_ROOT),
            "choose the smallest evidence-backed Codex plugin and skill set",
            standalone_skills=[untrusted],
            requested_skills=[untrusted.qualified_identity],
        )

        self.assertEqual((), plan.skill_recommendations)
        requested_assessment = next(
            item for item in plan.skill_assessments if item.skill_id == untrusted.skill_id
        )
        self.assertEqual("Blocked or untrusted", requested_assessment.classification)
        self.assertNotIn(
            "plugin-selection-specialist",
            {
                name
                for recommendation in plan.recommendations
                for name in recommendation.capability_names
            },
        )

    def test_unresolved_standalone_collision_cannot_leak_through_plugin_recommendation(self) -> None:
        from plugin_compass.adapters.codex import discover_plugins
        from plugin_compass.decision import build_recommendation_plan
        from plugin_compass.metadata import enrich_plugins
        from plugin_compass.repository import inspect_repository

        fixtures = REPOSITORY_ROOT / "tests" / "fixtures"
        plugins = enrich_plugins(
            discover_plugins(inventory_file=fixtures / "codex_plugins.json")
        )
        self.assertIn(
            "standalone_skills",
            inspect.signature(build_recommendation_plan).parameters,
        )
        standalone = skill(
            "plugin-selection-specialist",
            "Choose the smallest evidence-backed Codex plugin and skill set.",
            source_identity="project:collision",
        )

        plan = build_recommendation_plan(
            plugins,
            inspect_repository(PLUGIN_ROOT),
            "choose the smallest evidence-backed Codex plugin and skill set",
            standalone_skills=[standalone],
        )

        self.assertEqual(1, len(plan.skill_ambiguities))
        self.assertEqual((), plan.skill_recommendations)
        self.assertNotIn(
            "plugin-selection-specialist",
            {
                name
                for recommendation in plan.recommendations
                for name in recommendation.capability_names
            },
        )

    def test_differently_named_standalone_winner_suppresses_covered_plugin_skill(self) -> None:
        from plugin_compass.adapters.codex import discover_plugins
        from plugin_compass.decision import build_recommendation_plan
        from plugin_compass.metadata import enrich_plugins
        from plugin_compass.repository import inspect_repository

        fixtures = REPOSITORY_ROOT / "tests" / "fixtures"
        plugins = enrich_plugins(
            discover_plugins(inventory_file=fixtures / "codex_plugins.json")
        )
        standalone = skill(
            "capability-map",
            "Choose the smallest evidence-backed Codex plugin and skill set for a task.",
            source_identity="project:preferred-map",
            trust_status="trusted",
        )

        plan = build_recommendation_plan(
            plugins,
            inspect_repository(PLUGIN_ROOT),
            "choose the smallest evidence-backed Codex plugin and skill set",
            standalone_skills=[standalone],
        )

        self.assertEqual(
            [standalone.qualified_identity],
            [item.qualified_identity for item in plan.skill_recommendations],
        )
        self.assertNotIn(
            "plugin-selection-specialist",
            {
                name
                for recommendation in plan.recommendations
                for name in recommendation.capability_names
            },
        )


if __name__ == "__main__":
    unittest.main()
