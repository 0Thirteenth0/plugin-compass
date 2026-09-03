"""Public Compass Builder controller composition.

The controller is the sole owner of worker checkouts, durable phase changes, and
ordered integration. Worker transports may edit only their registered clone;
they never receive the StateStore or integration checkout.
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence

from .git_environment import GitEnvironment, prepare_git_environment
from .integrator import IntegrationError, integrate_verified_branch
from .launcher import (
    BUNDLED_WORKER_SCHEMA, PreparedLaunch, REASONING_CONFIG_KEY,
    prepare_launch, validate_worker_output,
)
from .models import canonical_json, validate_worker_receipt
from .process_runner import BoundedProcessError, parse_command, run_bounded, run_bounded_text
from .secure_files import write_new_no_follow
from .state import StateStore, validate_execution_bundle
from .verifier import VerificationError, verify_worker


CONTROLLER_VERSION = "compass-builder.controller.v1"
PROMPT_VERSION = "compass-builder.prompt.v1"
METRIC_NAMES = (
    "retries", "interventions", "conflictsDetected", "conflictsAutoResolved",
    "conflictsManualResolved", "conflictsUnresolved", "scopeViolations",
    "staleHeadEvents", "timeouts", "checkFailures", "checkReruns",
    "repairDispatches", "manualEdits",
)


class ControllerError(RuntimeError):
    """A run stopped without a clean, controller-verified integrated HEAD."""

    def __init__(
        self, message: str, *, terminal_status: str = "blocked",
        metrics: Mapping[str, int] | None = None,
    ) -> None:
        super().__init__(message)
        self.terminal_status = terminal_status
        self.metrics = dict(metrics or empty_metrics())


EventSink = Callable[[str, Mapping[str, object]], None]
WorkerTransport = Callable[
    [PreparedLaunch, Mapping[str, object], int, EventSink], Mapping[str, object]
]


@dataclass(frozen=True)
class ControllerResult:
    run_id: str
    final_green_sha: str
    started_at: str
    ended_at: str
    elapsed_ms: int
    metrics: Mapping[str, int]
    state: Mapping[str, object]


def empty_metrics() -> dict[str, int]:
    return {name: 0 for name in METRIC_NAMES}


def _failure_metrics(message: str) -> dict[str, int]:
    metrics = empty_metrics()
    lowered = message.casefold()
    if "scope" in lowered or "outside declared" in lowered:
        metrics["scopeViolations"] = 1
    if "stale" in lowered or "advanced or changed" in lowered:
        metrics["staleHeadEvents"] = 1
    if "conflict" in lowered or "merge failure" in lowered:
        metrics["conflictsDetected"] = 1
        metrics["conflictsUnresolved"] = 1
    if "check" in lowered or "validation" in lowered:
        metrics["checkFailures"] = 1
    if "timed out" in lowered or "timeout" in lowered:
        metrics["timeouts"] = 1
    return metrics


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def _git(
    repository: Path, environment: GitEnvironment, arguments: Sequence[str],
    *, cwd: Path | None = None, check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    result = run_bounded(
        ["git", "--no-pager", "-C", str(cwd or repository), *arguments],
        environment=environment.environment,
    )
    if check and result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ControllerError(f"Git {arguments[0]} failed: {detail}")
    return result


def _head(repository: Path, environment: GitEnvironment) -> str:
    return _git(repository, environment, ["rev-parse", "HEAD"]).stdout.decode("ascii").strip()


def _clean(repository: Path, environment: GitEnvironment) -> bool:
    return not _git(
        repository, environment,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
    ).stdout


def _canonical_repo_path(value: str) -> str:
    text = value.replace("\\", "/")
    path = PurePosixPath(text)
    if (
        not text
        or "\x00" in text
        or any(ord(character) < 32 or ord(character) == 127 for character in text)
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ControllerError("Git emitted a non-canonical repository path")
    return "/".join(path.parts)


def _parse_changed_files(raw: bytes) -> list[dict[str, object]]:
    values = raw.decode("utf-8", errors="strict").split("\0")
    if values and values[-1] == "":
        values.pop()
    changed: list[dict[str, object]] = []
    index = 0
    kinds = {"A": "added", "M": "modified", "D": "deleted"}
    while index < len(values):
        status = values[index]
        index += 1
        code = status[:1]
        if code in {"R", "C"}:
            if code == "C" or index + 1 >= len(values):
                raise ControllerError("worker emitted an unsupported or truncated copy/rename")
            source, target = values[index:index + 2]
            index += 2
            changed.append({
                "path": _canonical_repo_path(target),
                "sourcePath": _canonical_repo_path(source),
                "changeType": "renamed",
            })
            continue
        if code not in kinds or index >= len(values):
            raise ControllerError(f"worker emitted unsupported Git change type {status!r}")
        path = values[index]
        index += 1
        changed.append({
            "path": _canonical_repo_path(path), "sourcePath": None,
            "changeType": kinds[code],
        })
    return changed


def _changed_files(
    worktree: Path, environment: GitEnvironment, base_sha: str, head_sha: str,
) -> list[dict[str, object]]:
    raw = _git(
        worktree, environment,
        ["diff", "--name-status", "-z", "--find-renames", base_sha, head_sha, "--"],
    ).stdout
    return _parse_changed_files(raw)


def _within_scope(path: str, scope: str) -> bool:
    candidate = _canonical_repo_path(path).casefold()
    boundary = _canonical_repo_path(scope).casefold()
    return candidate == boundary or candidate.startswith(boundary + "/")


def _worker_git(
    worktree: Path,
    environment: Mapping[str, str],
    arguments: Sequence[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = run_bounded(
            ["git", "--no-pager", "-C", str(worktree), *arguments],
            environment=environment,
        )
    except BoundedProcessError as exc:
        raise ControllerError(f"bounded worker Git operation failed: {exc}") from exc
    if check and result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ControllerError(f"worker Git {arguments[0]} failed: {detail}")
    return result


def _require_staged_story_scope(
    worktree: Path,
    environment: Mapping[str, str],
    story: Mapping[str, object],
    changed: Sequence[Mapping[str, object]],
) -> None:
    scopes = tuple(str(scope) for scope in story["writeScopes"])
    for item in changed:
        paths = [str(item["path"])]
        if item["sourcePath"] is not None:
            paths.append(str(item["sourcePath"]))
        for path in paths:
            if not any(_within_scope(path, scope) for scope in scopes):
                raise ControllerError(
                    f"worker change is outside declared scope before commit: {path}"
                )
        if item["changeType"] == "deleted":
            continue
        staged = _worker_git(
            worktree, environment, ["ls-files", "-s", "--", str(item["path"])]
        ).stdout.decode("utf-8", errors="strict").splitlines()
        if len(staged) != 1:
            raise ControllerError("staged worker path has ambiguous Git index evidence")
        mode = staged[0].split(maxsplit=1)[0]
        if not mode.startswith("100"):
            raise ControllerError("staged worker path is not a regular Git blob")


def _commit_worker_edits(
    launch: PreparedLaunch, story: Mapping[str, object]
) -> tuple[str, list[dict[str, object]]]:
    worktree = Path(str(launch.record["worktree"]))
    environment = launch.environment
    base_sha = str(launch.record["workerStartSha"])
    before = _worker_git(worktree, environment, ["rev-parse", "HEAD"]).stdout.decode(
        "ascii"
    ).strip()
    if before != base_sha:
        raise ControllerError("worker changed HEAD before the controller-owned commit")
    staged_before = _worker_git(
        worktree, environment,
        ["diff", "--cached", "--quiet", "--exit-code", "--"],
        check=False,
    )
    if staged_before.returncode == 1:
        raise ControllerError("worker changed the Git index before the controller-owned commit")
    if staged_before.returncode != 0:
        raise ControllerError("worker Git index state could not be inspected")
    dirty = _worker_git(
        worktree, environment,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
    ).stdout
    if not dirty:
        raise ControllerError("worker returned succeeded without story changes")
    _worker_git(worktree, environment, ["add", "--all"])
    staged_raw = _worker_git(
        worktree, environment,
        ["diff", "--cached", "--name-status", "-z", "--find-renames", base_sha, "--"],
    ).stdout
    changed = _parse_changed_files(staged_raw)
    if not changed:
        raise ControllerError("controller staging produced no story changes")
    _require_staged_story_scope(worktree, environment, story, changed)
    _worker_git(
        worktree, environment,
        ["commit", "--no-gpg-sign", "-m", f"Compass Builder: {launch.record['storyId']}"],
    )
    head_sha = _worker_git(
        worktree, environment, ["rev-parse", "HEAD"]
    ).stdout.decode("ascii").strip()
    committed = _parse_changed_files(_worker_git(
        worktree, environment,
        ["diff", "--name-status", "-z", "--find-renames", base_sha, head_sha, "--"],
    ).stdout)
    if committed != changed:
        raise ControllerError("controller-owned commit differs from the staged story evidence")
    if _worker_git(
        worktree, environment,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
    ).stdout:
        raise ControllerError("controller-owned commit did not leave the worktree clean")
    return head_sha, changed


def _output_candidates(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _output_candidates(child)
    elif isinstance(value, list):
        for child in value:
            yield from _output_candidates(child)
    elif isinstance(value, str) and value.lstrip().startswith("{"):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return
        yield from _output_candidates(decoded)


def _structured_output(stdout: bytes) -> dict[str, object]:
    matches: list[dict[str, object]] = []
    for line in stdout.decode("utf-8", errors="strict").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        for candidate in _output_candidates(value):
            try:
                matches.append(validate_worker_output(candidate))
            except ValueError:
                continue
    if not matches:
        raise ControllerError("Codex worker emitted no valid structured terminal output")
    return matches[-1]


def codex_worker_transport(
    launch: PreparedLaunch, story: Mapping[str, object], timeout_ms: int,
    event_sink: EventSink,
) -> Mapping[str, object]:
    """Run one bounded Codex worker with a one-shot prompt and closed stdin."""

    started = time.monotonic()
    status = "failed"
    blocker: str | None = None
    output: dict[str, object] | None = None
    try:
        result = run_bounded(
            launch.argv, cwd=Path(str(launch.record["worktree"])),
            environment=launch.environment, stdin=launch.stdin.encode("utf-8"),
            timeout=timeout_ms / 1000,
        )
        output = _structured_output(result.stdout)
        status = str(output["status"])
        blocker = None if status == "succeeded" else str(output["blocker"])
        if result.returncode and status == "succeeded":
            status, blocker = "failed", f"Codex exited with status {result.returncode}"
    except BoundedProcessError as exc:
        status = "timed-out" if "timed out" in str(exc) else "failed"
        blocker = str(exc)[:2000]
        if status == "timed-out":
            event_sink("timeout", {"storyId": launch.record["storyId"]})
    worktree = Path(str(launch.record["worktree"]))
    head_sha: str | None = None
    changed: list[dict[str, object]] = []
    checks: list[dict[str, object]] = []
    if status == "succeeded":
        environment = launch.environment
        head_sha, changed = _commit_worker_edits(launch, story)
        for index, command in enumerate(story["validationCommands"]):
            event_sink("check-rerun", {
                "storyId": launch.record["storyId"], "commandIndex": index,
            })
            checked = run_bounded_text(
                parse_command(str(command)), cwd=worktree, environment=environment,
            )
            check_status = "passed" if checked.returncode == 0 else "failed"
            checks.append({
                "name": f"controller-check-{index + 1}", "command": str(command),
                "status": check_status,
                "evidenceDigest": _digest({
                    "command": command, "returnCode": checked.returncode,
                    "stdout": checked.stdout, "stderr": checked.stderr,
                }),
            })
        if not changed or any(item["status"] != "passed" for item in checks):
            status = "failed"
            blocker = "worker changes or required checks did not satisfy the controller"
    elapsed = max(1, int((time.monotonic() - started) * 1000))
    receipt = {
        "schemaVersion": "compass-builder.worker-receipt.v1",
        "runId": launch.record["runId"], "storyId": launch.record["storyId"],
        "branch": launch.record["branch"], "worktree": launch.record["worktree"],
        "exactModel": launch.record["exactModel"], "effort": launch.record["effort"],
        "baseSha": launch.record["workerStartSha"], "headSha": head_sha,
        "commitSha": head_sha, "changedFiles": changed, "checks": checks,
        "elapsedMs": elapsed, "status": status, "blocker": blocker,
    }
    return validate_worker_receipt(receipt)


def _create_worktree(
    store: StateStore, environment: GitEnvironment, story_id: str, start_sha: str,
) -> Path:
    """Create an object-isolated worker checkout at the registered legacy path."""

    target = store.registered_worktree(story_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise ControllerError("registered worker checkout already exists")
    branch = next(
        str(item["branch"]) for item in store.plan["stories"]
        if item["storyId"] == story_id
    )
    _git(
        store.repository.root, environment,
        [
            "clone", "--no-hardlinks", "--no-tags", "--no-checkout",
            str(store.repository.root), str(target),
        ],
    )
    checkout = target.resolve(strict=True)
    _git(checkout, environment, ["checkout", "-b", branch, start_sha])
    _git(checkout, environment, ["remote", "remove", "origin"])
    if _git(checkout, environment, ["remote"]).stdout:
        raise ControllerError("worker checkout retained unexpected Git remotes")
    common = Path(_git(
        checkout, environment,
        ["rev-parse", "--path-format=absolute", "--git-common-dir"],
    ).stdout.decode("utf-8", errors="strict").strip()).resolve(strict=True)
    expected = (checkout / ".git").resolve(strict=True)
    if common != expected or common == store.repository.common_git_dir:
        raise ControllerError("worker checkout does not have an isolated Git database")
    return checkout


def _import_worker_branch(
    store: StateStore,
    environment: GitEnvironment,
    receipt: Mapping[str, object],
) -> str:
    """Import one exact clone branch into the integration repository without checkout edits."""

    worktree = Path(str(receipt["worktree"])).resolve(strict=True)
    branch = str(receipt["branch"])
    head_sha = str(receipt["headSha"])
    source_ref = f"refs/heads/{branch}"
    source_head = _git(
        worktree, environment, ["rev-parse", "--verify", source_ref]
    ).stdout.decode("ascii").strip()
    if source_head != head_sha:
        raise ControllerError("worker clone branch changed before controller import")
    existing = _git(
        store.repository.root, environment,
        ["rev-parse", "--verify", source_ref], check=False,
    )
    if existing.returncode == 0:
        if existing.stdout.decode("ascii").strip() != head_sha:
            raise ControllerError("worker destination branch already exists at another SHA")
    elif existing.returncode == 128:
        _git(
            store.repository.root, environment,
            [
                "fetch", "--no-tags", "--no-write-fetch-head", str(worktree),
                f"{source_ref}:{source_ref}",
            ],
        )
    else:
        raise ControllerError("worker destination branch availability is ambiguous")
    imported = _git(
        store.repository.root, environment, ["rev-parse", "--verify", source_ref]
    ).stdout.decode("ascii").strip()
    if imported != head_sha:
        raise ControllerError("imported worker branch does not match the receipt SHA")
    return imported


def _publish_launch(store: StateStore, story_id: str, launch: PreparedLaunch) -> None:
    directory = store.run_root / "launch-records"
    directory.mkdir(exist_ok=True)
    write_new_no_follow(
        directory / f"{story_id}.json", canonical_json(launch.record), store.control_root,
        label="controller-owned launch record",
    )


def _block(store: StateStore, state: Mapping[str, object], message: str) -> None:
    store.record_blocker(
        state, reason=message, evidence_digest=_digest({"reason": message}),
    )


def execute_run(
    repository: Path,
    execution_bundle: Mapping[str, object],
    *,
    worker_transport: WorkerTransport = codex_worker_transport,
    timeout_ms: int = 600_000,
    event_sink: EventSink | None = None,
) -> ControllerResult:
    """Execute a new run to a clean green integrated HEAD or fail closed."""

    if type(timeout_ms) is not int or timeout_ms < 1:
        raise ControllerError("controller timeout must be a positive integer millisecond bound")
    sink = event_sink or (lambda _kind, _details: None)
    raw = validate_execution_bundle(execution_bundle, Path(repository))
    spec, plan = raw["runSpec"], raw["wavePlan"]
    provisional = StateStore(repository, spec, plan)
    initial = provisional.initial_state()
    provisional.create(initial, execution_bundle=raw)
    environment = prepare_git_environment(provisional.run_root / "git-environment")
    bundle = validate_execution_bundle(raw, Path(repository), environment)
    store = StateStore(repository, spec, plan, environment)
    state = store.load()
    metrics = empty_metrics()
    first_launch: datetime | None = None
    try:
        if not _clean(store.repository.root, environment):
            raise ControllerError(
                "integration checkout is not clean under the controller Git policy"
            )
        while True:
            opening_next = state["state"] == "wave-verified"
            target_wave = int(state["currentWaveIndex"]) + (1 if opening_next else 0)
            story_ids = [str(item) for item in plan["waves"][target_wave]["storyIds"]]
            start_sha = (
                str(state["expectedIntegrationSha"])
                if opening_next else
                str(state["waves"][state["currentWaveIndex"]]["startExpectedSha"])
            )
            launches: dict[str, PreparedLaunch] = {}
            for story_id in story_ids:
                worktree = _create_worktree(store, environment, story_id, start_sha)
                launch = prepare_launch(
                    spec, plan, bundle["hostCapabilities"],
                    planning_timestamp=str(bundle["planningTimestamp"]),
                    story_id=story_id, worktree=worktree,
                    worker_schema=BUNDLED_WORKER_SCHEMA,
                    reasoning_config_key=REASONING_CONFIG_KEY,
                    reasoning_config_evidence_digest=str(
                        bundle["hostCapabilities"]["reasoningConfig"]["evidenceDigest"]
                    ),
                    git_environment=environment, worker_start_sha=start_sha,
                )
                _publish_launch(store, story_id, launch)
                launches[story_id] = launch
            if opening_next:
                next_state = store.next_wave_state(state, start_workers=True)
                state = store.write_transition(state, next_state)
            elif state["state"] == "planned":
                dispatching = copy.deepcopy(state)
                dispatching.update(previousState="planned", state="dispatching")
                for entry in dispatching["waves"][dispatching["currentWaveIndex"]]["branches"]:
                    entry["workerState"] = "running"
                state = store.write_transition(state, dispatching)
            stories = {str(item["id"]): item for item in spec["stories"]}
            receipts: dict[str, dict[str, object]] = {}

            def run_story(story_id: str) -> tuple[str, dict[str, object]]:
                sink("worker-launch", {
                    "runId": spec["runId"], "storyId": story_id,
                    "attempt": launches[story_id].record["attempt"],
                })
                receipt = validate_worker_receipt(worker_transport(
                    launches[story_id], stories[story_id], timeout_ms, sink
                ))
                sink("worker-completion", {
                    "runId": spec["runId"], "storyId": story_id,
                    "status": receipt["status"], "headSha": receipt["headSha"],
                })
                return story_id, receipt

            if first_launch is None:
                first_launch = datetime.now(timezone.utc)
            with ThreadPoolExecutor(max_workers=int(plan["concurrency"])) as executor:
                futures = [executor.submit(run_story, story_id) for story_id in story_ids]
                for future in as_completed(futures):
                    story_id, receipt = future.result()
                    receipts[story_id] = receipt
            non_green = [item for item in receipts.values() if item["status"] != "succeeded"]
            if non_green:
                if any(item["status"] == "timed-out" for item in non_green):
                    metrics["timeouts"] += 1
                    status = "timed-out"
                else:
                    status = "blocked"
                raise ControllerError(
                    "one or more workers did not succeed", terminal_status=status,
                    metrics=metrics,
                )
            completed = copy.deepcopy(state)
            completed.update(previousState="dispatching", state="wave-workers-complete")
            for entry in completed["waves"][completed["currentWaveIndex"]]["branches"]:
                entry["workerState"] = "complete"
            state = store.write_transition(state, completed)
            for story_id in story_ids:
                imported = _import_worker_branch(store, environment, receipts[story_id])
                sink("worker-branch-import", {
                    "storyId": story_id, "headSha": imported,
                })
                sink("ref-status-observation", {
                    "storyId": story_id, "integrationHead": store.observed_integration_head(),
                })
                verify_worker(
                    store.repository.root, spec, plan, receipts[story_id],
                    launches[story_id].record, environment,
                )
            merging = copy.deepcopy(state)
            merging.update(previousState="wave-workers-complete", state="wave-merging")
            for entry in merging["waves"][merging["currentWaveIndex"]]["branches"]:
                entry.update(verificationState="verified", integrationState="worker-verified")
            state = store.write_transition(state, merging)
            for story_id in story_ids:
                result = integrate_verified_branch(
                    store, state, receipts[story_id], environment,
                )
                state = result.state
                sink("check-rerun", {
                    "storyId": story_id, "controllerCheckDigest": result.controller_check_digest,
                })
                sink("ref-status-observation", {
                    "storyId": story_id, "integrationHead": result.merge_sha,
                })
            if int(state["currentWaveIndex"]) + 1 < len(plan["waves"]):
                continue
            final = copy.deepcopy(state)
            final.update(previousState="wave-verified", state="completed")
            state = store.write_transition(state, final)
            break
    except (ControllerError, IntegrationError, VerificationError, OSError, ValueError) as exc:
        try:
            _block(store, state, str(exc))
        except Exception:
            pass
        if isinstance(exc, ControllerError):
            raise
        raise ControllerError(str(exc), metrics=_failure_metrics(str(exc))) from exc
    if first_launch is None:
        raise ControllerError("run reached no worker launch")
    ended = datetime.now(timezone.utc)
    started_wire = first_launch.replace(microsecond=(first_launch.microsecond // 1000) * 1000)
    ended_wire = ended.replace(microsecond=(ended.microsecond // 1000) * 1000)
    elapsed_ms = int((ended_wire - started_wire).total_seconds() * 1000)
    if elapsed_ms < 1:
        from datetime import timedelta
        ended_wire = started_wire + timedelta(milliseconds=1)
        elapsed_ms = 1
    final_sha = str(state["lastVerifiedIntegrationSha"])
    if _head(store.repository.root, environment) != final_sha or not _clean(
        store.repository.root, environment
    ):
        raise ControllerError("completed run does not end at a clean verified integration HEAD")
    return ControllerResult(
        run_id=str(spec["runId"]), final_green_sha=final_sha,
        started_at=_timestamp(started_wire), ended_at=_timestamp(ended_wire),
        elapsed_ms=elapsed_ms, metrics=metrics, state=state,
    )


__all__ = [
    "CONTROLLER_VERSION", "PROMPT_VERSION", "ControllerError", "ControllerResult",
    "EventSink", "WorkerTransport", "codex_worker_transport", "empty_metrics",
    "execute_run",
]
