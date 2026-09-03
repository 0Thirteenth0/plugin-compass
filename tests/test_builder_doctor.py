from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "plugins" / "compass-builder"
if str(BUILDER) not in sys.path:
    sys.path.insert(0, str(BUILDER))

from compass_builder.doctor import (  # noqa: E402
    COMMANDS, CommandEvidence, DoctorError, capture_evidence, doctor_from_captured,
)
from compass_builder.models import canonical_json  # noqa: E402


def digest(value: dict) -> str:
    return "sha256:" + sha256(canonical_json(value)).hexdigest()


def load(name: str) -> dict:
    path = ROOT / "tests" / "fixtures" / "compass_builder" / f"{name}.valid.json"
    return json.loads(path.read_text(encoding="utf-8"))


def evidence(repo: Path, spec: dict) -> dict[str, CommandEvidence]:
    outputs = {
        "codexVersion": "codex-cli 1.2.3\n",
        "codexExecHelp": "-C, --cd <DIR>  -m, --model <MODEL>  --json  --output-schema <FILE>  --disable <FEATURE>  --ignore-user-config  --approve-for-me\n",
        "codexFeatures": "multi_agent stable true\nplugins stable true\nhooks stable true\n",
        "gitVersion": "git version 2.51.0.windows.1\n",
        "worktreeList": f"worktree {repo.as_posix()}\nHEAD {spec['baseSha']}\nbranch refs/heads/main\n\n",
        "repositoryRoot": str(repo) + "\n",
        "baseSha": spec["baseSha"] + "\n",
        "status": "",
        "trackedGitignore": ".gitignore\n",
        "trackedController": "",
        "ignoredStateProbe": ".gitignore:1:/.compass-builder/\t.compass-builder/runs/doctor-probe/state.json\n",
        "ignoredWorktreeProbe": ".gitignore:1:/.compass-builder/\t.compass-builder/worktrees/doctor-probe/story\n",
    }
    result = {}
    for name, template in COMMANDS.items():
        argv = tuple(part.replace("{baseRef}", spec["baseRef"]) for part in template)
        result[name] = CommandEvidence(argv, 0, outputs[name], "")
    return result


def host_for(captured: dict[str, CommandEvidence]) -> dict:
    host = load("host-capabilities")
    cli = ("codexVersion", "codexExecHelp", "codexFeatures")
    git = tuple(name for name in COMMANDS if name not in cli)
    host["cliEvidenceDigest"] = digest({name: captured[name].wire() for name in cli})
    host["gitEvidenceDigest"] = digest({name: captured[name].wire() for name in git})
    return host


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name).resolve()
        (self.repo / ".gitignore").write_text("/.compass-builder/\n", encoding="utf-8")
        self.spec = load("run-spec")
        self.captured = evidence(self.repo, self.spec)
        self.host = host_for(self.captured)

    def tearDown(self):
        self.temporary.cleanup()

    def doctor(self, *, captured=None, host=None):
        return doctor_from_captured(
            self.repo, self.spec, host or self.host, captured or self.captured,
            planning_timestamp="2026-09-01T12:01:00Z",
        )

    def test_captured_evidence_produces_a_deterministic_read_only_report(self):
        first = self.doctor()
        second = self.doctor()
        self.assertEqual(first, second)
        self.assertTrue(first["workingTreeClean"])
        self.assertEqual(self.spec["baseSha"], first["resolvedBaseSha"])
        self.assertEqual(digest(self.host), first["hostEvidenceDigest"])
        self.assertFalse((self.repo / ".compass-builder").exists())

    def test_live_capture_surface_contains_only_the_bounded_read_only_commands(self):
        observed = []

        def runner(argv, cwd):
            observed.append((tuple(argv), cwd))
            return CommandEvidence(tuple(argv), 0, "captured")

        result = capture_evidence(self.repo, self.spec["baseRef"], runner=runner)
        self.assertEqual(set(COMMANDS), set(result))
        self.assertEqual(len(COMMANDS), len(observed))
        forbidden = {"add", "commit", "merge", "checkout", "switch", "reset", "clean", "remove", "prune"}
        for argv, cwd in observed:
            self.assertEqual(self.repo, cwd)
            self.assertFalse(forbidden.intersection(argv), argv)

    def test_missing_cli_contract_or_generic_config_help_fails_closed(self):
        removals = (("-C",), ("-m", "--model"), ("--json",), ("--output-schema",), ("--disable",))
        for removed in removals:
            with self.subTest(removed=removed):
                captured = copy.deepcopy(self.captured)
                text = captured["codexExecHelp"].stdout
                for flag in removed:
                    text = text.replace(flag, "-c")
                captured["codexExecHelp"] = CommandEvidence(captured["codexExecHelp"].argv, 0, text)
                host = host_for(captured)
                with self.assertRaisesRegex(DoctorError, "support claims|exact model"):
                    self.doctor(captured=captured, host=host)
        captured = copy.deepcopy(self.captured)
        item = captured["codexFeatures"]
        captured["codexFeatures"] = CommandEvidence(
            item.argv, 0,
            "multi_agent experimental true\nplugins stable true\nhooks stable true\n",
        )
        host = host_for(captured)
        with self.assertRaisesRegex(DoctorError, "support claims"):
            self.doctor(captured=captured, host=host)

    def test_reasoning_config_requires_separate_native_proof_not_generic_c_help(self):
        captured = copy.deepcopy(self.captured)
        item = captured["codexExecHelp"]
        captured["codexExecHelp"] = CommandEvidence(
            item.argv, 0, item.stdout + "  -c <key=value> generic config override\n"
        )
        host = host_for(captured)
        report = self.doctor(captured=captured, host=host)
        self.assertEqual(
            "model_reasoning_effort",
            report["hostCapabilities"]["reasoningConfig"]["key"],
        )
        missing = copy.deepcopy(host)
        del missing["reasoningConfig"]
        with self.assertRaisesRegex(DoctorError, "reasoningConfig"):
            self.doctor(captured=captured, host=missing)
        mismatched = copy.deepcopy(host)
        mismatched["reasoningConfig"]["key"] = "generic.key"
        with self.assertRaisesRegex(DoctorError, "reasoningConfig.key"):
            self.doctor(captured=captured, host=mismatched)

    def test_option_and_feature_lookalikes_do_not_prove_capabilities(self):
        captured = copy.deepcopy(self.captured)
        help_item = captured["codexExecHelp"]
        captured["codexExecHelp"] = CommandEvidence(
            help_item.argv, 0,
            "-C --model <MODEL> --json-schema "
            "--output-schema-file --disable-old --ignore-user-config --approve-for-me\n",
        )
        feature_item = captured["codexFeatures"]
        captured["codexFeatures"] = CommandEvidence(
            feature_item.argv, 0,
            "multi_agent stable false but enabled elsewhere\n"
            "plugins stable true\nhooks stable true\n",
        )
        host = host_for(captured)
        with self.assertRaisesRegex(DoctorError, "support claims"):
            self.doctor(captured=captured, host=host)

    def test_multi_agent_evidence_requires_one_unambiguous_authoritative_row(self):
        rows = (
            "multi_agent stable true\nmulti_agent stable true\n",
            "multi_agent stable false\nmulti_agent stable true\n",
            "multi_agent stable true extra-field\n",
        )
        for feature_rows in rows:
            with self.subTest(feature_rows=feature_rows):
                captured = copy.deepcopy(self.captured)
                item = captured["codexFeatures"]
                captured["codexFeatures"] = CommandEvidence(
                    item.argv, 0,
                    feature_rows + "plugins stable true\nhooks stable true\n",
                )
                with self.assertRaisesRegex(DoctorError, "support claims"):
                    self.doctor(captured=captured, host=host_for(captured))

    def test_missing_worker_isolation_surface_fails_closed(self):
        for removed, replacement in (
            ("--ignore-user-config", "--ignored-config"),
            ("--approve-for-me", "--approve-manually"),
        ):
            with self.subTest(removed=removed):
                captured = copy.deepcopy(self.captured)
                item = captured["codexExecHelp"]
                captured["codexExecHelp"] = CommandEvidence(
                    item.argv, 0, item.stdout.replace(removed, replacement)
                )
                with self.assertRaisesRegex(DoctorError, "worker isolation surface"):
                    self.doctor(captured=captured, host=host_for(captured))

        for feature in ("plugins", "hooks"):
            with self.subTest(feature=feature):
                captured = copy.deepcopy(self.captured)
                item = captured["codexFeatures"]
                captured["codexFeatures"] = CommandEvidence(
                    item.argv, 0,
                    item.stdout.replace(f"{feature} stable true", f"{feature} stable false"),
                )
                with self.assertRaisesRegex(DoctorError, "worker isolation surface"):
                    self.doctor(captured=captured, host=host_for(captured))

    def test_missing_stale_or_inconsistent_native_capability_evidence_fails_closed(self):
        with self.assertRaisesRegex(DoctorError, "stale"):
            doctor_from_captured(
                self.repo, self.spec, self.host, self.captured,
                planning_timestamp="2026-09-01T12:06:00Z",
            )
        host = copy.deepcopy(self.host)
        host["selectedModel"] = "different-model"
        with self.assertRaisesRegex(DoctorError, "different selected model"):
            self.doctor(host=host)
        host = copy.deepcopy(self.host)
        host["cliEvidenceDigest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(DoctorError, "does not bind"):
            self.doctor(host=host)

    def test_explicit_base_ref_is_resolved_once_and_must_match(self):
        captured = copy.deepcopy(self.captured)
        item = captured["baseSha"]
        captured["baseSha"] = CommandEvidence(item.argv, 0, "b" * 40 + "\n")
        host = host_for(captured)
        with self.assertRaisesRegex(DoctorError, "immutable run-spec baseSha"):
            self.doctor(captured=captured, host=host)
        captured = copy.deepcopy(self.captured)
        item = captured["baseSha"]
        captured["baseSha"] = CommandEvidence(
            ("git", "rev-parse", "--verify", "refs/heads/other^{commit}"),
            0, self.spec["baseSha"] + "\n",
        )
        with self.assertRaisesRegex(DoctorError, "explicit run-spec baseRef"):
            self.doctor(captured=captured, host=host_for(captured))

    def test_dirty_tree_is_reported_without_mutation(self):
        captured = copy.deepcopy(self.captured)
        item = captured["status"]
        captured["status"] = CommandEvidence(item.argv, 0, " M src/file.py\n")
        host = host_for(captured)
        self.assertFalse(self.doctor(captured=captured, host=host)["workingTreeClean"])

    def test_controller_root_must_be_tracked_ignore_unindexed_and_repo_local(self):
        cases = []
        captured = copy.deepcopy(self.captured)
        item = captured["trackedController"]
        captured["trackedController"] = CommandEvidence(item.argv, 0, ".compass-builder/state.json\n")
        cases.append((captured, "Git index"))
        captured = copy.deepcopy(self.captured)
        item = captured["ignoredStateProbe"]
        captured["ignoredStateProbe"] = CommandEvidence(item.argv, 0, ".git/info/exclude:1:.compass-builder\t.compass-builder/runs/x\n")
        cases.append((captured, "tracked repository"))
        for captured, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(DoctorError, message):
                    self.doctor(captured=captured, host=host_for(captured))
        (self.repo / ".gitignore").write_text(".compass-builder/\n", encoding="utf-8")
        with self.assertRaisesRegex(DoctorError, "exact"):
            self.doctor()


if __name__ == "__main__":
    unittest.main()
