from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_decision import PLUGIN_ROOT  # also adds the plugin import path
from plugin_compass.metadata import enrich_plugin
from plugin_compass.models import PluginRecord, RepositoryContext
from plugin_compass.decision import build_recommendation_plan
from plugin_compass.readiness import inspect_readiness


class ReadinessTests(unittest.TestCase):
    def test_missing_runtime_is_observed_without_executing_it(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "subprocess.run", side_effect=AssertionError("must remain static")
        ):
            root = Path(directory)
            readiness, evidence = inspect_readiness(
                root, root / "SKILL.md", 'python <plugin-install-path>/runtime/runner.py'
            )
        self.assertEqual("missing_files", readiness.status)
        self.assertEqual(("runtime/runner.py",), readiness.references)
        self.assertEqual("missing", evidence[0].status)

    def test_files_present_is_not_a_runtime_verification_claim(self):
        with tempfile.TemporaryDirectory(prefix="plugin with spaces ") as directory:
            root = Path(directory)
            (root / "run.py").write_text("raise RuntimeError('do not execute')", encoding="utf-8")
            readiness, _ = inspect_readiness(root, root / "SKILL.md", 'python "<plugin-root>/run.py"')
        self.assertEqual("files_present", readiness.status)

    def test_traversal_is_unknown_without_probing_outside_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(Path, "is_file", side_effect=AssertionError("no external probe")):
                readiness, evidence = inspect_readiness(root, root / "SKILL.md", "<plugin-dir>/../outside.py")
        self.assertEqual("unknown", readiness.status)
        self.assertEqual("unknown", evidence[0].status)

    def test_narrative_and_unanchored_examples_are_not_dependencies(self):
        readiness, evidence = inspect_readiness(PLUGIN_ROOT, PLUGIN_ROOT / "SKILL.md", "Example: python scripts/example.py\nNo local runner is required.")
        self.assertEqual("not_declared", readiness.status)
        self.assertEqual((), evidence)

    def test_missing_runner_does_not_exclude_healthy_sibling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".codex-plugin").mkdir()
            (root / ".codex-plugin/plugin.json").write_text(json.dumps({
                "name": "mixed", "description": "Pipeline execution and architecture advice",
                "skills": "./skills/"
            }), encoding="utf-8")
            for name, description, body in (
                ("run", "Execute pipeline workers", "python <plugin-root>/runtime/runner.py"),
                ("design", "Review architecture boundaries", "Read and explain architecture."),
            ):
                skill = root / "skills" / name
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n", encoding="utf-8")
            plugin = enrich_plugin(PluginRecord("mixed@fixture", "mixed", "fixture", "1.0.0", True, True, str(root)))
            repo = RepositoryContext(str(root), True, False)
            run_plan = build_recommendation_plan((plugin,), repo, "Execute pipeline workers")
            exact_run_plan = build_recommendation_plan((plugin,), repo, "Use mixed run to execute pipeline workers")
            design_plan = build_recommendation_plan((plugin,), repo, "Review architecture boundaries")
        self.assertEqual((), run_plan.recommendations)
        self.assertEqual((), exact_run_plan.recommendations)
        self.assertEqual("Unknown or insufficient evidence", exact_run_plan.assessments[0].classification)
        self.assertEqual("Unknown or insufficient evidence", run_plan.assessments[0].classification)
        self.assertEqual(("design",), design_plan.recommendations[0].capability_names)
        self.assertEqual("unknown", run_plan.assessments[0].dimensions["trust_and_security"])
        self.assertTrue(any(item.kind == "runtime-file" for item in run_plan.evidence))


if __name__ == "__main__":
    unittest.main()
