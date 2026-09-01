from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "plugin-compass"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from plugin_compass.adapters.codex import (  # noqa: E402
    CodexInventoryError,
    CodexInventoryInconclusive,
    discover_plugins,
    load_inventory_file,
    run_inventory,
)
from plugin_compass.adapters.drskill import (  # noqa: E402
    DrSkillEvidenceError,
    load_report as load_drskill_report,
    parse_jsonl,
)
from plugin_compass.adapters.hol import (  # noqa: E402
    load_report as load_hol_report,
    parse_report as parse_hol_report,
)
from plugin_compass.metadata import enrich_plugins  # noqa: E402


class CodexAdapterTests(unittest.TestCase):
    def test_live_empty_inventory_is_inconclusive_without_automatic_retry(self) -> None:
        completed = subprocess.CompletedProcess([], 0, '{"installed":[],"available":[]}', '')
        with patch("plugin_compass.adapters.codex.subprocess.run", return_value=completed) as run:
            with self.assertRaises(CodexInventoryInconclusive):
                run_inventory()
        run.assert_called_once_with(
            ["codex", "plugin", "list", "--json"], check=False, capture_output=True,
            text=True, timeout=20.0, shell=False,
        )

    def test_live_nonempty_inventory_still_uses_the_official_cli(self) -> None:
        payload = {"installed": [{"name": "sample", "installed": True}], "available": []}
        with patch(
            "plugin_compass.adapters.codex.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), ''),
        ):
            observed, evidence = run_inventory()
        self.assertEqual(payload, observed)
        self.assertEqual("codex-inventory", evidence.kind)

    def test_invalid_live_shape_is_not_treated_as_an_empty_inventory(self) -> None:
        with patch(
            "plugin_compass.adapters.codex.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, '{}', ''),
        ):
            with self.assertRaises(CodexInventoryError) as error:
                run_inventory()
        self.assertNotIsInstance(error.exception, CodexInventoryInconclusive)

    def test_explicit_empty_snapshot_does_not_run_live_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary) / "approved empty.json"
            snapshot.write_text('{"installed":[],"available":[]}', encoding="utf-8")
            with patch("plugin_compass.adapters.codex.subprocess.run") as run:
                self.assertEqual((), discover_plugins(inventory_file=snapshot))
            run.assert_not_called()

    def test_fixture_inventory_resolves_relative_roots_with_spaces(self) -> None:
        plugins = enrich_plugins(
            discover_plugins(inventory_file=FIXTURES / "codex_plugins.json")
        )
        by_id = {plugin.plugin_id: plugin for plugin in plugins}

        self.assertEqual(7, len(plugins))
        specialist = by_id["specialist@fixture"]
        self.assertTrue(Path(specialist.source_root or "").is_absolute())
        self.assertIn("Plugin Compass", specialist.source_root or "")
        self.assertEqual("complete", specialist.metadata_status)
        self.assertEqual(
            ["plugin-selection-specialist"],
            [capability.name for capability in specialist.capabilities],
        )

        self.assertEqual("malformed", by_id["malformed@fixture"].metadata_status)
        self.assertTrue(by_id["risky-exec@fixture"].has_mcp)
        self.assertFalse(by_id["disabled-docs@fixture"].enabled)
        self.assertEqual(
            ["llm-cost-optimizer"],
            [
                capability.name
                for capability in by_id["claude-code-skills@fixture"].capabilities
            ],
        )

    def test_invalid_inventory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad inventory.json"
            path.write_text('{"installed": "not-an-array"}', encoding="utf-8")
            with self.assertRaises(CodexInventoryError):
                load_inventory_file(path)


class DrSkillAdapterTests(unittest.TestCase):
    def test_jsonl_is_normalized_and_fix_commands_remain_data(self) -> None:
        findings, evidence = load_drskill_report(FIXTURES / "drskill_findings.jsonl")

        self.assertEqual(3, len(findings))
        overlap = next(item for item in findings if item.check_id == "near-duplicate")
        self.assertEqual(("Compare and keep one",), overlap.fix_commands)
        self.assertEqual("drskill-report", evidence[0].kind)

    def test_jsonl_order_does_not_change_findings(self) -> None:
        lines = (FIXTURES / "drskill_findings.jsonl").read_text(encoding="utf-8").splitlines()
        forward, _ = parse_jsonl("\n".join(lines), source="stable-source")
        reverse, _ = parse_jsonl("\n".join(reversed(lines)), source="stable-source")
        self.assertEqual(forward, reverse)

    def test_invalid_jsonl_reports_the_line(self) -> None:
        with self.assertRaisesRegex(DrSkillEvidenceError, r"report:2"):
            parse_jsonl('{"check_id":"ok"}\nnot-json', source="report")


class HolAdapterTests(unittest.TestCase):
    def test_clean_report_is_exact_target_review_evidence(self) -> None:
        findings, evidence = load_hol_report(FIXTURES / "hol_clean.json")
        self.assertEqual((), findings)
        self.assertEqual("specialist", evidence[0].subject)
        self.assertEqual("reviewed", evidence[0].status)

    def test_blocked_report_preserves_high_finding_and_target(self) -> None:
        findings, evidence = load_hol_report(FIXTURES / "hol_blocked.json")
        self.assertEqual(1, len(findings))
        self.assertEqual("high", findings[0].severity)
        self.assertEqual(("broad-suite",), findings[0].target_plugin_ids)
        self.assertEqual("blocked", evidence[0].status)

    def test_wsl_absolute_target_is_not_rebased_as_a_windows_relative_path(self) -> None:
        payload = json.loads((FIXTURES / "hol_clean.json").read_text(encoding="utf-8"))
        payload["pluginDir"] = "/mnt/c/Users/example/plugin"
        _findings, evidence = parse_hol_report(
            payload,
            source="fixture",
            base_dir=FIXTURES,
        )
        self.assertEqual("/mnt/c/Users/example/plugin", evidence[0].target_root)


if __name__ == "__main__":
    unittest.main()
