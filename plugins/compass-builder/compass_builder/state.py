"""Controller-owned durable state and resumable dry-run composition.

This module validates repository and run bindings, but deliberately performs no
worker dispatch, verification, merge, integration, or cleanup.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ._validation import branch as validate_branch
from ._validation import run_id as validate_run_id
from .models import (
    canonical_json,
    run_binding_digest,
    validate_run_bindings,
    validate_run_state,
    validate_run_state_transition,
    validate_run_structure_bindings,
)


class StateError(ValueError):
    """Durable controller state cannot be trusted or advanced safely."""


@dataclass(frozen=True)
class RepositoryIdentity:
    """Canonical identity shared by every worktree of one Git repository."""

    root: Path
    common_git_dir: Path
    git_dir: Path


def _is_reparse(path: Path) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    stat = path.lstat()
    return path.is_symlink() or bool(
        getattr(stat, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _reject_existing_reparse_components(path: Path, *, label: str) -> None:
    """Reject lexical aliases before ``resolve`` can erase their identity."""

    raw = Path(path).absolute()
    for component in (raw, *raw.parents):
        if component.exists() and _is_reparse(component):
            raise StateError(f"{label} contains a symlink or reparse ancestor: {component}")


def _read_file_no_follow(path: Path, root: Path, *, label: str) -> bytes:
    _require_contained(path, root, label=label)
    _reject_existing_reparse_components(path, label=label)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StateError(f"{label} is unavailable: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise StateError(f"{label} is not a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read()
        named = os.stat(path, follow_symlinks=False)
        if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
            raise StateError(f"{label} changed while it was read")
        return payload
    except OSError as exc:
        raise StateError(f"{label} could not be read safely: {exc}") from exc
    finally:
        os.close(descriptor)


def _write_new_file(path: Path, payload: bytes, root: Path, *, label: str) -> None:
    _require_contained(path, root, label=label)
    _reject_existing_reparse_components(path.parent, label=label)
    if path.exists() or path.is_symlink():
        raise StateError(f"{label} already exists")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise StateError(f"{label} publication failed: {exc}") from exc


def _git(repo: Path, arguments: list[str], *, check: bool = True) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            shell=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise StateError(f"Git repository evidence is unavailable: {exc}") from exc
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "Git command failed"
        raise StateError(f"Git repository evidence failed closed: {detail}")
    return result.stdout


def resolve_repository(repository: Path) -> RepositoryIdentity:
    """Resolve an exact checkout root and its canonical Git common directory."""

    raw = Path(repository)
    if not raw.is_absolute():
        raise StateError("repository path must be absolute")
    _reject_existing_reparse_components(raw, label="repository path")
    if _is_reparse(raw):
        raise StateError("repository root may not be a symlink or reparse point")
    try:
        requested = raw.resolve(strict=True)
    except OSError as exc:
        raise StateError(f"repository path is missing or unreadable: {exc}") from exc
    if not requested.is_dir() or _is_reparse(requested):
        raise StateError("repository root must be a real non-reparse directory")
    output = _git(
        requested,
        ["rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir", "--git-dir"],
    ).splitlines()
    if len(output) != 3:
        raise StateError("Git returned ambiguous repository identity evidence")
    try:
        output_paths = tuple(Path(item) for item in output)
        for item in output_paths:
            _reject_existing_reparse_components(item, label="Git repository identity")
        root, common, git_dir = (item.resolve(strict=True) for item in output_paths)
    except OSError as exc:
        raise StateError(f"Git repository identity contains an unreadable path: {exc}") from exc
    if root != requested:
        raise StateError("repository path must name the canonical checkout root exactly")
    if any(not path.is_dir() or _is_reparse(path) for path in (root, common, git_dir)):
        raise StateError("repository identity contains a non-directory or reparse target")
    return RepositoryIdentity(root=root, common_git_dir=common, git_dir=git_dir)


def _require_contained(path: Path, root: Path, *, label: str) -> Path:
    lexical = path.absolute()
    lexical_root = root.absolute()
    current = lexical
    while current != lexical_root.parent:
        if current.exists() and _is_reparse(current):
            raise StateError(f"{label} contains a reparse point: {current}")
        if current == lexical_root:
            break
        current = current.parent
    try:
        resolved = path.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise StateError(f"{label} escapes its registered controller root") from exc
    if resolved in {root, root.parent}:
        raise StateError(f"{label} may not target the repository or controller root")
    current = resolved
    while current != root.parent:
        if current.exists() and _is_reparse(current):
            raise StateError(f"{label} contains a reparse point: {current}")
        if current == root:
            break
        current = current.parent
    return resolved


class StateStore:
    """Validate and persist one controller-owned run under its checkout."""

    def __init__(
        self,
        repository: Path,
        run_spec: Mapping[str, object],
        wave_plan: Mapping[str, object],
    ) -> None:
        self.repository = resolve_repository(Path(repository))
        try:
            self.spec, self.plan, _ = validate_run_structure_bindings(run_spec, wave_plan)
        except ValueError as exc:
            raise StateError(f"run bindings are invalid: {exc}") from exc
        self.run_id = str(self.spec["runId"])
        self.control_root = self.repository.root / ".compass-builder"
        self.run_root = self.control_root / "runs" / self.run_id
        self.worktree_root = self.control_root / "worktrees" / self.run_id
        self.path = self.run_root / "state.json"
        self.registry_path = self.run_root / "controller.json"
        self.bundle_path = self.run_root / "plan-bundle.json"
        self._branches = {
            str(item["storyId"]): str(item["branch"]) for item in self.plan["stories"]
        }
        self._validate_static_layout()

    def _validate_static_layout(self) -> None:
        validate_run_id(self.run_id, "runId")
        probe = f".compass-builder/runs/{self.run_id}/state.json"
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repository.root), "check-ignore", "--quiet", "--no-index", probe],
                check=False, capture_output=True, shell=False, timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StateError(f"ignore evidence is unavailable: {exc}") from exc
        if result.returncode != 0:
            raise StateError(".compass-builder must be ignored before controller state is used")
        tracked = _git(self.repository.root, ["ls-files", "--", ".compass-builder"])
        if tracked.strip():
            raise StateError(".compass-builder must be absent from the repository index")
        _require_contained(self.run_root, self.control_root, label="run state path")
        _require_contained(self.worktree_root, self.control_root, label="worktree registry root")
        for story_id, planned in self._branches.items():
            expected = f"cb/{self.run_id}/{story_id}"
            validate_branch(planned, f"wavePlan.stories.{story_id}.branch")
            if planned != expected:
                raise StateError(
                    f"story {story_id!r} branch is not the deterministic registered branch {expected!r}"
                )
            self.registered_worktree(story_id)

    def registered_worktree(self, story_id: str) -> Path:
        if story_id not in self._branches:
            raise StateError(f"story {story_id!r} is not registered in the immutable plan")
        target = self.worktree_root / story_id
        resolved = _require_contained(target, self.worktree_root, label="registered worktree")
        forbidden = {
            self.repository.root,
            self.repository.common_git_dir,
            self.repository.git_dir,
            self.control_root.resolve(strict=False),
            self.worktree_root.resolve(strict=False),
        }
        if resolved in forbidden:
            raise StateError("registered worktree targets a protected repository/controller path")
        return resolved

    def registrations(self) -> tuple[dict[str, str], ...]:
        return tuple(
            {
                "storyId": story_id,
                "branch": self._branches[story_id],
                "worktree": str(self.registered_worktree(story_id)),
            }
            for story_id in self._branches
        )

    def observed_integration_head(self) -> str:
        ref = f"refs/heads/{self.spec['integrationBranch']}^{{commit}}"
        value = _git(self.repository.root, ["rev-parse", "--verify", ref]).strip()
        if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
            raise StateError("integration branch did not resolve to one immutable lowercase SHA")
        return value

    def compare_and_swap(self, expected_sha: str) -> str:
        actual = self.observed_integration_head()
        if actual != expected_sha:
            raise StateError(
                f"stale integration HEAD: expected {expected_sha}, observed {actual}"
            )
        return actual

    def _wave(self, index: int, expected_sha: str) -> dict[str, object]:
        plan_wave = self.plan["waves"][index]
        return {
            "waveIndex": index,
            "startExpectedSha": expected_sha,
            "branches": [
                {
                    "storyId": story_id,
                    "branch": self._branches[story_id],
                    "workerState": "pending",
                    "verificationState": "pending",
                    "integrationState": "pending",
                    "preMergeExpectedSha": expected_sha,
                    "mergeSha": None,
                    "controllerCheckDigest": None,
                    "postCheckExpectedSha": None,
                }
                for story_id in plan_wave["storyIds"]
            ],
        }

    def initial_state(self) -> dict[str, object]:
        initial = str(self.plan["integrationExpectedSha"])
        self.compare_and_swap(initial)
        state = {
            "schemaVersion": "compass-builder.run-state.v1",
            "runId": self.run_id,
            "baseSha": self.spec["baseSha"],
            "integrationBranch": self.spec["integrationBranch"],
            "initialIntegrationSha": initial,
            "expectedIntegrationSha": initial,
            "lastVerifiedIntegrationSha": initial,
            "runBindingDigest": run_binding_digest(self.spec, self.plan),
            "previousState": None,
            "state": "planned",
            "currentWaveIndex": 0,
            "activeBlocker": None,
            "blockerHistory": [],
            "waves": [self._wave(0, initial)],
        }
        return self._validate_state(state)

    def _validate_state(self, state: Mapping[str, object]) -> dict[str, object]:
        try:
            _spec, _plan, normalized = validate_run_structure_bindings(
                self.spec, self.plan, state
            )
        except ValueError as exc:
            raise StateError(f"run state is invalid or belongs to another run: {exc}") from exc
        assert normalized is not None
        for wave in normalized["waves"]:
            for entry in wave["branches"]:
                self.registered_worktree(str(entry["storyId"]))
        self._validate_git_sha_chain(normalized)
        return normalized

    def _git_status(self, arguments: list[str]) -> int:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repository.root), *arguments],
                check=False, capture_output=True, shell=False, timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StateError(f"Git object evidence is unavailable: {exc}") from exc
        return result.returncode

    def _validate_git_sha_chain(self, state: Mapping[str, object]) -> None:
        shas = {
            str(self.spec["baseSha"]), str(state["initialIntegrationSha"]),
            str(state["expectedIntegrationSha"]), str(state["lastVerifiedIntegrationSha"]),
        }
        ancestry: list[tuple[str, str]] = [
            (str(self.spec["baseSha"]), str(state["initialIntegrationSha"]))
        ]
        for wave in state["waves"]:
            shas.add(str(wave["startExpectedSha"]))
            for entry in wave["branches"]:
                pre_merge = str(entry["preMergeExpectedSha"])
                shas.add(pre_merge)
                for field in ("mergeSha", "postCheckExpectedSha"):
                    value = entry[field]
                    if value is not None:
                        shas.add(str(value))
                if entry["mergeSha"] is not None:
                    ancestry.append((pre_merge, str(entry["mergeSha"])))
        for sha in sorted(shas):
            if self._git_status(["cat-file", "-e", f"{sha}^{{commit}}"]):
                raise StateError(f"recorded SHA is not a commit object in this repository: {sha}")
        for ancestor, descendant in ancestry:
            if self._git_status(["merge-base", "--is-ancestor", ancestor, descendant]):
                raise StateError(
                    f"recorded SHA ancestry is invalid: {ancestor} is not an ancestor of {descendant}"
                )

    def _registry(self) -> dict[str, object]:
        identity = {
            "repositoryRoot": str(self.repository.root),
            "commonGitDir": str(self.repository.common_git_dir),
            "gitDir": str(self.repository.git_dir),
        }
        digest = "sha256:" + hashlib.sha256(
            (json.dumps(identity, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        ).hexdigest()
        return {
            "schemaVersion": "compass-builder.controller-registry.v1",
            "runId": self.run_id,
            "runBindingDigest": run_binding_digest(self.spec, self.plan),
            "repositoryIdentity": identity,
            "repositoryIdentityDigest": digest,
        }

    def _validate_registry(self) -> dict[str, object]:
        try:
            value = json.loads(
                _read_file_no_follow(
                    self.registry_path, self.run_root, label="durable controller registry"
                ).decode("utf-8-sig")
            )
        except (OSError, UnicodeError, ValueError) as exc:
            raise StateError(f"durable controller registry is unavailable or malformed: {exc}") from exc
        expected = self._registry()
        if not isinstance(value, dict) or set(value) != set(expected):
            raise StateError("durable controller registry has an unsupported closed field set")
        if value != expected:
            raise StateError("durable controller registry does not bind this canonical repository and run")
        return value

    def _validate_publication(self, *, require_bundle: bool = False) -> None:
        self._require_safe_run_root()
        try:
            names = {path.name for path in self.run_root.iterdir()}
        except OSError as exc:
            raise StateError(f"durable run artifact set is unavailable: {exc}") from exc
        without_bundle = {"transaction.json", "controller.json", "state.json"}
        with_bundle = without_bundle | {"plan-bundle.json"}
        allowed = (with_bundle,) if require_bundle else (without_bundle, with_bundle)
        if names not in allowed:
            raise StateError("durable run artifact set is partial or contains unknown files")
        try:
            transaction = json.loads(
                _read_file_no_follow(
                    self.run_root / "transaction.json", self.run_root,
                    label="durable create transaction marker",
                ).decode("utf-8-sig")
            )
        except (UnicodeError, ValueError) as exc:
            raise StateError(f"durable create transaction marker is malformed: {exc}") from exc
        expected = {
            "schemaVersion": "compass-builder.create-transaction.v1",
            "runId": self.run_id,
            "runBindingDigest": run_binding_digest(self.spec, self.plan),
        }
        if transaction != expected:
            raise StateError("durable create transaction marker does not bind this run")

    def load(self) -> dict[str, object]:
        self._validate_publication()
        self._validate_registry()
        try:
            raw = _read_file_no_follow(
                self.path, self.run_root, label="durable run state"
            )
        except OSError as exc:
            raise StateError(f"durable run state is unavailable: {exc}") from exc
        try:
            import json

            decoded = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeError, ValueError) as exc:
            raise StateError(f"durable run state is malformed: {exc}") from exc
        if not isinstance(decoded, dict):
            raise StateError("durable run state must be a JSON object")
        state = self._validate_state(decoded)
        self.compare_and_swap(str(state["expectedIntegrationSha"]))
        return state

    def _require_safe_run_root(self) -> None:
        _require_contained(self.run_root, self.control_root, label="run state directory")
        if _is_reparse(self.run_root):
            raise StateError("run state directory may not be a reparse point")

    def _atomic_replace(self, state: Mapping[str, object]) -> None:
        self.run_root.mkdir(parents=True, exist_ok=True)
        self._require_safe_run_root()
        if self.path.is_symlink() or (self.path.exists() and _is_reparse(self.path)):
            raise StateError("durable run-state leaf may not be a reparse point")
        payload = canonical_json(state, "run-state")
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix="state-", suffix=".tmp", dir=self.run_root, delete=False
            ) as stream:
                temporary = stream.name
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self._require_safe_run_root()
            _reject_existing_reparse_components(Path(temporary), label="state temporary file")
            if self.path.is_symlink() or (self.path.exists() and _is_reparse(self.path)):
                raise StateError("durable run-state leaf changed to a reparse point")
            os.replace(temporary, self.path)
            temporary = None
            try:
                directory = os.open(self.run_root, os.O_RDONLY)
            except OSError:
                directory = None
            if directory is not None:
                try:
                    os.fsync(directory)
                except OSError:
                    pass
                finally:
                    os.close(directory)
        except OSError as exc:
            raise StateError(f"atomic state persistence failed: {exc}") from exc
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink()
                except OSError:
                    pass

    def create(
        self,
        state: Mapping[str, object],
        *,
        execution_bundle: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        normalized = self._validate_state(state)
        self.compare_and_swap(str(normalized["expectedIntegrationSha"]))
        bundle: dict[str, object] | None = None
        if execution_bundle is not None:
            bundle = validate_execution_bundle(execution_bundle, self.repository.root)
            if (
                canonical_json(bundle["runSpec"], "run-spec")
                != canonical_json(self.spec, "run-spec")
                or canonical_json(bundle["wavePlan"], "wave-plan")
                != canonical_json(self.plan, "wave-plan")
            ):
                raise StateError("execution bundle does not match this store's immutable run inputs")

        runs_root = self.control_root / "runs"
        _require_contained(runs_root, self.control_root, label="runs publication root")
        runs_root.mkdir(parents=True, exist_ok=True)
        _reject_existing_reparse_components(runs_root, label="runs publication root")
        if self.run_root.exists() or self.run_root.is_symlink():
            raise StateError("durable run artifact set already exists or is partial")
        prefix = f".{self.run_id}.create-"
        if any(path.name.startswith(prefix) for path in runs_root.iterdir()):
            raise StateError("a partial create transaction exists; refusing silent repair")
        try:
            staging = Path(tempfile.mkdtemp(prefix=prefix, dir=runs_root))
        except OSError as exc:
            raise StateError(f"create transaction could not start: {exc}") from exc
        _require_contained(staging, runs_root, label="create transaction")
        try:
            transaction = {
                "schemaVersion": "compass-builder.create-transaction.v1",
                "runId": self.run_id,
                "runBindingDigest": run_binding_digest(self.spec, self.plan),
            }
            _write_new_file(
                staging / "transaction.json", canonical_json(transaction), staging,
                label="create transaction marker",
            )
            _write_new_file(
                staging / "controller.json", canonical_json(self._registry()), staging,
                label="controller registry",
            )
            if bundle is not None:
                _write_new_file(
                    staging / "plan-bundle.json", canonical_json(bundle), staging,
                    label="execution bundle",
                )
            _write_new_file(
                staging / "state.json", canonical_json(normalized, "run-state"), staging,
                label="initial run state",
            )
            try:
                directory = os.open(staging, os.O_RDONLY)
            except OSError:
                directory = None
            if directory is not None:
                try:
                    os.fsync(directory)
                except OSError:
                    pass
                finally:
                    os.close(directory)
            _reject_existing_reparse_components(staging, label="create transaction")
            if self.run_root.exists() or self.run_root.is_symlink():
                raise StateError("run artifact target appeared during publication")
            os.rename(staging, self.run_root)
        except (OSError, StateError) as exc:
            raise StateError(f"create publication failed closed: {exc}") from exc
        return normalized

    def write_transition(
        self,
        previous: Mapping[str, object],
        current: Mapping[str, object],
    ) -> dict[str, object]:
        before = self._validate_state(previous)
        after = self._validate_state(current)
        try:
            validate_run_state_transition(before, after)
        except ValueError as exc:
            raise StateError(f"run state transition is invalid: {exc}") from exc
        on_disk = self.load()
        if canonical_json(on_disk, "run-state") != canonical_json(before, "run-state"):
            raise StateError("durable state changed since the proposed predecessor was read")
        self.compare_and_swap(str(after["expectedIntegrationSha"]))
        self._atomic_replace(after)
        return after

    def resume_state(
        self, blocked: Mapping[str, object]
    ) -> dict[str, object]:
        previous = self._validate_state(blocked)
        if previous["state"] != "blocked" or previous["activeBlocker"] is None:
            raise StateError("resume requires one durable active blocker")
        self.compare_and_swap(str(previous["expectedIntegrationSha"]))
        current = copy.deepcopy(previous)
        blocker = current["activeBlocker"]
        current["previousState"] = "blocked"
        current["state"] = blocker["resumeState"]
        current["activeBlocker"] = None
        story_id = blocker["storyId"]
        phase = blocker["phase"]
        if story_id is not None and phase not in {"controller", "pre-dispatch"}:
            entries = current["waves"][current["currentWaveIndex"]]["branches"]
            matches = [entry for entry in entries if entry["storyId"] == story_id]
            if len(matches) != 1:
                raise StateError("active blocker does not identify one registered current-wave branch")
            entry = matches[0]
            if phase in {"dispatch", "worker"}:
                entry.update(workerState="pending", verificationState="pending", integrationState="pending")
            elif phase == "verification":
                entry.update(verificationState="pending", integrationState="pending")
            elif phase == "pre-merge":
                entry["integrationState"] = "worker-verified"
            elif phase == "post-merge-check":
                entry["integrationState"] = "merged"
        try:
            _before, normalized = validate_run_state_transition(previous, current)
        except ValueError as exc:
            raise StateError(f"recorded blocker cannot be resumed safely: {exc}") from exc
        return normalized

    def next_wave_state(
        self, verified: Mapping[str, object]
    ) -> dict[str, object]:
        previous = self._validate_state(verified)
        if previous["state"] != "wave-verified":
            raise StateError("a next wave may open only from wave-verified")
        next_index = int(previous["currentWaveIndex"]) + 1
        if next_index >= len(self.plan["waves"]):
            raise StateError("the immutable plan has no next dependency wave")
        self.compare_and_swap(str(previous["expectedIntegrationSha"]))
        current = copy.deepcopy(previous)
        current["previousState"] = "wave-verified"
        current["state"] = "dispatching"
        current["currentWaveIndex"] = next_index
        current["waves"].append(self._wave(next_index, str(previous["expectedIntegrationSha"])))
        try:
            _before, normalized = validate_run_state_transition(previous, current)
        except ValueError as exc:
            raise StateError(f"next dependency wave cannot be opened safely: {exc}") from exc
        return normalized

    def dry_run_projection(self, state: Mapping[str, object]) -> dict[str, object]:
        normalized = self._validate_state(state)
        entries = normalized["waves"][normalized["currentWaveIndex"]]["branches"]
        pending = next(
            (entry["storyId"] for entry in entries if entry["integrationState"] != "integration-verified"),
            None,
        )
        return {
            "schemaVersion": "compass-builder.controller-dry-run.v1",
            "runId": self.run_id,
            "state": normalized["state"],
            "currentWaveIndex": normalized["currentWaveIndex"],
            "expectedIntegrationSha": normalized["expectedIntegrationSha"],
            "lastVerifiedIntegrationSha": normalized["lastVerifiedIntegrationSha"],
            "firstIncompleteStoryId": pending,
            "registeredWorktrees": list(self.registrations()),
            "workerExecutionAllowed": False,
        }


def build_execution_bundle(
    run_spec: Mapping[str, object],
    wave_plan: Mapping[str, object],
    host_capabilities: Mapping[str, object],
    planning_timestamp: str,
    repository: Path,
) -> dict[str, object]:
    """Build the closed public plan artifact consumed unchanged by ``run``."""

    identity = resolve_repository(repository)
    try:
        spec, plan, _ = validate_run_bindings(
            run_spec, wave_plan, host_capabilities=host_capabilities,
            planning_timestamp=planning_timestamp,
        )
    except ValueError as exc:
        raise StateError(f"execution bundle bindings are invalid: {exc}") from exc
    bundle = {
        "schemaVersion": "compass-builder.plan-bundle.v1",
        "runSpec": spec,
        "wavePlan": plan,
        "hostCapabilities": copy.deepcopy(dict(host_capabilities)),
        "planningTimestamp": planning_timestamp,
        "repositoryIdentity": {
            "repositoryRoot": str(identity.root),
            "commonGitDir": str(identity.common_git_dir),
            "gitDir": str(identity.git_dir),
        },
    }
    return validate_execution_bundle(bundle, identity.root)


def validate_execution_bundle(
    value: Mapping[str, object], repository: Path | None = None
) -> dict[str, object]:
    """Validate a closed execution-ready plan bundle and optional repository replay."""

    required = {
        "schemaVersion", "runSpec", "wavePlan", "hostCapabilities",
        "planningTimestamp", "repositoryIdentity",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise StateError("execution bundle must match the closed compass-builder.plan-bundle.v1 field set")
    bundle = copy.deepcopy(dict(value))
    if bundle["schemaVersion"] != "compass-builder.plan-bundle.v1":
        raise StateError("execution bundle has an unsupported schemaVersion")
    if not all(isinstance(bundle[field], Mapping) for field in ("runSpec", "wavePlan", "hostCapabilities")):
        raise StateError("execution bundle contracts must be JSON objects")
    if not isinstance(bundle["planningTimestamp"], str):
        raise StateError("execution bundle planningTimestamp must be text")
    try:
        spec, plan, _ = validate_run_bindings(
            bundle["runSpec"], bundle["wavePlan"],
            host_capabilities=bundle["hostCapabilities"],
            planning_timestamp=bundle["planningTimestamp"],
        )
    except ValueError as exc:
        raise StateError(f"execution bundle bindings are invalid: {exc}") from exc
    identity_value = bundle["repositoryIdentity"]
    identity_fields = {"repositoryRoot", "commonGitDir", "gitDir"}
    if not isinstance(identity_value, dict) or set(identity_value) != identity_fields:
        raise StateError("execution bundle repositoryIdentity has an unsupported field set")
    for field in sorted(identity_fields):
        text = identity_value[field]
        if (
            not isinstance(text, str) or not text or text != text.strip()
            or len(text) > 1024
            or any(ord(character) < 32 or ord(character) == 127 for character in text)
        ):
            raise StateError(
                f"execution bundle repositoryIdentity.{field} must be bounded clean text"
            )
    if repository is not None:
        actual = resolve_repository(repository)
        expected = {
            "repositoryRoot": str(actual.root), "commonGitDir": str(actual.common_git_dir),
            "gitDir": str(actual.git_dir),
        }
        if identity_value != expected:
            raise StateError("execution bundle repository identity does not match this checkout")
    bundle["runSpec"] = spec
    bundle["wavePlan"] = plan
    canonical_json(bundle)
    return bundle


def load_run_bundle(repository: Path, run_id: str) -> dict[str, object]:
    """Load the controller-persisted public execution bundle for resume."""

    validate_run_id(run_id, "runId")
    identity = resolve_repository(repository)
    root = identity.root / ".compass-builder" / "runs" / run_id
    _require_contained(root, identity.root / ".compass-builder", label="resume run root")
    path = root / "plan-bundle.json"
    try:
        names = {item.name for item in root.iterdir()}
    except OSError as exc:
        raise StateError(f"durable run artifact set is unavailable: {exc}") from exc
    if names != {"transaction.json", "controller.json", "state.json", "plan-bundle.json"}:
        raise StateError("durable run artifact set is partial or contains unknown files")
    try:
        value = json.loads(
            _read_file_no_follow(path, root, label="durable execution bundle").decode(
                "utf-8-sig"
            )
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise StateError(f"durable execution bundle is unavailable or malformed: {exc}") from exc
    if not isinstance(value, dict):
        raise StateError("durable execution bundle must be a JSON object")
    return validate_execution_bundle(value, identity.root)


def load_run_inputs(repository: Path, run_id: str) -> tuple[dict[str, object], dict[str, object]]:
    bundle = load_run_bundle(repository, run_id)
    return bundle["runSpec"], bundle["wavePlan"]


__all__ = [
    "RepositoryIdentity", "StateError", "StateStore", "build_execution_bundle",
    "load_run_bundle", "load_run_inputs", "resolve_repository",
    "validate_execution_bundle",
]
