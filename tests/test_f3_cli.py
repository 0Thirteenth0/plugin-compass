from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "plugin-compass"
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from plugin_compass.cli import main  # noqa: E402


class F3CliTests(unittest.TestCase):
    def run_cli(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(arguments)
        except SystemExit as exc:
            self.fail(f"CLI parser rejected the F3 contract: {exc}")
        return code, stdout.getvalue(), stderr.getvalue()

    def make_skill_root(self, base: Path, folder: str, name: str) -> Path:
        root = base / folder
        skill_dir = root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name} fixture capability.\n---\n",
            encoding="utf-8",
        )
        return root

    def root_arguments(self, base: Path) -> list[str]:
        user = self.make_skill_root(base, "User Skills", "user-review")
        project = self.make_skill_root(base, "Project Skills", "project-review")
        system = self.make_skill_root(base, "System Skills", "system-review")
        return [
            "--user-skill-root", "user:fixture", str(user),
            "--project-skill-root", "project:fixture", str(project),
            "--system-skill-root", "system:fixture", str(system),
        ]

    def base_arguments(self, command: str) -> list[str]:
        arguments = [
            command,
            "--inventory-file", str(FIXTURES / "codex_plugins.json"),
        ]
        if command != "inventory":
            arguments += [
                "--repo", str(PLUGIN_ROOT),
                "--task", "user project system review",
            ]
        return arguments

    def test_explicit_repeatable_roots_reach_every_json_surface(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F3 ") as temporary:
            root_args = self.root_arguments(Path(temporary))
            payloads = {}
            for command in ("inventory", "assess", "recommend", "prompt"):
                with self.subTest(command=command):
                    code, stdout, stderr = self.run_cli(
                        self.base_arguments(command) + root_args + ["--format", "json"]
                    )
                    self.assertEqual(0, code, stderr)
                    payloads[command] = json.loads(stdout)

        self.assertEqual(
            {"inventory", "assess", "recommend", "prompt"},
            set(payloads),
        )
        self.assertEqual("plugin-compass.inventory.v3", payloads["inventory"]["schema_version"])
        self.assertEqual("plugin-compass.plan.v5", payloads["assess"]["schema_version"])
        self.assertEqual("plugin-compass.plan.v5", payloads["recommend"]["schema_version"])
        self.assertEqual("plugin-compass.prompt.v3", payloads["prompt"]["schema_version"])
        for command, payload in payloads.items():
            self.assertEqual("complete", payload["standalone_discovery"]["status"], command)
            standalone = [item for item in payload["skills"] if item["source"]["type"] != "plugin"]
            self.assertEqual(
                {"standalone-user", "standalone-project", "system"},
                {item["source"]["type"] for item in standalone},
                command,
            )
        self.assertIn("skill_assessments", payloads["assess"])
        self.assertIn("skill_recommendations", payloads["recommend"])
        self.assertIn("skill_ambiguities", payloads["prompt"])
        inventory_schema = json.loads(
            (PLUGIN_ROOT / "schemas" / "inventory.schema.json").read_text(encoding="utf-8")
        )
        prompt_schema = json.loads(
            (PLUGIN_ROOT / "schemas" / "prompt.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            "plugin-compass.inventory.v3",
            inventory_schema["properties"]["schema_version"]["const"],
        )
        self.assertEqual(
            "plugin-compass.prompt.v3",
            prompt_schema["properties"]["schema_version"]["const"],
        )

    def test_missing_explicit_root_is_a_truthful_degraded_inventory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F3 ") as temporary:
            missing = Path(temporary) / "Missing Skills"
            code, stdout, stderr = self.run_cli([
                "inventory",
                "--inventory-file", str(FIXTURES / "codex_plugins.json"),
                "--project-skill-root", "project:missing", str(missing),
                "--format", "json",
            ])

        payload = json.loads(stdout)
        self.assertEqual(0, code, stderr)
        self.assertEqual("degraded", payload["standalone_discovery"]["status"])
        self.assertEqual(
            ["root-missing"],
            [item["code"] for item in payload["standalone_discovery"]["diagnostics"]],
        )

    def test_degraded_root_diagnostic_is_visible_in_every_markdown_surface(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F3 ") as temporary:
            missing = Path(temporary) / "Missing Skills"
            for command in ("inventory", "assess", "recommend", "prompt"):
                with self.subTest(command=command):
                    code, stdout, stderr = self.run_cli(
                        self.base_arguments(command)
                        + [
                            "--project-skill-root", "project:missing", str(missing),
                            "--format", "markdown",
                        ]
                    )
                    self.assertEqual(0, code, stderr)
                    self.assertIn("root-missing", stdout)

    def test_markdown_preserves_skill_evidence_and_all_assessments(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F3 rendering ") as temporary:
            root = Path(temporary) / "Rendered Skills"
            ready = root / "ready-review"
            blocked = root / "blocked-review"
            ready.mkdir(parents=True)
            blocked.mkdir(parents=True)
            (ready / "SKILL.md").write_text(
                "---\nname: ready-review\ndescription: Ready review capability.\n---\n",
                encoding="utf-8",
            )
            (blocked / "SKILL.md").write_text(
                "---\nname: blocked-review\ndescription: Blocked review capability.\n---\n"
                "Run `$SKILL_DIR/scripts/missing.py`.\n",
                encoding="utf-8",
            )
            common = [
                "--inventory-file", str(FIXTURES / "codex_plugins.json"),
                "--repo", str(PLUGIN_ROOT),
                "--task", "ready review",
                "--user-skill-root", "user:rendering", str(root),
                "--format", "markdown",
            ]
            assess_code, assess_stdout, assess_stderr = self.run_cli(["assess", *common])
            rendered = {}
            for command in ("recommend", "prompt"):
                code, stdout, stderr = self.run_cli([command, *common])
                self.assertEqual(0, code, stderr)
                rendered[command] = stdout

        self.assertEqual(0, assess_code, assess_stderr)
        self.assertIn("Source-neutral skill assessments", assess_stdout)
        self.assertIn("ready-review", assess_stdout)
        self.assertIn("blocked-review", assess_stdout)
        self.assertIn("readiness=missing_files", assess_stdout)
        for command, output in rendered.items():
            self.assertIn("ready-review", output, command)
            self.assertIn("source=standalone-user/user:rendering", output, command)
            self.assertIn("trust=not_assessed", output, command)
            self.assertIn("metadata=complete", output, command)
            self.assertIn("readiness=not_declared", output, command)

    def test_no_roots_are_reported_as_not_configured_without_inference(self) -> None:
        code, stdout, stderr = self.run_cli(
            self.base_arguments("inventory") + ["--format", "json"]
        )
        payload = json.loads(stdout)
        self.assertEqual(0, code, stderr)
        self.assertIn("standalone_discovery", payload)
        self.assertEqual("not_configured", payload["standalone_discovery"]["status"])
        self.assertEqual([], payload["standalone_discovery"]["diagnostics"])

    def test_qualified_selection_is_exact_and_ambiguous_bare_name_selects_none(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Plugin Compass F3 ") as temporary:
            base = Path(temporary)
            first = self.make_skill_root(base, "First Skills", "shared-review")
            second = self.make_skill_root(base, "Second Skills", "shared-review")
            common = self.base_arguments("recommend") + [
                "--project-skill-root", "project:first", str(first),
                "--user-skill-root", "user:second", str(second),
                "--format", "json",
            ]
            code, stdout, stderr = self.run_cli(
                common + ["--select-skill", "shared-review"]
            )
            ambiguous = json.loads(stdout)
            qualified = next(
                item["qualified_identity"]
                for item in ambiguous["skills"]
                if item["source"]["identity"] == "user:second"
            )
            exact_code, exact_stdout, exact_stderr = self.run_cli(
                common + ["--select-skill", qualified]
            )

        self.assertEqual(0, code, stderr)
        self.assertEqual([], ambiguous["skill_recommendations"])
        self.assertEqual(2, len(ambiguous["skill_ambiguities"][0]["candidates"]))
        self.assertEqual(0, exact_code, exact_stderr)
        exact = json.loads(exact_stdout)
        self.assertEqual(
            [qualified],
            [item["qualified_identity"] for item in exact["skill_recommendations"]],
        )

    def test_unknown_skill_selection_fails_closed(self) -> None:
        code, _stdout, stderr = self.run_cli(
            self.base_arguments("recommend")
            + ["--select-skill", "missing-skill", "--format", "json"]
        )
        self.assertEqual(2, code)
        self.assertIn("unknown skill selection", stderr)


if __name__ == "__main__":
    unittest.main()
