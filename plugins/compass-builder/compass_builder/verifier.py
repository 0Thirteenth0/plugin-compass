"""Git-object-derived worker verification for Compass Builder."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence

from .git_environment import GitEnvironment, validate_git_environment
from .git_objects import GitObjectError, read_raw_commit
from .launcher import LaunchError, build_worker_prompt, validate_launch_record
from .models import canonical_json, validate_run_structure_bindings, validate_worker_receipt
from .process_runner import (
    BoundedProcessError, completed_text, parse_command, run_bounded, run_bounded_text,
)
from .state import StateError, StateStore
from .secure_files import is_reparse, read_no_follow


class VerificationError(ValueError):
    """Worker evidence does not agree with the repository and launch contract."""


CommandRunner = Callable[[Sequence[str], Path, Mapping[str, str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class VerifiedWorker:
    run_id: str
    story_id: str
    branch: str
    worktree: Path
    base_sha: str
    head_sha: str
    changed_files: tuple[dict[str, object], ...]
    check_evidence: tuple[dict[str, object], ...]
    evidence_digest: str

    def wire(self) -> dict[str, object]:
        return {
            "schemaVersion": "compass-builder.worker-verification.v1",
            "runId": self.run_id,
            "storyId": self.story_id,
            "branch": self.branch,
            "worktree": str(self.worktree),
            "baseSha": self.base_sha,
            "headSha": self.head_sha,
            "changedFiles": [dict(item) for item in self.changed_files],
            "checks": [dict(item) for item in self.check_evidence],
            "evidenceDigest": self.evidence_digest,
        }


def load_controller_launch_record(store: StateStore, story_id: str) -> dict[str, object]:
    """Read one deterministic controller-owned launch record without following links."""

    store.registered_worktree(story_id)
    directory = store.run_root / "launch-records"
    path = directory / f"{story_id}.json"
    try:
        raw = read_no_follow(
            path, store.control_root, label="controller-owned worker launch record",
            max_bytes=1_048_576,
        )
        value = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise VerificationError(f"controller-owned launch record is unavailable: {exc}") from exc
    if not isinstance(value, dict):
        raise VerificationError("controller-owned launch record must be a JSON object")
    return value


def _run_git(
    worktree: Path, environment: GitEnvironment, arguments: Sequence[str], *, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    bundle = validate_git_environment(environment)
    try:
        result = run_bounded(
            ["git", "--no-pager", "-C", str(worktree), *arguments],
            environment=bundle.environment,
        )
    except BoundedProcessError as exc:
        raise VerificationError(f"bounded Git inspection failed: {exc}") from exc
    if check and result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise VerificationError(f"Git inspection failed for {arguments[0]}: {detail}")
    return result


def _canonical_path(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise VerificationError(f"{field} is not a repository-relative path")
    text = value.replace("\\", "/")
    path = PurePosixPath(text)
    if path.is_absolute() or text.startswith("/") or any(part in {"", ".", ".."} for part in path.parts):
        raise VerificationError(f"{field} is not a canonical repository-relative path")
    canonical = "/".join(path.parts)
    if canonical.endswith("/"):
        raise VerificationError(f"{field} has an invalid trailing separator")
    return canonical


def _path_key(value: str) -> str:
    return _canonical_path(value, "Git path").casefold()


def _within(path: str, scope: str) -> bool:
    candidate = _path_key(path)
    boundary = _path_key(scope)
    return candidate == boundary or candidate.startswith(boundary + "/")


def _decode_z(output: bytes) -> list[str]:
    try:
        values = output.decode("utf-8", errors="strict").split("\x00")
    except UnicodeDecodeError as exc:
        raise VerificationError("Git emitted a non-UTF-8 path") from exc
    return values[:-1] if values and values[-1] == "" else values


def _changed_files(
    worktree: Path, environment: GitEnvironment, base_sha: str, head_sha: str
) -> tuple[dict[str, object], ...]:
    values = _decode_z(_run_git(
        worktree, environment,
        ["diff", "--name-status", "-z", "--find-renames", base_sha, head_sha, "--"],
    ).stdout)
    changed: list[dict[str, object]] = []
    index = 0
    while index < len(values):
        status = values[index]
        index += 1
        code = status[:1]
        if code in {"R", "C"}:
            if index + 1 >= len(values):
                raise VerificationError("Git rename record is truncated")
            source, target = values[index], values[index + 1]
            index += 2
            if code == "C":
                raise VerificationError("copy records are outside the MVP worker contract")
            changed.append({
                "path": _canonical_path(target, "renamed path"),
                "sourcePath": _canonical_path(source, "rename source"),
                "changeType": "renamed",
            })
        else:
            if index >= len(values):
                raise VerificationError("Git change record is truncated")
            path = _canonical_path(values[index], "changed path")
            index += 1
            kinds = {"A": "added", "M": "modified", "D": "deleted"}
            if code not in kinds:
                raise VerificationError(f"unsupported Git change type {status!r}")
            changed.append({"path": path, "sourcePath": None, "changeType": kinds[code]})
    seen: set[str] = set()
    for item in changed:
        for field in ("sourcePath", "path"):
            value = item[field]
            if value is None:
                continue
            key = _path_key(str(value))
            if key in seen:
                raise VerificationError("changed paths contain a case/separator alias")
            seen.add(key)
    return tuple(changed)


def _mode_at(
    worktree: Path, environment: GitEnvironment, sha: str, path: str
) -> str | None:
    output = _run_git(worktree, environment, ["ls-tree", "-z", sha, "--", path]).stdout
    if not output:
        return None
    records = _decode_z(output)
    if len(records) != 1 or "\t" not in records[0]:
        raise VerificationError(f"Git tree mode for {path!r} is ambiguous")
    return records[0].split(" ", 1)[0]


def _require_regular_modes(
    worktree: Path,
    environment: GitEnvironment,
    base_sha: str,
    head_sha: str,
    changed: Sequence[Mapping[str, object]],
) -> None:
    for item in changed:
        candidates = []
        if item["sourcePath"] is not None:
            candidates.append((base_sha, str(item["sourcePath"])))
        elif item["changeType"] == "deleted":
            candidates.append((base_sha, str(item["path"])))
        if item["changeType"] != "deleted":
            candidates.append((head_sha, str(item["path"])))
        for sha, path in candidates:
            mode = _mode_at(worktree, environment, sha, path)
            if mode in {"120000", "160000"}:
                kind = "symlink" if mode == "120000" else "submodule"
                raise VerificationError(f"changed {kind} path is forbidden: {path}")
            if mode is None or not mode.startswith("100"):
                raise VerificationError(f"changed path is not a regular Git blob: {path}")


def _clean(worktree: Path, environment: GitEnvironment) -> bool:
    return not _run_git(
        worktree, environment,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
    ).stdout


def _registered_worktrees(
    repository: Path, environment: GitEnvironment
) -> dict[Path, dict[str, str]]:
    fields = _decode_z(_run_git(
        repository, environment, ["worktree", "list", "--porcelain", "-z"]
    ).stdout)
    records: dict[Path, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for field in fields:
        key, _, value = field.partition(" ")
        if key == "worktree":
            if current is not None:
                records[Path(current["worktree"]).resolve(strict=False)] = current
            current = {"worktree": value}
        elif current is not None:
            current[key] = value
    if current is not None:
        records[Path(current["worktree"]).resolve(strict=False)] = current
    return records


def _require_owned_checkout(
    root: Path,
    primary_common_git_dir: Path,
    worktree: Path,
    environment: GitEnvironment,
    branch: str,
    head_sha: str,
) -> None:
    """Accept an exact legacy linked worktree or an object-isolated local clone."""

    common_text = _run_git(
        worktree, environment,
        ["rev-parse", "--path-format=absolute", "--git-common-dir"],
    ).stdout.decode("utf-8", errors="strict").strip()
    common = Path(common_text).resolve(strict=True)
    registry = _registered_worktrees(root, environment).get(worktree)
    if common == primary_common_git_dir:
        if (
            registry is None
            or registry.get("branch") != f"refs/heads/{branch}"
            or registry.get("HEAD") != head_sha
        ):
            raise VerificationError("worker is not an exact registered git worktree member")
        return

    git_dir_text = _run_git(
        worktree, environment,
        ["rev-parse", "--path-format=absolute", "--absolute-git-dir"],
    ).stdout.decode("utf-8", errors="strict").strip()
    git_dir = Path(git_dir_text).resolve(strict=True)
    expected_git_dir = (worktree / ".git").resolve(strict=True)
    if (
        common != expected_git_dir
        or git_dir != expected_git_dir
        or not expected_git_dir.is_dir()
        or is_reparse(expected_git_dir)
        or registry is not None
    ):
        raise VerificationError("worker checkout is not an exact isolated Git clone")
    remotes = _run_git(worktree, environment, ["remote"]).stdout.decode(
        "utf-8", errors="strict"
    ).splitlines()
    if remotes:
        raise VerificationError("worker clone retained a remote during isolated execution")
    if _run_git(
        worktree, environment, ["rev-parse", "--is-shallow-repository"]
    ).stdout.decode("ascii").strip() != "false":
        raise VerificationError("worker clone must contain complete Git history")
    alternates = expected_git_dir / "objects" / "info" / "alternates"
    if alternates.exists() or alternates.is_symlink():
        raise VerificationError("worker clone may not use alternate object storage")


def _default_command_runner(
    argv: Sequence[str], cwd: Path, environment: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    return run_bounded_text(list(argv), cwd=cwd, environment=environment)


def _check_digest(command: str, result: subprocess.CompletedProcess[str]) -> str:
    payload = json.dumps({
        "command": command, "returnCode": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def verify_worker(
    repository: Path,
    run_spec: Mapping[str, object],
    wave_plan: Mapping[str, object],
    receipt: Mapping[str, object],
    launch_record: Mapping[str, object],
    git_environment: GitEnvironment,
    *,
    command_runner: CommandRunner | None = None,
    verify_before_import: bool = False,
) -> VerifiedWorker:
    """Verify one worker solely from launch bindings, Git objects, and fresh checks."""

    try:
        spec, plan, _ = validate_run_structure_bindings(run_spec, wave_plan)
        claimed = validate_worker_receipt(receipt)
        launch = validate_launch_record(launch_record)
    except (ValueError, LaunchError) as exc:
        raise VerificationError(f"worker contract is invalid: {exc}") from exc
    base_sha, head_sha = str(claimed["baseSha"]), str(claimed["headSha"])
    if claimed["commitSha"] != head_sha:
        raise VerificationError("receipt commit SHA does not equal its worker head")
    store = StateStore(repository, spec, plan, git_environment)
    root = store.repository.root
    story_id = str(claimed["storyId"])
    registered_worktree = store.registered_worktree(story_id)
    try:
        worktree = Path(str(claimed["worktree"])).resolve(strict=True)
    except OSError as exc:
        raise VerificationError(f"worker checkout identity is unavailable: {exc}") from exc
    if root == worktree:
        raise VerificationError("worker receipt points at the primary integration checkout")
    if worktree != registered_worktree:
        raise VerificationError("receipt worktree is not the controller-registered story worktree")
    try:
        raw_head = read_raw_commit(
            worktree if verify_before_import else root,
            head_sha, git_environment.environment, expected_parent_count=1,
        )
    except GitObjectError as exc:
        raise VerificationError(f"worker commit object is invalid: {exc}") from exc
    if raw_head.parents != (base_sha,):
        raise VerificationError(
            "worker range must contain exactly one non-merge commit directly parented by base"
        )
    try:
        durable_state = store.load()
    except StateError as exc:
        raise VerificationError(f"durable controller ownership is invalid: {exc}") from exc
    current_entries = durable_state["waves"][durable_state["currentWaveIndex"]]["branches"]
    ledger_matches = [item for item in current_entries if item["storyId"] == story_id]
    ledger_tuple = None if len(ledger_matches) != 1 else (
        ledger_matches[0]["workerState"], ledger_matches[0]["verificationState"],
        ledger_matches[0]["integrationState"],
    )
    eligible = (
        durable_state["state"] == "wave-workers-complete"
        and ledger_tuple == ("complete", "pending", "pending")
    ) or (
        durable_state["state"] == "wave-merging"
        and ledger_tuple == ("complete", "verified", "worker-verified")
    )
    if not eligible:
        raise VerificationError("worker is not eligible in the current durable verification ledger")
    stories = [item for item in spec["stories"] if item["id"] == story_id]
    planned = [item for item in plan["stories"] if item["storyId"] == story_id]
    if len(stories) != 1 or len(planned) != 1:
        raise VerificationError("worker story is not uniquely bound by the immutable plan")
    story, planned_story = stories[0], planned[0]
    expected = {
        "runId": spec["runId"], "storyId": story_id,
        "branch": planned_story["branch"], "worktree": str(registered_worktree),
        "exactModel": spec["exactModel"], "effort": launch["effort"],
        "baseSha": durable_state["waves"][durable_state["currentWaveIndex"]]["startExpectedSha"],
    }
    launch_expected = {
        "runId": expected["runId"], "storyId": story_id,
        "branch": expected["branch"], "worktree": str(worktree),
        "exactModel": expected["exactModel"], "effort": expected["effort"],
        "workerStartSha": expected["baseSha"],
    }
    for field, value in launch_expected.items():
        actual = launch[field]
        if field == "worktree":
            actual = str(Path(str(actual)).resolve(strict=True))
        if actual != value:
            raise VerificationError(f"launch record {field} does not match the plan/receipt")
    if launch["gitEnvironmentDigest"] != validate_git_environment(git_environment).digest:
        raise VerificationError("launch record does not bind the Git environment used for verification")
    if (
        launch["handoffDigest"] != planned_story["handoffDigest"]
        or launch["hostEvidenceDigest"] != plan["hostEvidenceDigest"]
        or launch["initialRecommendedEffort"] != planned_story["recommendedEffort"]
    ):
        raise VerificationError("launch record does not bind immutable plan evidence")
    expected_prompt_digest = "sha256:" + hashlib.sha256(
        build_worker_prompt(spec, story_id=story_id).encode("utf-8")
    ).hexdigest()
    if launch["promptDigest"] != expected_prompt_digest:
        raise VerificationError("launch prompt digest does not bind the immutable story contract")
    for field, value in expected.items():
        actual = claimed[field]
        if field == "worktree":
            actual = str(Path(str(actual)).resolve(strict=True))
        if actual != value:
            raise VerificationError(f"receipt {field} does not match Git/launch planning evidence")

    actual_branch = _run_git(
        worktree, git_environment, ["rev-parse", "--verify", f"refs/heads/{claimed['branch']}"],
    ).stdout.decode("ascii").strip()
    actual_head = _run_git(worktree, git_environment, ["rev-parse", "HEAD"]).stdout.decode("ascii").strip()
    if actual_branch != head_sha or actual_head != head_sha:
        raise VerificationError("receipt SHA is stale or the registered branch/worktree HEAD drifted")
    _require_owned_checkout(
        root, store.repository.common_git_dir, worktree, git_environment,
        str(claimed["branch"]), head_sha,
    )
    if not _clean(worktree, git_environment):
        raise VerificationError("worker worktree is dirty, including tracked or untracked files")

    changed = _changed_files(worktree, git_environment, base_sha, head_sha)
    if not changed:
        raise VerificationError("worker commit has no Git-derived changed files")
    _require_regular_modes(worktree, git_environment, base_sha, head_sha, changed)
    scopes = tuple(str(item) for item in story["writeScopes"])
    for item in changed:
        paths = [str(item["path"])]
        if item["sourcePath"] is not None:
            paths.append(str(item["sourcePath"]))
        for path in paths:
            if not any(_within(path, scope) for scope in scopes):
                raise VerificationError(f"Git-derived path is outside declared scope: {path}")
    if list(changed) != claimed["changedFiles"]:
        raise VerificationError("receipt changedFiles do not exactly match Git-derived changes")

    required_commands = [str(item) for item in story["validationCommands"]]
    receipt_commands = [str(item["command"]) for item in claimed["checks"]]
    if receipt_commands != required_commands or any(item["status"] != "passed" for item in claimed["checks"]):
        raise VerificationError("receipt does not contain every required worker check in order")
    runner = command_runner or _default_command_runner
    check_evidence: list[dict[str, object]] = []
    for command in required_commands:
        current_head = _run_git(worktree, git_environment, ["rev-parse", "HEAD"]).stdout.decode("ascii").strip()
        if current_head != head_sha:
            raise VerificationError("required checks are no longer running at the receipt SHA")
        if not _clean(worktree, git_environment):
            raise VerificationError("required checks share mutation state; refusing the next check")
        try:
            argv = parse_command(command)
        except ValueError as exc:
            raise VerificationError(f"required check cannot be parsed as argv: {command}") from exc
        if not argv:
            raise VerificationError("required check has no executable argv")
        try:
            result = completed_text(runner(argv, worktree, git_environment.environment))
        except BoundedProcessError as exc:
            raise VerificationError(f"required check exceeded execution bounds: {exc}") from exc
        evidence = {
            "command": command, "status": "passed" if result.returncode == 0 else "failed",
            "evidenceDigest": _check_digest(command, result),
        }
        check_evidence.append(evidence)
        current_head = _run_git(worktree, git_environment, ["rev-parse", "HEAD"]).stdout.decode("ascii").strip()
        if current_head != head_sha:
            raise VerificationError("required check changed HEAD away from the receipt SHA")
        if not _clean(worktree, git_environment):
            raise VerificationError("required check mutated the worker worktree; evidence retained")
    if any(item["status"] != "passed" for item in check_evidence):
        raise VerificationError("one or more independently rerun worker checks failed")

    unsigned = {
        "runId": str(claimed["runId"]), "storyId": story_id,
        "branch": str(claimed["branch"]), "worktree": str(worktree),
        "baseSha": base_sha, "headSha": head_sha,
        "changedFiles": [dict(item) for item in changed], "checks": check_evidence,
    }
    evidence_digest = "sha256:" + hashlib.sha256(canonical_json(unsigned)).hexdigest()
    return VerifiedWorker(
        run_id=str(claimed["runId"]), story_id=story_id,
        branch=str(claimed["branch"]), worktree=worktree,
        base_sha=base_sha, head_sha=head_sha, changed_files=changed,
        check_evidence=tuple(check_evidence), evidence_digest=evidence_digest,
    )


__all__ = [
    "VerificationError", "VerifiedWorker", "load_controller_launch_record", "verify_worker",
]
