from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = REPOSITORY_ROOT / "scripts" / "check_repo_harness.py"
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "validate.yml"

SPEC = importlib.util.spec_from_file_location("repo_harness_under_test", HARNESS_PATH)
assert SPEC is not None and SPEC.loader is not None
repo_harness = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = repo_harness
SPEC.loader.exec_module(repo_harness)


class FakeRunner:
    def __init__(self, returncodes: tuple[int, ...] = ()) -> None:
        self.returncodes = list(returncodes)
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, argv, **kwargs):
        copied = list(argv)
        self.calls.append((copied, dict(kwargs)))
        returncode = self.returncodes.pop(0) if self.returncodes else 0
        output = "variable failure trace" if returncode else ""
        return subprocess.CompletedProcess(copied, returncode, stdout=output, stderr="")


class RepositoryHarnessTests(unittest.TestCase):
    def test_profile_surface_is_closed_and_behavior_profiles_end_with_full_suite(self):
        profiles = repo_harness.build_profiles()
        self.assertEqual(
            ["audit", "contracts", "docs", "integration", "unit"],
            sorted(profiles),
        )
        for profile in repo_harness.BEHAVIOR_PROFILES:
            with self.subTest(profile=profile):
                self.assertEqual(repo_harness.FULL_SUITE_ID, profiles[profile][-1].check_id)
                self.assertEqual(
                    1,
                    sum(check.check_id == repo_harness.FULL_SUITE_ID for check in profiles[profile]),
                )
        self.assertNotIn(
            repo_harness.FULL_SUITE_ID,
            [check.check_id for check in profiles["docs"]],
        )
        process_checks = [
            check
            for checks in profiles.values()
            for check in checks
            if check.argv is not None
        ]
        self.assertTrue(process_checks)
        self.assertTrue(all(check.timeout_seconds and check.timeout_seconds > 0 for check in process_checks))
        self.assertEqual(
            repo_harness.GIT_CHECK_TIMEOUT_SECONDS,
            profiles["docs"][-1].timeout_seconds,
        )

    def test_processes_use_argument_arrays_and_repository_paths_with_spaces(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory(prefix="repo harness ") as temporary:
            root = Path(temporary)
            result = repo_harness.execute_profile(
                "integration",
                root,
                process_runner=runner,
                command_finder=lambda _command: "available",
            )
        self.assertEqual("passed", result.status)
        self.assertTrue(result.full_validation)
        self.assertEqual(2, len(runner.calls))
        for argv, kwargs in runner.calls:
            self.assertIsInstance(argv, list)
            self.assertEqual(sys.executable, argv[0])
            self.assertEqual(root, kwargs["cwd"])
            self.assertNotIn("shell", kwargs)
            self.assertEqual(repo_harness.PYTHON_CHECK_TIMEOUT_SECONDS, kwargs["timeout"])
            self.assertEqual(repo_harness.MAX_CAPTURE_BYTES, kwargs["max_output_bytes"])
        self.assertIn("tests.test_builder_verifier", runner.calls[0][0])
        self.assertIn("tests.integration.test_builder_worktrees", runner.calls[0][0])
        self.assertEqual("discover", runner.calls[1][0][3])

    def test_timeout_is_failed_actionable_and_json_omits_bounded_diagnostic(self):
        calls = []

        def timed_out(argv, **kwargs):
            calls.append((list(argv), dict(kwargs)))
            raise repo_harness.BoundedProcessError(
                "process timed out after 900 seconds",
                stdout=b"last bounded timeout diagnostic",
            )

        spec = repo_harness.build_profiles()["contracts"][0]
        result = repo_harness.execute_check(spec, process_runner=timed_out)
        self.assertEqual(1, len(calls))
        self.assertEqual("failed", result.status)
        self.assertIn("900-second timeout", result.reason)
        self.assertIn("hang", result.corrective_direction)
        self.assertEqual("last bounded timeout diagnostic", result.output)
        rendered = repo_harness.render_json(repo_harness.ProfileResult("contracts", (result,)))
        self.assertNotIn("last bounded timeout diagnostic", rendered)
        self.assertEqual(repo_harness.PYTHON_CHECK_TIMEOUT_SECONDS, calls[0][1]["timeout"])
        self.assertEqual(repo_harness.MAX_CAPTURE_BYTES, calls[0][1]["max_output_bytes"])

    def test_output_overflow_is_failed_actionable_and_diagnostic_is_bounded(self):
        calls = []
        oversized = b"x" * (repo_harness.MAX_CAPTURE_BYTES + 17)

        def overflowed(argv, **kwargs):
            calls.append((list(argv), dict(kwargs)))
            raise repo_harness.BoundedProcessError(
                f"process output exceeded {repo_harness.MAX_CAPTURE_BYTES} bytes",
                stdout=oversized,
                stderr=b"bounded stderr",
            )

        spec = repo_harness.build_profiles()["contracts"][0]
        result = repo_harness.execute_check(spec, process_runner=overflowed)
        self.assertEqual(1, len(calls))
        self.assertEqual("failed", result.status)
        self.assertIn("per-stream capture bound", result.reason)
        self.assertIn("reduce command output", result.corrective_direction)
        stdout, stderr = result.output.split("\n", 1)
        self.assertEqual(repo_harness.MAX_CAPTURE_BYTES, len(stdout.encode("utf-8")))
        self.assertEqual("bounded stderr", stderr)
        self.assertLessEqual(
            len(result.output.encode("utf-8")),
            repo_harness.MAX_CAPTURE_BYTES * 2 + 1,
        )

    def test_focused_failure_still_runs_full_suite_and_reports_actionable_context(self):
        runner = FakeRunner((7, 0))
        result = repo_harness.execute_profile(
            "contracts",
            process_runner=runner,
            command_finder=lambda _command: "available",
        )
        self.assertEqual(2, len(runner.calls))
        self.assertEqual("failed", result.status)
        self.assertEqual(1, result.exit_code)
        self.assertFalse(result.full_validation)
        failed = result.checks[0]
        self.assertEqual("failed", failed.status)
        self.assertEqual("command exited 7", failed.reason)
        self.assertIn("python -m unittest tests.test_builder_models -v", failed.command)
        self.assertIn("tests/test_builder_models.py", failed.path)
        self.assertIn("schemas", failed.case)
        self.assertIn("rerun the profile", failed.corrective_direction)
        human = repo_harness.render_human(result)
        self.assertIn("failed validation (not full validation)", human)
        self.assertIn("process output", human)

    def test_unavailable_command_is_a_recorded_nonzero_degraded_skip(self):
        runner = FakeRunner()
        result = repo_harness.execute_profile(
            "audit",
            process_runner=runner,
            command_finder=lambda command: None if command == "git" else "available",
        )
        self.assertEqual("degraded", result.status)
        self.assertEqual(2, result.exit_code)
        self.assertFalse(result.full_validation)
        skipped = [check for check in result.checks if check.status == "skipped"]
        self.assertEqual(1, len(skipped))
        self.assertEqual("git diff --check", skipped[0].command)
        self.assertIn("unavailable", skipped[0].reason)
        self.assertIn("install Git", skipped[0].corrective_direction)
        self.assertIn("degraded validation (not full validation)", repo_harness.render_human(result))

    def test_required_path_failure_names_path_case_reason_and_correction(self):
        spec = repo_harness.CheckSpec(
            check_id="docs.missing",
            command="check-path docs/MISSING.md",
            path="docs/MISSING.md",
            case="missing documentation fixture",
            corrective_direction="restore the fixture",
            required_path="docs/MISSING.md",
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = repo_harness.execute_check(spec, Path(temporary))
        self.assertEqual("failed", result.status)
        self.assertEqual("docs/MISSING.md", result.path)
        self.assertEqual("missing documentation fixture", result.case)
        self.assertIn("missing", result.reason)
        self.assertEqual("restore the fixture", result.corrective_direction)

    def test_json_is_stable_and_omits_unstable_process_output(self):
        runner_one = FakeRunner((3, 0))
        runner_two = FakeRunner((3, 0))
        first = repo_harness.execute_profile(
            "contracts",
            process_runner=runner_one,
            command_finder=lambda _command: "available",
        )
        second = repo_harness.execute_profile(
            "contracts",
            process_runner=runner_two,
            command_finder=lambda _command: "available",
        )
        first_json = repo_harness.render_json(first)
        self.assertEqual(first_json, repo_harness.render_json(second))
        payload = json.loads(first_json)
        self.assertEqual("plugin-compass.repo-harness.v1", payload["schemaVersion"])
        self.assertEqual("failed", payload["status"])
        self.assertFalse(payload["fullValidation"])
        self.assertNotIn("variable failure trace", first_json)

    def test_python_suite_has_only_stdlib_and_repository_local_import_roots(self):
        source_roots = (
            REPOSITORY_ROOT / "tests",
            REPOSITORY_ROOT / "scripts",
            REPOSITORY_ROOT / "plugins" / "plugin-compass",
            REPOSITORY_ROOT / "plugins" / "compass-builder",
        )
        source_files = sorted({
            path
            for source_root in source_roots
            for path in source_root.rglob("*.py")
            if "__pycache__" not in path.parts
        })
        for prior_dependency_owner in (
            REPOSITORY_ROOT / "tests" / "test_builder_launcher.py",
            REPOSITORY_ROOT / "tests" / "test_builder_state.py",
        ):
            self.assertIn(prior_dependency_owner, source_files)

        repository_local_roots = {"compass_builder", "plugin_compass", "tests"}
        allowed_roots = set(sys.stdlib_module_names) | repository_local_roots | {"__future__"}

        def absolute_import_roots(source: str, filename: str) -> set[str]:
            tree = ast.parse(source, filename=filename)
            roots: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots.update(alias.name.partition(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    roots.add(node.module.partition(".")[0])
            return roots

        prior_import_probe = (
            "import jsonschema\n"
            "from referencing import Registry\n"
        )
        self.assertEqual(
            {"jsonschema", "referencing"},
            absolute_import_roots(prior_import_probe, "prior-dependency-probe.py") - allowed_roots,
        )

        undeclared: dict[str, list[str]] = {}
        for path in source_files:
            roots = absolute_import_roots(path.read_text(encoding="utf-8"), str(path))
            relative = path.relative_to(REPOSITORY_ROOT).as_posix()
            for root in sorted(roots - allowed_roots):
                undeclared.setdefault(root, []).append(relative)
        self.assertEqual(
            {},
            undeclared,
            "repository Python sources contain undeclared third-party absolute imports",
        )

    def test_ci_uses_only_repository_python_git_and_explicit_test_commands(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        required = (
            "python -m unittest tests.test_builder_models -v",
            "python -m unittest discover -s tests -v",
            "python -m unittest tests.test_builder_compare tests.test_builder_benchmark_runner -v",
            "python -m unittest tests.test_builder_verifier tests.test_builder_integrator tests.test_builder_cleanup tests.integration.test_builder_worktrees -v",
            "python -m unittest tests.test_repo_harness -v",
            "git diff --check",
        )
        for command in required:
            with self.subTest(command=command):
                self.assertIn(command, workflow)
        action_refs = dict(re.findall(
            r"uses:\s+(actions/(?:checkout|setup-python))@([^\s#]+)",
            workflow,
        ))
        self.assertEqual(
            {
                "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
                "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
            },
            action_refs,
        )
        for action, reference in action_refs.items():
            with self.subTest(action=action):
                self.assertRegex(reference, r"^[0-9a-f]{40}$")
        self.assertIn(
            "uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0",
            workflow,
        )
        self.assertIn(
            "uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5.6.0",
            workflow,
        )
        self.assertRegex(
            workflow,
            r"actions/checkout@[0-9a-f]{40} # v4\.4\.0\s+with:\s+persist-credentials: false",
        )
        self.assertNotRegex(
            workflow,
            r"uses:\s+actions/(?:checkout|setup-python)@v[0-9]",
        )
        lowered = workflow.casefold()
        for forbidden in ("plugin-creator", "skill-creator", "plugin-scanner", "\\.codex\\", "\\.local\\"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)


if __name__ == "__main__":
    unittest.main()
