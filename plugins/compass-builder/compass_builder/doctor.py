"""Read-only host and repository preflight for Compass Builder."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .models import (
    ContractValidationError,
    canonical_json,
    validate_host_capabilities_at,
    validate_run_spec,
)


class DoctorError(ValueError):
    """A fail-closed host or repository preflight failure."""


@dataclass(frozen=True)
class CommandEvidence:
    """Captured command result; suitable for deterministic unit tests."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str = ""

    def wire(self) -> dict[str, object]:
        return {
            "argv": list(self.argv),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


Runner = Callable[[Sequence[str], Path], CommandEvidence]


COMMANDS: dict[str, tuple[str, ...]] = {
    "codexVersion": ("codex", "--version"),
    "codexExecHelp": ("codex", "exec", "--help"),
    "codexFeatures": ("codex", "features", "list"),
    "gitVersion": ("git", "--version"),
    "worktreeList": ("git", "worktree", "list", "--porcelain"),
    "repositoryRoot": ("git", "rev-parse", "--show-toplevel"),
    "baseSha": ("git", "rev-parse", "--verify", "{baseRef}^{commit}"),
    "status": ("git", "status", "--porcelain=v1", "--untracked-files=all"),
    "trackedGitignore": ("git", "ls-files", "--error-unmatch", "--", ".gitignore"),
    "trackedController": ("git", "ls-files", "--", ".compass-builder"),
    "ignoredStateProbe": (
        "git", "check-ignore", "-v", "--no-index", "--",
        ".compass-builder/runs/doctor-probe/state.json",
    ),
    "ignoredWorktreeProbe": (
        "git", "check-ignore", "-v", "--no-index", "--",
        ".compass-builder/worktrees/doctor-probe/story",
    ),
}


def _digest(value: Mapping[str, object]) -> str:
    from hashlib import sha256

    return "sha256:" + sha256(canonical_json(value)).hexdigest()


def _default_runner(argv: Sequence[str], cwd: Path) -> CommandEvidence:
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        result = subprocess.run(
            list(argv), cwd=str(cwd), check=False, capture_output=True,
            text=True, encoding="utf-8", errors="strict", shell=False,
            timeout=20, env=environment,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise DoctorError(f"unable to capture read-only evidence for {tuple(argv)!r}: {exc}") from exc
    return CommandEvidence(tuple(argv), result.returncode, result.stdout, result.stderr)


def capture_evidence(
    repo: Path, base_ref: str, *, runner: Runner = _default_runner
) -> dict[str, CommandEvidence]:
    """Capture only the bounded read-only command set."""
    captured: dict[str, CommandEvidence] = {}
    for name, template in COMMANDS.items():
        argv = tuple(part.replace("{baseRef}", base_ref) for part in template)
        evidence = runner(argv, repo)
        if tuple(evidence.argv) != argv:
            raise DoctorError(f"captured {name} evidence names a different command")
        captured[name] = evidence
    return captured


def _successful(captured: Mapping[str, CommandEvidence], name: str) -> CommandEvidence:
    evidence = captured.get(name)
    if evidence is None:
        raise DoctorError(f"missing captured evidence: {name}")
    expected = COMMANDS[name]
    if len(evidence.argv) != len(expected):
        raise DoctorError(f"captured {name} evidence names a different command")
    for actual, template in zip(evidence.argv, expected):
        if "{baseRef}" not in template and actual != template:
            raise DoctorError(f"captured {name} evidence names a different command")
    if evidence.returncode != 0:
        detail = evidence.stderr.strip()[:300]
        raise DoctorError(f"{name} evidence exited {evidence.returncode}: {detail}")
    return evidence


def _flag(text: str, short: str, long: str) -> bool:
    return bool(re.search(rf"(?<![\w-])(?:{re.escape(short)}|{re.escape(long)})(?![\w-])", text))


def _long_flag(text: str, name: str) -> bool:
    return bool(re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text))


def _derived_support(captured: Mapping[str, CommandEvidence]) -> dict[str, bool]:
    help_text = _successful(captured, "codexExecHelp").stdout
    features = _successful(captured, "codexFeatures").stdout
    multi_agent_rows = [
        " ".join(line.strip().casefold().split())
        for line in features.splitlines()
        if line.strip().casefold().split(maxsplit=1)[:1] == ["multi_agent"]
    ]
    stable_enabled = multi_agent_rows == ["multi_agent stable true"]
    return {
        "worktrees": bool(_successful(captured, "worktreeList").stdout.strip()),
        "workingDirectoryBinding": bool(re.search(r"(?<![\w-])-C(?![\w-])", help_text)),
        "structuredOutput": _long_flag(help_text, "--json") and _long_flag(help_text, "--output-schema"),
        "multiAgentDisable": _long_flag(help_text, "--disable") and stable_enabled,
    }


def _require_exec_surface(captured: Mapping[str, CommandEvidence]) -> None:
    help_text = _successful(captured, "codexExecHelp").stdout
    if not _flag(help_text, "-m", "--model"):
        raise DoctorError("codex exec help does not prove exact model selection with -m/--model")


def _require_worker_isolation_surface(captured: Mapping[str, CommandEvidence]) -> None:
    help_text = _successful(captured, "codexExecHelp").stdout
    if (
        not _long_flag(help_text, "--ignore-user-config")
        or not _long_flag(help_text, "--approve-for-me")
    ):
        raise DoctorError(
            "codex exec help does not prove the required worker isolation surface"
        )
    features = _successful(captured, "codexFeatures").stdout
    normalized_rows = [" ".join(line.strip().casefold().split()) for line in features.splitlines()]
    for feature in ("plugins", "hooks"):
        rows = [row for row in normalized_rows if row.split(maxsplit=1)[:1] == [feature]]
        if rows != [f"{feature} stable true"]:
            raise DoctorError(
                "Codex feature evidence does not prove the required worker isolation surface"
            )


def _is_reparse(path: Path) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    stat = path.lstat()
    return path.is_symlink() or bool(
        getattr(stat, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _require_controller_roots(repo: Path, captured: Mapping[str, CommandEvidence]) -> None:
    gitignore = repo / ".gitignore"
    if _successful(captured, "trackedGitignore").stdout.strip().replace("\\", "/") != ".gitignore":
        raise DoctorError("root .gitignore is not tracked")
    try:
        rules = gitignore.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise DoctorError("unable to read tracked root .gitignore") from exc
    if "/.compass-builder/" not in {line.strip() for line in rules}:
        raise DoctorError("tracked root .gitignore lacks exact '/.compass-builder/' rule")
    if _successful(captured, "trackedController").stdout.strip():
        raise DoctorError(".compass-builder content is present in the Git index")
    for name in ("ignoredStateProbe", "ignoredWorktreeProbe"):
        output = _successful(captured, name).stdout.strip().replace("\\", "/")
        metadata = output.split("\t", 1)[0].casefold()
        if not metadata.startswith(".gitignore:"):
            raise DoctorError(f"{name} is not ignored by the tracked repository .gitignore")
    root = repo / ".compass-builder"
    for candidate in (root, root / "runs", root / "worktrees"):
        try:
            candidate.resolve(strict=False).relative_to(repo.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise DoctorError("controller roots must remain inside the controller checkout") from exc
        current = candidate
        while current != repo and current.exists():
            if _is_reparse(current):
                raise DoctorError(f"controller root contains a reparse point: {current}")
            current = current.parent


def doctor_from_captured(
    repo: Path,
    run_spec: Mapping[str, object],
    native_capabilities: Mapping[str, object],
    captured: Mapping[str, CommandEvidence],
    *,
    planning_timestamp: str,
) -> dict[str, object]:
    """Validate captured host evidence without running commands or mutating Git."""
    missing = sorted(set(COMMANDS) - set(captured))
    extra = sorted(set(captured) - set(COMMANDS))
    if missing or extra:
        raise DoctorError(
            f"captured evidence set is not closed (missing={missing}, extra={extra})"
        )
    try:
        spec = validate_run_spec(run_spec)
        host = validate_host_capabilities_at(native_capabilities, planning_timestamp)
    except ContractValidationError as exc:
        raise DoctorError(str(exc)) from exc
    repo = repo.resolve(strict=True)
    recorded_root = Path(_successful(captured, "repositoryRoot").stdout.strip()).resolve(strict=False)
    if os.path.normcase(str(recorded_root)) != os.path.normcase(str(repo)):
        raise DoctorError("Git repository root does not match the requested controller checkout")
    expected_base_argv = tuple(
        part.replace("{baseRef}", spec["baseRef"]) for part in COMMANDS["baseSha"]
    )
    if _successful(captured, "baseSha").argv != expected_base_argv:
        raise DoctorError("base SHA evidence did not resolve the explicit run-spec baseRef")
    worktree_paths = [
        Path(line.removeprefix("worktree ")).resolve(strict=False)
        for line in _successful(captured, "worktreeList").stdout.splitlines()
        if line.startswith("worktree ")
    ]
    if not any(os.path.normcase(str(path)) == os.path.normcase(str(repo)) for path in worktree_paths):
        raise DoctorError("requested controller checkout is absent from Git worktree evidence")
    base_sha = _successful(captured, "baseSha").stdout.strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{40}", base_sha) or base_sha != spec["baseSha"]:
        raise DoctorError("explicit baseRef did not resolve to the immutable run-spec baseSha")
    codex_version = _successful(captured, "codexVersion").stdout.strip()
    git_version = _successful(captured, "gitVersion").stdout.strip()
    if codex_version != host["codexVersion"]:
        raise DoctorError("native capability Codex version is inconsistent with CLI evidence")
    if git_version != host["gitVersion"]:
        raise DoctorError("native capability Git version is inconsistent with CLI evidence")
    if spec["exactModel"] != host["selectedModel"]:
        raise DoctorError("native capability snapshot names a different selected model")
    for field in ("hostConcurrencyCeiling", "userConcurrencyCeiling"):
        if spec[field] != host[field]:
            raise DoctorError(f"native capability {field} is inconsistent with the run spec")
    derived_support = _derived_support(captured)
    _require_exec_surface(captured)
    _require_worker_isolation_surface(captured)
    if derived_support != host["supports"]:
        raise DoctorError("native capability support claims are inconsistent with captured CLI/Git evidence")
    provenance = host["captureSource"].casefold().replace("-", " ")
    if "native" not in provenance or "control plane" not in provenance:
        raise DoctorError("native capability snapshot lacks invoking control-plane provenance")
    cli_names = ("codexVersion", "codexExecHelp", "codexFeatures")
    git_names = tuple(name for name in COMMANDS if name not in cli_names)
    cli_digest = _digest({name: captured[name].wire() for name in cli_names})
    git_digest = _digest({name: captured[name].wire() for name in git_names})
    if host["cliEvidenceDigest"] != cli_digest:
        raise DoctorError("native capability cliEvidenceDigest does not bind the captured raw CLI evidence")
    if host["gitEvidenceDigest"] != git_digest:
        raise DoctorError("native capability gitEvidenceDigest does not bind the captured raw Git evidence")
    _require_controller_roots(repo, captured)
    clean = not bool(_successful(captured, "status").stdout.strip())
    return {
        "hostCapabilities": host,
        "hostEvidenceDigest": _digest(host),
        "planningTimestamp": planning_timestamp,
        "resolvedBaseSha": base_sha,
        "workingTreeClean": clean,
        "rawEvidenceDigests": {"cli": cli_digest, "git": git_digest},
    }


def run_doctor(
    repo: Path,
    run_spec: Mapping[str, object],
    native_capabilities: Mapping[str, object],
    *,
    planning_timestamp: str,
    runner: Runner = _default_runner,
) -> dict[str, object]:
    spec = validate_run_spec(run_spec)
    captured = capture_evidence(repo.resolve(strict=True), spec["baseRef"], runner=runner)
    return doctor_from_captured(
        repo, spec, native_capabilities, captured, planning_timestamp=planning_timestamp
    )
