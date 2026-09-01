from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "plugins" / "compass-builder" / "skills" / "compass-builder"


class BuilderSkillContractTests(unittest.TestCase):
    def test_progressive_disclosure_and_automatic_invocation_metadata(self):
        entry = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        metadata = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")
        for name in ("preflight", "sequential", "parallel", "recovery"):
            self.assertIn(f"references/{name}.md", entry)
        self.assertIn('default_prompt: "Use $compass-builder', metadata)
        self.assertIn("allow_implicit_invocation: true", metadata)
        self.assertLess(len(entry.splitlines()), 50)

    def test_every_mode_forbids_nested_workers_and_shared_controller_writes(self):
        documents = [SKILL / "SKILL.md", *sorted((SKILL / "references").rglob("*.md"))]
        for path in documents:
            text = path.read_text(encoding="utf-8").casefold()
            with self.subTest(path=path.name):
                self.assertIn("must not launch child workers", text)
                self.assertIn("controller", text)
        parallel = (SKILL / "references" / "parallel.md").read_text(encoding="utf-8").casefold()
        self.assertIn("priority never establishes independence", parallel)
        self.assertIn("never write shared controller state", parallel)
        self.assertNotIn("priority-only independence", parallel)
        notices = (ROOT / "plugins" / "compass-builder" / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("subagent-prompt.md", notices)
        self.assertIn("parallel-subagent-prompt.md", notices)
        self.assertNotIn("No upstream source file is adapted", notices)

    def test_launcher_prompt_and_flag_enforce_no_nested_workers(self):
        launcher = (ROOT / "plugins" / "compass-builder" / "compass_builder" / "launcher.py").read_text(encoding="utf-8")
        self.assertIn('"--disable", "multi_agent"', launcher)
        self.assertIn("Do not launch child workers or agents", launcher)


if __name__ == "__main__":
    unittest.main()
