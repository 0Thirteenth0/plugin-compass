"""Run deterministic, repository-local validation profiles.

The harness intentionally depends only on the Python interpreter running this file and
Git for profiles that include ``git diff --check``.  Workstation-specific release gates
are documented in ``docs/VALIDATION.md`` and are never inferred as passing here.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COMPASS_BUILDER_ROOT = REPOSITORY_ROOT / "plugins" / "compass-builder"
if str(COMPASS_BUILDER_ROOT) not in sys.path:
    sys.path.insert(0, str(COMPASS_BUILDER_ROOT))

from compass_builder.process_runner import (  # noqa: E402
    BoundedProcessError, MAX_CAPTURE_BYTES, run_bounded_text,
)


SCHEMA_VERSION = "plugin-compass.repo-harness.v1"
BEHAVIOR_PROFILES = frozenset({"contracts", "unit", "integration", "audit"})
FULL_SUITE_ID = "suite.full"
PYTHON_CHECK_TIMEOUT_SECONDS = 900.0
GIT_CHECK_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class CheckSpec:
    check_id: str
    command: str
    path: str
    case: str
    corrective_direction: str
    argv: tuple[str, ...] | None = None
    required_command: str | None = None
    required_path: str | None = None
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    status: str
    command: str
    path: str
    case: str
    reason: str
    corrective_direction: str
    output: str = ""

    def as_json(self) -> dict[str, str]:
        return {
            "case": self.case,
            "command": self.command,
            "correctiveDirection": self.corrective_direction,
            "id": self.check_id,
            "path": self.path,
            "reason": self.reason,
            "status": self.status,
        }


@dataclass(frozen=True)
class ProfileResult:
    profile: str
    checks: tuple[CheckResult, ...]

    @property
    def status(self) -> str:
        if any(check.status == "failed" for check in self.checks):
            return "failed"
        if any(check.status == "skipped" for check in self.checks):
            return "degraded"
        return "passed"

    @property
    def full_validation(self) -> bool:
        return (
            self.profile in BEHAVIOR_PROFILES
            and self.status == "passed"
            and bool(self.checks)
            and self.checks[-1].check_id == FULL_SUITE_ID
            and self.checks[-1].status == "passed"
        )

    @property
    def validation_level(self) -> str:
        if self.status == "failed":
            return "failed"
        if self.status == "degraded":
            return "degraded"
        if self.full_validation:
            return "full"
        return "focused"

    @property
    def exit_code(self) -> int:
        return {"passed": 0, "failed": 1, "degraded": 2}[self.status]

    def as_json(self) -> dict[str, object]:
        return {
            "checks": [check.as_json() for check in self.checks],
            "fullValidation": self.full_validation,
            "profile": self.profile,
            "schemaVersion": SCHEMA_VERSION,
            "status": self.status,
            "validationLevel": self.validation_level,
        }


def _python_check(
    check_id: str,
    modules: Sequence[str],
    path: str,
    case: str,
) -> CheckSpec:
    display = "python -m unittest " + " ".join(modules) + " -v"
    return CheckSpec(
        check_id=check_id,
        command=display,
        path=path,
        case=case,
        corrective_direction=f"run `{display}` directly, correct the reported case, and rerun the profile",
        argv=(sys.executable, "-m", "unittest", *modules, "-v"),
        timeout_seconds=PYTHON_CHECK_TIMEOUT_SECONDS,
    )


def _full_suite_check() -> CheckSpec:
    display = "python -m unittest discover -s tests -v"
    return CheckSpec(
        check_id=FULL_SUITE_ID,
        command=display,
        path="tests/",
        case="repository-wide Python behavior suite",
        corrective_direction=f"run `{display}` directly, fix every failure, and rerun the profile",
        argv=(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"),
        timeout_seconds=PYTHON_CHECK_TIMEOUT_SECONDS,
    )


def _path_check(check_id: str, relative_path: str, case: str) -> CheckSpec:
    return CheckSpec(
        check_id=check_id,
        command=f"check-path {relative_path}",
        path=relative_path,
        case=case,
        corrective_direction=f"restore `{relative_path}` as a UTF-8 repository file and rerun the profile",
        required_path=relative_path,
    )


def _git_diff_check() -> CheckSpec:
    return CheckSpec(
        check_id="repository.diff-check",
        command="git diff --check",
        path="repository worktree",
        case="whitespace errors and unresolved conflict markers in the Git diff",
        corrective_direction="install Git if needed, correct every reported diff error, and rerun the profile",
        argv=("git", "diff", "--check"),
        required_command="git",
        timeout_seconds=GIT_CHECK_TIMEOUT_SECONDS,
    )


def build_profiles() -> dict[str, tuple[CheckSpec, ...]]:
    """Return the closed, deterministically ordered validation profile map."""

    documentation = (
        _path_check("docs.validation", "docs/VALIDATION.md", "repository validation guide"),
        _path_check(
            "docs.builder-contract",
            "docs/COMPASS_BUILDER_CONTRACT.md",
            "Compass Builder product and validation contract",
        ),
        _path_check(
            "docs.builder-benchmark",
            "docs/COMPASS_BUILDER_BENCHMARK.md",
            "Compass Builder paired benchmark protocol and evidence status",
        ),
        _path_check("docs.product-contract", "docs/PRODUCT_CONTRACT.md", "Plugin Compass product contract"),
        _path_check("docs.technical-design", "docs/TECHNICAL_DESIGN.md", "repository technical design"),
        _path_check("docs.security", "SECURITY.md", "repository security policy"),
        _path_check("docs.readme", "README.md", "repository entry-point documentation"),
    )
    contracts = _python_check(
        "contracts.models-and-schemas",
        ("tests.test_builder_models",),
        "tests/test_builder_models.py; plugins/compass-builder/schemas/",
        "versioned closed schemas, fixtures, model validators, and canonical bytes",
    )
    unit = _python_check(
        "unit.builder-core",
        (
            "tests.test_builder_doctor",
            "tests.test_builder_planner",
            "tests.test_builder_state",
            "tests.test_builder_lease",
            "tests.test_builder_handoff",
            "tests.test_builder_launcher",
            "tests.test_builder_compare",
            "tests.test_builder_benchmark_runner",
            "tests.test_builder_skill_contract",
        ),
        "tests/test_builder_*.py",
        "focused deterministic Compass Builder unit behavior",
    )
    integration = _python_check(
        "integration.builder-git",
        (
            "tests.test_builder_verifier",
            "tests.test_builder_integrator",
            "tests.test_builder_cleanup",
            "tests.integration.test_builder_worktrees",
        ),
        "tests/test_builder_verifier.py; tests/test_builder_integrator.py; tests/test_builder_cleanup.py; tests/integration/test_builder_worktrees.py",
        "temporary-repository verification, isolated workers, serial integration, and fail-closed cleanup",
    )
    harness_self_test = _python_check(
        "audit.harness-self-tests",
        ("tests.test_repo_harness",),
        "tests/test_repo_harness.py",
        "profile ordering, stable rendering, skip truthfulness, and fail-closed exit behavior",
    )
    full_suite = _full_suite_check()
    diff_check = _git_diff_check()

    return {
        "audit": documentation + (contracts, integration, harness_self_test, diff_check, full_suite),
        "contracts": (contracts, full_suite),
        "docs": documentation + (diff_check,),
        "integration": (integration, full_suite),
        "unit": (unit, full_suite),
    }


def _path_result(spec: CheckSpec, repository_root: Path) -> CheckResult:
    target = repository_root / str(spec.required_path)
    if not target.is_file():
        return CheckResult(
            spec.check_id,
            "failed",
            spec.command,
            spec.path,
            spec.case,
            "required repository file is missing or is not a regular file",
            spec.corrective_direction,
        )
    try:
        target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return CheckResult(
            spec.check_id,
            "failed",
            spec.command,
            spec.path,
            spec.case,
            f"required repository file is not readable UTF-8 ({type(exc).__name__})",
            spec.corrective_direction,
        )
    return CheckResult(
        spec.check_id,
        "passed",
        spec.command,
        spec.path,
        spec.case,
        "required repository file is present and readable as UTF-8",
        spec.corrective_direction,
    )


def _bounded_text(value: object) -> str:
    if value is None:
        return ""
    raw = value if isinstance(value, bytes) else str(value).encode("utf-8")
    return bytes(raw[:MAX_CAPTURE_BYTES]).decode("utf-8", errors="replace")


def _process_output(completed: subprocess.CompletedProcess[object]) -> str:
    pieces = []
    for value in (completed.stdout, completed.stderr):
        if value:
            pieces.append(_bounded_text(value).rstrip())
    return "\n".join(pieces)


def _bounded_failure(spec: CheckSpec, error: BoundedProcessError) -> CheckResult:
    message = str(error).casefold()
    diagnostic = _process_output(
        subprocess.CompletedProcess(spec.argv or (), 1, error.stdout, error.stderr)
    )
    if "timed out" in message or "wall bound" in message:
        reason = f"command exceeded its {spec.timeout_seconds:g}-second timeout"
        corrective = (
            "inspect the command for a hang, reduce its runtime, or raise the explicit "
            "timeout only with measured evidence, then rerun the profile"
        )
    elif "exceeded" in message and any(
        marker in message for marker in ("output", "stdout", "stderr")
    ):
        reason = (
            f"command output exceeded the {MAX_CAPTURE_BYTES}-byte per-stream capture bound"
        )
        corrective = (
            f"reduce command output below {MAX_CAPTURE_BYTES} bytes per stream or narrow "
            "the check, then rerun the profile"
        )
    else:
        reason = "bounded command execution failed"
        corrective = spec.corrective_direction
    return CheckResult(
        spec.check_id,
        "failed",
        spec.command,
        spec.path,
        spec.case,
        reason,
        corrective,
        diagnostic,
    )


def execute_check(
    spec: CheckSpec,
    repository_root: Path = REPOSITORY_ROOT,
    *,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] = run_bounded_text,
    command_finder: Callable[[str], str | None] = shutil.which,
) -> CheckResult:
    if spec.required_path is not None:
        return _path_result(spec, repository_root)

    if spec.required_command is not None and command_finder(spec.required_command) is None:
        return CheckResult(
            spec.check_id,
            "skipped",
            spec.command,
            spec.path,
            spec.case,
            f"required command `{spec.required_command}` is unavailable on PATH",
            spec.corrective_direction,
        )

    assert spec.argv is not None
    if spec.timeout_seconds is None or spec.timeout_seconds <= 0:
        return CheckResult(
            spec.check_id,
            "failed",
            spec.command,
            spec.path,
            spec.case,
            "process check has no positive timeout",
            "assign a positive explicit timeout to this check and rerun the profile",
        )
    try:
        completed = process_runner(
            list(spec.argv),
            cwd=repository_root,
            timeout=spec.timeout_seconds,
            max_output_bytes=MAX_CAPTURE_BYTES,
        )
    except BoundedProcessError as exc:
        return _bounded_failure(spec, exc)
    except FileNotFoundError:
        return CheckResult(
            spec.check_id,
            "skipped",
            spec.command,
            spec.path,
            spec.case,
            "command executable is unavailable",
            spec.corrective_direction,
        )
    except Exception as exc:  # fail closed while retaining a useful command receipt
        return CheckResult(
            spec.check_id,
            "failed",
            spec.command,
            spec.path,
            spec.case,
            f"command could not run ({type(exc).__name__})",
            spec.corrective_direction,
        )

    output = _process_output(completed)
    if completed.returncode != 0:
        return CheckResult(
            spec.check_id,
            "failed",
            spec.command,
            spec.path,
            spec.case,
            f"command exited {completed.returncode}",
            spec.corrective_direction,
            output,
        )
    return CheckResult(
        spec.check_id,
        "passed",
        spec.command,
        spec.path,
        spec.case,
        "command exited 0",
        spec.corrective_direction,
    )


def execute_profile(
    profile: str,
    repository_root: Path = REPOSITORY_ROOT,
    *,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] = run_bounded_text,
    command_finder: Callable[[str], str | None] = shutil.which,
) -> ProfileResult:
    profiles = build_profiles()
    if profile not in profiles:
        raise ValueError(f"unsupported profile {profile!r}; choose one of {sorted(profiles)}")
    results = tuple(
        execute_check(
            spec,
            repository_root,
            process_runner=process_runner,
            command_finder=command_finder,
        )
        for spec in profiles[profile]
    )
    return ProfileResult(profile, results)


def render_json(result: ProfileResult) -> str:
    return json.dumps(result.as_json(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_human(result: ProfileResult) -> str:
    labels = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}
    coverage = {
        "full": "full repository validation",
        "focused": "focused repository validation",
        "degraded": "degraded validation (not full validation)",
        "failed": "failed validation (not full validation)",
    }[result.validation_level]
    lines = [
        f"Repository validation profile: {result.profile}",
        f"status: {result.status.upper()}",
        f"coverage: {coverage}",
    ]
    for check in result.checks:
        lines.extend(
            (
                f"[{labels[check.status]}] {check.check_id}",
                f"  command: {check.command}",
                f"  path/case: {check.path} :: {check.case}",
                f"  reason: {check.reason}",
            )
        )
        if check.status != "passed":
            lines.append(f"  corrective direction: {check.corrective_direction}")
        if check.output:
            lines.append("  process output:")
            lines.extend(f"    {line}" for line in check.output.splitlines())
    passed = sum(check.status == "passed" for check in result.checks)
    failed = sum(check.status == "failed" for check in result.checks)
    skipped = sum(check.status == "skipped" for check in result.checks)
    lines.append(f"summary: {passed} passed, {failed} failed, {skipped} skipped")
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=sorted(build_profiles()))
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--format", choices=("human", "json"), default="human")
    output.add_argument("--json", action="store_const", const="json", dest="format")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = execute_profile(args.profile)
    renderer = render_json if args.format == "json" else render_human
    sys.stdout.write(renderer(result))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
