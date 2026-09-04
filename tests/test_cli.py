from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "plugin-compass"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from plugin_compass.cli import main  # noqa: E402


def common_arguments(command: str, output_format: str = "json") -> list[str]:
    return [
        command,
        "--inventory-file",
        str(FIXTURES / "codex_plugins.json"),
        "--repo",
        str(PLUGIN_ROOT),
        "--task",
        "choose the smallest evidence-backed Codex plugin and skill set",
        "--drskill-report",
        str(FIXTURES / "drskill_findings.jsonl"),
        "--hol-report",
        str(FIXTURES / "hol_clean.json"),
        "--hol-report",
        str(FIXTURES / "hol_blocked.json"),
        "--format",
        output_format,
    ]


class CliTests(unittest.TestCase):
    def run_cli(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_inventory_json_is_machine_readable(self) -> None:
        code, stdout, stderr = self.run_cli(
            [
                "inventory",
                "--inventory-file",
                str(FIXTURES / "codex_plugins.json"),
                "--format",
                "json",
            ]
        )
        payload = json.loads(stdout)
        self.assertEqual(0, code, stderr)
        self.assertEqual("plugin-compass.inventory.v3", payload["schema_version"])
        self.assertEqual(7, len(payload["plugins"]))

    def test_subprocess_emits_utf8_on_windows_code_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inventory = Path(temporary) / "unicode inventory.json"
            inventory.write_text(
                json.dumps(
                    {
                        "installed": [
                            {
                                "pluginId": "unicode@fixture",
                                "name": "unicode",
                                "marketplaceName": "fixture",
                                "description": "input → output",
                                "installed": True,
                                "enabled": True,
                            }
                        ],
                        "available": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(PLUGIN_ROOT / "scripts" / "plugin_compass.py"),
                    "inventory",
                    "--inventory-file",
                    str(inventory),
                    "--format",
                    "json",
                ],
                check=False,
                capture_output=True,
                encoding="utf-8",
                shell=False,
            )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("input → output", completed.stdout)

    def test_assess_json_matches_public_contract(self) -> None:
        code, stdout, stderr = self.run_cli(common_arguments("assess"))
        payload = json.loads(stdout)
        self.assertEqual(0, code, stderr)
        self.assertEqual("plugin-compass.plan.v5", payload["schema_version"])
        self.assertEqual("specialist@fixture", payload["recommendations"][0]["plugin_id"])
        self.assertTrue(payload["evidence"])

    def test_prompt_json_contains_ready_to_paste_prompt(self) -> None:
        arguments = common_arguments("prompt")
        arguments[arguments.index("--task") + 1] = (
            "choose model and reasoning effort before scheduling subagents"
        )
        code, stdout, stderr = self.run_cli(arguments)
        payload = json.loads(stdout)
        self.assertEqual(0, code, stderr)
        self.assertEqual("plugin-compass.prompt.v3", payload["schema_version"])
        self.assertIn("Use only this evidence-backed capability set", payload["generated_prompt"])
        self.assertEqual("speed", payload["optimization_goal"])
        self.assertEqual([], payload["invocation_routes"])
        self.assertEqual("fastest_verified_completion", payload["scheduling_guidance"]["objective"])
        self.assertEqual("advisory", payload["scheduling_guidance"]["enforcement"])
        self.assertIn("acceptance_checks", payload["scheduling_guidance"]["decision_fields"])

    def test_cost_mode_is_explicit(self) -> None:
        code, stdout, stderr = self.run_cli(
            common_arguments("prompt") + ["--optimization-goal", "cost"]
        )
        payload = json.loads(stdout)
        self.assertEqual(0, code, stderr)
        self.assertEqual("cost", payload["optimization_goal"])
        self.assertEqual(
            "claude-code-skills:llm-cost-optimizer",
            payload["invocation_routes"][0]["capability_name"],
        )
        self.assertIsNone(payload["scheduling_guidance"])

    def test_empty_live_discovery_stops_every_command_before_recommendations(self) -> None:
        for command in ("inventory", "assess", "recommend", "prompt"):
            for output_format in ("json", "markdown"):
                with self.subTest(command=command, output_format=output_format):
                    args = [command, "--format", output_format]
                    if command != "inventory":
                        args += ["--repo", str(PLUGIN_ROOT), "--task", "select agent effort"]
                    with patch(
                        "plugin_compass.adapters.codex.subprocess.run",
                        return_value=subprocess.CompletedProcess(
                            [], 0, '{"installed":[],"available":[]}', ''
                        ),
                    ) as run:
                        code, stdout, stderr = self.run_cli(args)
                    self.assertEqual(3, code)
                    self.assertEqual(1, run.call_count)
                    if output_format == "json":
                        payload = json.loads(stdout)
                        self.assertEqual("inconclusive", payload["status"])
                        self.assertEqual("CODEX_INVENTORY_EMPTY", payload["code"])
                        self.assertNotIn("recommendations", payload)
                        self.assertNotIn("generated_prompt", payload)
                        self.assertEqual("", stderr)
                    else:
                        self.assertEqual("", stdout)
                        self.assertIn("inconclusive", stderr)
                        self.assertIn("--inventory-file", stderr)

    def test_empty_saved_and_live_inventory_stop_before_standalone_scan(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Plugin Compass empty inventory ") as temporary:
            root = Path(temporary)
            snapshot = root / "approved empty.json"
            snapshot.write_text('{"installed":[],"available":[]}', encoding="utf-8")
            skill_root = root / "Standalone Skills"
            skill_root.mkdir()
            for source in ("saved", "live"):
                with self.subTest(source=source), patch(
                    "plugin_compass.cli.discover_standalone_skills",
                    side_effect=AssertionError("standalone discovery must not run"),
                ) as standalone_scan:
                    args = [
                        "inventory",
                        "--user-skill-root", "user:fixture", str(skill_root),
                        "--format", "json",
                    ]
                    if source == "saved":
                        args += ["--inventory-file", str(snapshot)]
                        code, stdout, stderr = self.run_cli(args)
                    else:
                        with patch(
                            "plugin_compass.adapters.codex.subprocess.run",
                            return_value=subprocess.CompletedProcess(
                                [], 0, '{"installed":[],"available":[]}', ''
                            ),
                        ):
                            code, stdout, stderr = self.run_cli(args)
                    payload = json.loads(stdout)
                    self.assertEqual(3, code, stderr)
                    self.assertEqual("CODEX_INVENTORY_EMPTY", payload["code"])
                    standalone_scan.assert_not_called()

    def test_speed_guidance_is_visible_in_recommend_markdown(self) -> None:
        args = common_arguments("recommend", "markdown")
        args[args.index("--task") + 1] = "select agent reasoning effort"
        code, stdout, stderr = self.run_cli(args)
        self.assertEqual(0, code, stderr)
        self.assertIn("Per-agent effort guidance (advisory)", stdout)
        self.assertNotIn("llm-cost-optimizer", stdout)

    def test_report_mode_never_runs_tool_or_fix_commands(self) -> None:
        with patch(
            "plugin_compass.adapters.codex.subprocess.run",
            side_effect=AssertionError("Codex command execution was not expected"),
        ), patch(
            "plugin_compass.adapters.drskill.subprocess.run",
            side_effect=AssertionError("DrSkill or fix execution was not expected"),
        ):
            code, stdout, stderr = self.run_cli(common_arguments("recommend", "markdown"))
        self.assertEqual(0, code, stderr)
        self.assertIn("specialist@fixture", stdout)

    def test_collect_and_report_are_mutually_exclusive(self) -> None:
        arguments = common_arguments("assess") + ["--collect-drskill"]
        code, _stdout, stderr = self.run_cli(arguments)
        self.assertEqual(2, code)
        self.assertIn("mutually exclusive", stderr)


if __name__ == "__main__":
    unittest.main()
