"""Fail-closed cleanup derived only from durable controller ownership."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Sequence

from .git_environment import GitEnvironment, validate_git_environment
from .git_objects import GitObjectError, read_raw_commit
from .lease import acquire_lease, release_lease
from .models import canonical_json
from .process_runner import BoundedProcessError, run_bounded
from .state import StateError, StateStore


class CleanupError(ValueError):
    """A durable verified worker checkout cannot be removed without weakening gates."""


def _remove_clone(path: Path) -> None:
    """Remove a preflighted clone, clearing Git's Windows read-only object bits."""

    root = path.resolve(strict=True)

    def retry_readonly(function, candidate, _error):
        literal = Path(candidate)
        try:
            literal.resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise CleanupError("clone removal callback escaped its preflighted root") from exc
        os.chmod(literal, stat.S_IWRITE)
        function(candidate)

    shutil.rmtree(root, onerror=retry_readonly)


def _is_reparse(path: Path) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    stat = path.lstat()
    return path.is_symlink() or bool(
        getattr(stat, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _git(repo: Path, environment: GitEnvironment, arguments: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    bundle = validate_git_environment(environment)
    try:
        result = run_bounded(
            ["git", "--no-pager", "-C", str(repo), *arguments],
            environment=bundle.environment,
        )
    except BoundedProcessError as exc:
        raise CleanupError(f"bounded Git inspection failed: {exc}") from exc
    if check and result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise CleanupError(f"Git {arguments[0]} failed: {detail}")
    return result


def _text(repo: Path, environment: GitEnvironment, arguments: Sequence[str]) -> str:
    return _git(repo, environment, arguments).stdout.decode("utf-8", errors="strict").strip()


def _worktrees(repo: Path, environment: GitEnvironment) -> dict[Path, dict[str, str]]:
    output = _git(repo, environment, ["worktree", "list", "--porcelain", "-z"]).stdout
    try:
        fields = output.decode("utf-8", errors="strict").split("\x00")
    except UnicodeDecodeError as exc:
        raise CleanupError("Git worktree registry contains a non-UTF-8 path") from exc
    records: dict[Path, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for field in fields:
        if not field:
            continue
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


def _contained(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return path != root
    except ValueError:
        return False


def _require_real_chain(path: Path, root: Path) -> None:
    current = path
    while True:
        if _is_reparse(current):
            raise CleanupError(f"cleanup target contains a symlink/reparse point: {current}")
        if current == root:
            return
        if current.parent == current:
            raise CleanupError("cleanup target does not reach its controller-owned root")
        current = current.parent


def _integration_identity(store: StateStore, state: Mapping[str, object], environment: GitEnvironment) -> None:
    expected = str(state["expectedIntegrationSha"])
    expected_ref = f"refs/heads/{store.spec['integrationBranch']}"
    symbolic = _git(store.repository.root, environment, ["symbolic-ref", "--quiet", "HEAD"], check=False)
    if symbolic.returncode or symbolic.stdout.decode("utf-8").strip() != expected_ref:
        raise CleanupError("cleanup checkout is not on the durable integration branch")
    head = _text(store.repository.root, environment, ["rev-parse", "HEAD"])
    ref = _text(store.repository.root, environment, ["rev-parse", "--verify", expected_ref])
    if head != expected or ref != expected:
        raise CleanupError("cleanup integration HEAD/ref does not match durable state")


def _derive_targets(store: StateStore, state: Mapping[str, object], environment: GitEnvironment) -> list[dict[str, str]]:
    entries: list[Mapping[str, object]] = []
    for wave in state["waves"]:
        for entry in wave["branches"]:
            if entry["integrationState"] != "integration-verified":
                continue
            merge_sha = str(entry["mergeSha"])
            if entry["postCheckExpectedSha"] != merge_sha or entry["controllerCheckDigest"] is None:
                raise CleanupError("durable verified entry lacks clean post-merge evidence")
            entries.append(entry)
    if not entries:
        raise CleanupError("durable state contains no verified-merged worktrees")

    commits: dict[str, tuple[str, str]] = {}
    cursor = str(state["expectedIntegrationSha"])
    for entry in reversed(entries):
        merge_sha = str(entry["mergeSha"])
        if merge_sha != cursor:
            raise CleanupError("durable integration ledger has an extra or missing merge-chain edge")
        try:
            parents = read_raw_commit(
                store.repository.root, merge_sha, environment.environment,
                expected_parent_count=2,
            ).parents
        except GitObjectError as exc:
            raise CleanupError(f"verified merge object is invalid: {exc}") from exc
        if parents[0] != entry["preMergeExpectedSha"]:
            raise CleanupError("verified merge first parent does not match ledger CAS evidence")
        commits[merge_sha] = (parents[0], parents[1])
        cursor = parents[0]

    targets: list[dict[str, str]] = []
    for entry in entries:
        merge_sha = str(entry["mergeSha"])
        parents = commits[merge_sha]
        targets.append({
            "storyId": str(entry["storyId"]), "branch": str(entry["branch"]),
            "worktree": str(store.registered_worktree(str(entry["storyId"]))),
            "headSha": parents[1], "mergeSha": merge_sha,
        })
    return targets


def _preflight_all(
    store: StateStore,
    state: Mapping[str, object],
    environment: GitEnvironment,
    targets: Sequence[Mapping[str, str]],
) -> tuple[Path, ...]:
    _integration_identity(store, state, environment)
    registry = _worktrees(store.repository.root, environment)
    root = store.worktree_root.resolve(strict=True)
    if (
        _is_reparse(root)
        or not root.is_dir()
        or not _contained(root, store.workspace_control_root.resolve(strict=True))
    ):
        raise CleanupError("controller-owned worktree root is unavailable or unsafe")
    prepared: list[Path] = []
    seen: set[str] = set()
    for item in targets:
        literal = Path(item["worktree"])
        if not literal.exists() or not literal.is_dir():
            raise CleanupError(f"registered worker checkout is missing: {literal}")
        _require_real_chain(literal, root)
        target = literal.resolve(strict=True)
        if target == store.repository.root or not _contained(target, root):
            raise CleanupError("cleanup target is primary checkout or outside the run root")
        key = os.path.normcase(str(target)).casefold()
        if key in seen:
            raise CleanupError("durable worktree registrations contain a case/path alias")
        seen.add(key)
        record = registry.get(target)
        expected_ref = f"refs/heads/{item['branch']}"
        common = Path(_text(target, environment, ["rev-parse", "--path-format=absolute", "--git-common-dir"])).resolve(strict=True)
        if common == store.repository.common_git_dir:
            if record is None or record.get("branch") != expected_ref or record.get("HEAD") != item["headSha"]:
                raise CleanupError("worktree registry branch/HEAD differs from durable merge evidence")
        else:
            git_dir = Path(_text(
                target, environment,
                ["rev-parse", "--path-format=absolute", "--absolute-git-dir"],
            )).resolve(strict=True)
            expected_git_dir = (target / ".git").resolve(strict=True)
            if (
                record is not None
                or common != expected_git_dir
                or git_dir != expected_git_dir
                or not expected_git_dir.is_dir()
                or _is_reparse(expected_git_dir)
            ):
                raise CleanupError("cleanup target is not an exact isolated Git clone")
            remotes = _text(target, environment, ["remote"]).splitlines()
            if remotes:
                raise CleanupError("cleanup clone retained a remote during isolated execution")
            if _text(target, environment, ["rev-parse", "--is-shallow-repository"]) != "false":
                raise CleanupError("cleanup clone must contain complete Git history")
            alternates = expected_git_dir / "objects" / "info" / "alternates"
            if alternates.exists() or alternates.is_symlink():
                raise CleanupError("cleanup clone may not use alternate object storage")
        if _text(target, environment, ["rev-parse", "HEAD"]) != item["headSha"]:
            raise CleanupError("cleanup target HEAD changed after registry inspection")
        symbolic = _git(target, environment, ["symbolic-ref", "--quiet", "HEAD"], check=False)
        if symbolic.returncode or symbolic.stdout.decode("utf-8").strip() != expected_ref:
            raise CleanupError("cleanup target is detached or on the wrong branch")
        if _text(target, environment, ["rev-parse", "--verify", expected_ref]) != item["headSha"]:
            raise CleanupError("cleanup branch ref differs from durable worker merge parent")
        if _git(target, environment, ["status", "--porcelain=v1", "-z", "--untracked-files=all"]).stdout:
            raise CleanupError("cleanup target is dirty; evidence retained")
        prepared.append(target)
    return tuple(prepared)


def cleanup_run(store: StateStore, git_environment: GitEnvironment) -> tuple[Path, ...]:
    """Remove only checkouts authorized by the durable verified integration ledger."""

    bundle = validate_git_environment(git_environment)
    if store.git_environment is None or store.git_environment.digest != bundle.digest or store.git_environment.root != bundle.root:
        raise CleanupError("cleanup Git environment does not match the StateStore binding")
    handle = None
    state: Mapping[str, object] | None = None
    primary: Exception | None = None
    removed: tuple[Path, ...] = ()
    try:
        state = store.load()
        now = datetime.now(timezone.utc)
        evidence = "sha256:" + hashlib.sha256(canonical_json({
            "runId": store.run_id, "expectedIntegrationSha": state["expectedIntegrationSha"],
            "operation": "cleanup",
        })).hexdigest()
        handle = acquire_lease(
            store.repository.common_git_dir, str(store.spec["integrationBranch"]),
            owner_id="compass-builder-cleanup", evidence_digest=evidence,
            acquired_at=now.isoformat().replace("+00:00", "Z"),
            expires_at=(now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        )
        leased_state = store.load()
        if canonical_json(leased_state) != canonical_json(state):
            raise CleanupError("durable cleanup ledger changed during lease acquisition")
        state = leased_state
        targets = _derive_targets(store, state, bundle)
        progress = store.cleanup_progress()
        registry = _worktrees(store.repository.root, bundle)
        pending: list[dict[str, str]] = []
        completed: list[Path] = []
        for item in targets:
            path = Path(item["worktree"]).resolve(strict=False)
            matching = [record for record in progress if (
                record.get("storyId") == item["storyId"]
                and record.get("worktree") == item["worktree"]
                and record.get("headSha") == item["headSha"]
            )]
            statuses = {str(record.get("status")) for record in matching}
            present = path.exists() or path.is_symlink()
            registered = path in registry
            if "removed" in statuses:
                if present or registered:
                    raise CleanupError("completed cleanup receipt conflicts with a present worktree")
                completed.append(path)
            elif "removing" in statuses and not present and not registered:
                store.record_cleanup_progress(
                    story_id=item["storyId"], worktree=item["worktree"],
                    head_sha=item["headSha"], status="removed",
                )
                completed.append(path)
            else:
                pending.append(item)

        # Establish that every not-yet-removed target is initially safe before side effects.
        if pending:
            _preflight_all(store, state, bundle, pending)
        for item in pending:
            # Re-check this target at the last safe boundary before its removal.
            target = _preflight_all(store, state, bundle, [item])[0]
            store.record_cleanup_progress(
                story_id=item["storyId"], worktree=item["worktree"],
                head_sha=item["headSha"], status="removing",
            )
            if target in _worktrees(store.repository.root, bundle):
                _git(store.repository.root, bundle, ["worktree", "remove", str(target)])
            else:
                _remove_clone(target)
                if target.exists() or target.is_symlink():
                    raise CleanupError("isolated clone removal did not complete")
            store.record_cleanup_progress(
                story_id=item["storyId"], worktree=item["worktree"],
                head_sha=item["headSha"], status="removed",
            )
            completed.append(target)
        removed = tuple(completed)
    except Exception as exc:
        primary = exc if isinstance(exc, CleanupError) else CleanupError(str(exc))
    if handle is not None:
        try:
            release_lease(handle)
        except Exception as exc:
            if primary is None:
                primary = CleanupError(f"cleanup lease release failed: {exc}")
    if primary is not None:
        if state is not None:
            digest = "sha256:" + hashlib.sha256(canonical_json({
                "operation": "cleanup", "reason": str(primary),
            })).hexdigest()
            try:
                store.record_failure_evidence(
                    blocked_from_state=str(state.get("state", "unknown")),
                    reason=str(primary), evidence_digest=digest,
                )
            except Exception:
                pass
        raise primary
    return removed


__all__ = ["CleanupError", "cleanup_run"]
