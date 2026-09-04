from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from dataclasses import fields, replace
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "plugin-compass"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from plugin_compass.adapters.codex import discover_plugins  # noqa: E402
from plugin_compass.adapters.standalone import (  # noqa: E402
    DiscoveryDiagnostic,
    StandaloneDiscoveryResult,
)
from plugin_compass.decision import build_recommendation_plan  # noqa: E402
from plugin_compass.metadata import enrich_plugins  # noqa: E402
from plugin_compass.repository import inspect_repository  # noqa: E402
from plugin_compass.skill_models import (  # noqa: E402
    SKILL_SOURCE_TYPES,
    SkillAssessment,
    SkillRecord,
)
from plugin_compass.skill_decision import build_skill_decision  # noqa: E402
import plugin_compass.skill_models as skill_models_module  # noqa: E402


class SourceNeutralSkillModelTests(unittest.TestCase):
    def test_standalone_discovery_summary_is_closed_canonical_and_immutable(self) -> None:
        self.assertTrue(
            hasattr(skill_models_module, "StandaloneDiscoverySummary"),
            "typed standalone discovery summary is not implemented",
        )
        Summary = skill_models_module.StandaloneDiscoverySummary
        diagnostics = [
            {
                "code": "alpha",
                "source_type": "standalone-user",
                "source_identity": "user:z",
                "path": "C:/z",
                "detail": "zeta",
            },
            {
                "code": "Alpha",
                "source_type": "standalone-user",
                "source_identity": "user:a",
                "path": "C:/a",
                "detail": "alpha",
            },
        ]
        forward = Summary.create("degraded", diagnostics)
        reverse = Summary.create("degraded", reversed(diagnostics))

        self.assertEqual(forward, reverse)
        self.assertEqual(
            ["Alpha", "alpha"],
            [item["code"] for item in forward.to_dict()["diagnostics"]],
        )
        diagnostics[0]["detail"] = "mutated input"
        serialized = forward.to_dict()
        serialized["diagnostics"][0]["detail"] = "mutated output"
        self.assertEqual("alpha", forward.to_dict()["diagnostics"][0]["detail"])
        with self.assertRaisesRegex(ValueError, "status"):
            Summary.create("bogus", ())
        with self.assertRaisesRegex(ValueError, "diagnostic fields"):
            Summary.create("degraded", [{**diagnostics[0], "extra": "open"}])

    def test_plan_accepts_discovery_result_and_stores_typed_summary(self) -> None:
        self.assertTrue(hasattr(skill_models_module, "StandaloneDiscoverySummary"))
        result = StandaloneDiscoveryResult(
            skills=(),
            diagnostics=(DiscoveryDiagnostic(
                code="root-missing",
                source_type="standalone-project",
                source_identity="project:fixture",
                path="C:/missing",
                detail="Missing.",
            ),),
        )

        plan = build_recommendation_plan(
            (), inspect_repository(PLUGIN_ROOT), "review", standalone_discovery=result,
        )

        self.assertIsInstance(
            plan.standalone_discovery,
            skill_models_module.StandaloneDiscoverySummary,
        )
        self.assertEqual("degraded", plan.standalone_discovery.status)
        payload = plan.to_dict()
        payload["standalone_discovery"]["diagnostics"][0]["detail"] = "mutated"
        self.assertEqual(
            "Missing.",
            plan.to_dict()["standalone_discovery"]["diagnostics"][0]["detail"],
        )
        with self.assertRaisesRegex(TypeError, "StandaloneDiscoverySummary"):
            replace(
                plan,
                standalone_discovery={"status": "complete", "diagnostics": []},
            )
        with self.assertRaisesRegex(TypeError, "StandaloneDiscoverySummary"):
            build_recommendation_plan(
                (), inspect_repository(PLUGIN_ROOT), "review",
                standalone_discovery={"status": "complete", "diagnostics": []},
            )

    def test_skill_record_arrays_are_stable_across_python_hash_seeds(self) -> None:
        script = f"""
import json
import sys
sys.path.insert(0, {str(PLUGIN_ROOT)!r})
from plugin_compass.skill_models import SkillRecord
record = SkillRecord.create(
    name='review', description='Review changes.', path='C:/skills/review/SKILL.md',
    relative_path='review/SKILL.md', source_type='standalone-user',
    source_identity='user:fixture', metadata_status='complete',
    readiness_status='files_present', readiness_references={{'alpha', 'Alpha'}},
    evidence_refs={{'alpha', 'Alpha'}},
)
payload = record.to_dict()
print(json.dumps({{
    'readiness': payload['readiness']['references'],
    'evidence': payload['evidence_refs'],
}}))
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
        expected = {"readiness": ["Alpha", "alpha"], "evidence": ["Alpha", "alpha"]}
        self.assertTrue(all(output == expected for output in outputs), outputs)

    def test_all_frozen_source_types_are_supported_and_other_values_are_rejected(self) -> None:
        self.assertEqual(
            {
                "plugin",
                "standalone-user",
                "standalone-project",
                "system",
                "session-only",
            },
            set(SKILL_SOURCE_TYPES),
        )
        for source_type in SKILL_SOURCE_TYPES:
            with self.subTest(source_type=source_type):
                record = SkillRecord.create(
                    name="review",
                    description="Review a change.",
                    path="C:/machine-specific/root/review/SKILL.md",
                    source_type=source_type,
                    source_identity="logical-source",
                    relative_path="review/SKILL.md",
                )
                self.assertEqual(source_type, record.source_type)
        with self.assertRaisesRegex(ValueError, "source type"):
            SkillRecord.create(
                name="review",
                description="Review a change.",
                path="review/SKILL.md",
                source_type="invented",
                source_identity="logical-source",
                relative_path="review/SKILL.md",
            )

    def test_identity_uses_logical_source_and_normalized_relative_path_only(self) -> None:
        common = {
            "name": "review",
            "description": "Review a change.",
            "source_type": "plugin",
            "source_identity": "reviewer@marketplace",
        }
        first = SkillRecord.create(
            **common,
            path="C:/one/root/skills/review/SKILL.md",
            relative_path="skills\\review\\.\\SKILL.md",
        )
        second = SkillRecord.create(
            **common,
            path="D:/different/root/skills/review/SKILL.md",
            relative_path="skills/review/SKILL.md",
        )
        other_source = SkillRecord.create(
            **{**common, "source_identity": "reviewer@other"},
            path=second.path,
            relative_path=second.relative_path,
        )
        self.assertEqual(first.skill_id, second.skill_id)
        self.assertEqual(first.qualified_identity, second.qualified_identity)
        self.assertEqual("skills/review/SKILL.md", first.relative_path)
        self.assertNotEqual(first.skill_id, other_source.skill_id)
        with self.assertRaisesRegex(ValueError, "relative path"):
            SkillRecord.create(
                **common,
                path="C:/outside/SKILL.md",
                relative_path="../outside/SKILL.md",
            )

    def test_qualified_identity_disambiguates_duplicate_names_without_absolute_roots(self) -> None:
        common = {
            "name": "review",
            "description": "Review a change.",
            "source_type": "standalone-project",
        }
        project_skill = SkillRecord.create(
            **common,
            path="C:/checkout-a/.codex/skills/review/SKILL.md",
            source_identity="project:alpha",
            relative_path="review/SKILL.md",
        )
        relocated_project_skill = SkillRecord.create(
            **common,
            path="D:/checkout-b/.codex/skills/review/SKILL.md",
            source_identity="project:alpha",
            relative_path="review/SKILL.md",
        )
        sibling_skill = SkillRecord.create(
            **common,
            path="C:/checkout-a/.codex/skills/other/SKILL.md",
            source_identity="project:alpha",
            relative_path="other/SKILL.md",
        )
        user_skill = SkillRecord.create(
            **{**common, "source_type": "standalone-user"},
            path="C:/user/.codex/skills/review/SKILL.md",
            source_identity="codex-user-skills",
            relative_path="review/SKILL.md",
        )

        self.assertEqual(project_skill.qualified_identity, relocated_project_skill.qualified_identity)
        self.assertNotEqual(project_skill.qualified_identity, sibling_skill.qualified_identity)
        self.assertNotEqual(project_skill.qualified_identity, user_skill.qualified_identity)
        self.assertNotIn("checkout-a", project_skill.qualified_identity)
        self.assertEqual(project_skill.qualified_identity, project_skill.to_dict()["qualified_identity"])

    def test_serialization_preserves_provenance_trust_readiness_and_evidence(self) -> None:
        record = SkillRecord.create(
            name="review",
            description="Review a change.",
            path="C:/root/skills/review/SKILL.md",
            source_type="standalone-user",
            source_identity="codex-user-skills",
            relative_path="review/SKILL.md",
            trust_status="trusted",
            metadata_status="complete",
            readiness_status="files_present",
            readiness_root="C:/root",
            readiness_references=("scripts/review.py",),
            evidence_refs=("ev-z", "ev-a"),
        )
        payload = record.to_dict()
        self.assertEqual("review", payload["name"])
        self.assertEqual("Review a change.", payload["description"])
        self.assertEqual("C:/root/skills/review/SKILL.md", payload["path"])
        self.assertEqual("standalone-user", payload["source"]["type"])
        self.assertEqual("codex-user-skills", payload["source"]["identity"])
        self.assertEqual("trusted", payload["trust_status"])
        self.assertEqual("complete", payload["metadata_status"])
        self.assertEqual("files_present", payload["readiness"]["status"])
        self.assertEqual(["ev-a", "ev-z"], payload["evidence_refs"])

    def test_skill_trust_is_closed_in_models_decisions_and_public_schemas(self) -> None:
        valid = SkillRecord.create(
            name="review",
            description="Review changes.",
            path="C:/skills/review/SKILL.md",
            relative_path="review/SKILL.md",
            source_type="standalone-user",
            source_identity="user:fixture",
            trust_status="trusted",
            metadata_status="complete",
            readiness_status="files_present",
        )
        bypassed = object.__new__(SkillRecord)
        for field in fields(SkillRecord):
            object.__setattr__(bypassed, field.name, getattr(valid, field.name))
        object.__setattr__(bypassed, "trust_status", "untrusted ")

        decision = build_skill_decision([bypassed], "review changes")
        self.assertEqual((), decision.recommendations)
        self.assertIn(
            "skill trust status is untrusted ",
            decision.assessments[0].hard_gates,
        )

        allowed = {
            "not_assessed", "trusted", "unknown", "untrusted", "blocked", "rejected",
        }
        for trust_status in allowed:
            with self.subTest(valid=trust_status):
                record = SkillRecord.create(
                    name="review",
                    description="Review changes.",
                    path="C:/skills/review/SKILL.md",
                    relative_path="review/SKILL.md",
                    source_type="standalone-user",
                    source_identity="user:fixture",
                    trust_status=trust_status,
                )
                self.assertEqual(trust_status, record.trust_status)
                if trust_status in {"unknown", "untrusted", "blocked", "rejected"}:
                    decision = build_skill_decision([record], "review changes")
                    self.assertEqual((), decision.recommendations)
        for trust_status in ("untrusted ", "UNTRUSTED", "", "invented"):
            with self.subTest(invalid=trust_status):
                with self.assertRaisesRegex(ValueError, "trust status"):
                    SkillRecord.create(
                        name="review",
                        description="Review changes.",
                        path="C:/skills/review/SKILL.md",
                        relative_path="review/SKILL.md",
                        source_type="standalone-user",
                        source_identity="user:fixture",
                        trust_status=trust_status,
                    )

        recommendation_schema = json.loads(
            (PLUGIN_ROOT / "schemas" / "recommendation-plan.schema.json").read_text(
                encoding="utf-8"
            )
        )
        expected_enum = sorted(allowed)
        for definition in ("skill", "skillAssessment", "skillRecommendation"):
            with self.subTest(schema_definition=definition):
                self.assertEqual(
                    expected_enum,
                    sorted(
                        recommendation_schema["$defs"][definition]["properties"]
                        ["trust_status"]["enum"]
                    ),
                )
        inventory_schema = json.loads(
            (PLUGIN_ROOT / "schemas" / "inventory.schema.json").read_text(
                encoding="utf-8"
            )
        )
        prompt_schema = json.loads(
            (PLUGIN_ROOT / "schemas" / "prompt.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            "recommendation-plan.schema.json#/$defs/skill",
            inventory_schema["properties"]["skills"]["items"]["$ref"],
        )
        self.assertEqual(
            "recommendation-plan.schema.json#/$defs/skill",
            prompt_schema["properties"]["skills"]["items"]["$ref"],
        )

    def test_skill_assessment_dimensions_are_exactly_the_closed_public_shape(self) -> None:
        record = SkillRecord.create(
            name="review",
            description="Review changes.",
            path="C:/skills/review/SKILL.md",
            relative_path="review/SKILL.md",
            source_type="standalone-user",
            source_identity="user:fixture",
        )
        expected = {
            "repository_and_task_relevance": "high",
            "trust_and_security": "not_assessed",
            "metadata_completeness": "complete",
            "execution_readiness": "not_declared",
        }
        invalid_dimensions = (
            {},
            {"invented": "value"},
            {key: value for key, value in expected.items() if key != "execution_readiness"},
            {**expected, "invented": "value"},
        )
        for dimensions in invalid_dimensions:
            with self.subTest(dimensions=dimensions):
                with self.assertRaisesRegex(ValueError, "dimension"):
                    SkillAssessment(
                        skill=record,
                        classification="Unknown or insufficient evidence",
                        dimensions=dimensions,
                        hard_gates=(),
                        rationale=(),
                    )

        assessment = SkillAssessment(
            skill=record,
            classification="Unknown or insufficient evidence",
            dimensions=expected,
            hard_gates=(),
            rationale=(),
        )
        self.assertEqual(expected, assessment.to_dict()["dimensions"])
        with self.assertRaises(TypeError):
            assessment.dimensions["invented"] = "value"
        self.assertEqual(set(expected), set(assessment.to_dict()["dimensions"]))
        serialized = assessment.to_dict()
        serialized["dimensions"]["invented"] = "output-only"
        self.assertEqual(set(expected), set(assessment.to_dict()["dimensions"]))

        schema = json.loads(
            (PLUGIN_ROOT / "schemas" / "recommendation-plan.schema.json").read_text(
                encoding="utf-8"
            )
        )
        dimensions_schema = schema["$defs"]["skillAssessment"]["properties"][
            "dimensions"
        ]
        self.assertFalse(dimensions_schema["additionalProperties"])
        self.assertEqual(set(expected), set(dimensions_schema["required"]))
        self.assertEqual(set(expected), set(dimensions_schema["properties"]))

    def test_plan_v5_exposes_plugin_skills_without_removing_nested_capabilities(self) -> None:
        plugins = enrich_plugins(
            discover_plugins(inventory_file=FIXTURES / "codex_plugins.json")
        )
        plan = build_recommendation_plan(
            plugins,
            inspect_repository(PLUGIN_ROOT),
            "choose the smallest evidence-backed skill set",
        )
        payload = plan.to_dict()
        specialist = next(
            item for item in payload["plugins"] if item["plugin_id"] == "specialist@fixture"
        )
        skill = next(
            item
            for item in payload["skills"]
            if item["source"]["identity"] == "specialist@fixture"
        )

        self.assertEqual("plugin-compass.plan.v5", payload["schema_version"])
        self.assertEqual("plugin-selection-specialist", specialist["capabilities"][0]["name"])
        self.assertEqual("plugin-selection-specialist", skill["name"])
        self.assertEqual("plugin", skill["source"]["type"])
        self.assertEqual("skills/plugin-selection-specialist/SKILL.md", skill["relative_path"])

        schema = json.loads(
            (PLUGIN_ROOT / "schemas" / "recommendation-plan.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual("plugin-compass.plan.v5", schema["properties"]["schema_version"]["const"])
        self.assertIn("skills", schema["required"])
        self.assertIn("qualified_identity", schema["$defs"]["skill"]["required"])
        self.assertEqual(
            set(schema["$defs"]["skill"]["properties"]),
            set(skill),
        )


if __name__ == "__main__":
    unittest.main()
