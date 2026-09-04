"""Controller-owned state transitions and repository-bound CAS validation.

Worker execution and Git mutation stay in their dedicated controllers; this
module publishes only validated state transitions and delegates auxiliary
immutable evidence storage to :mod:`durable_artifacts`.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping

from .git_environment import (
    GitEnvironment, load_git_environment, validate_git_environment,
)
from .durable_artifacts import (
    ArtifactJournal, DIRECTORIES as RUN_DIRECTORY_ARTIFACTS,
    accepts as accepts_artifacts, decode_canonical_mapping,
)
from .git_objects import GitObjectError, reject_active_grafts
from .errors import StateError
from .launcher import validate_launch_authority, validate_launch_record
from .repository import RepositoryIdentity, git_text as _git, resolve_repository
from .secure_files import (
    is_reparse as _is_reparse, read_no_follow, reject_reparse_components,
    require_contained as _require_contained, write_new_no_follow,
)
_reject_existing_reparse_components = reject_reparse_components
from ._validation import branch as validate_branch
from ._validation import run_id as validate_run_id
from .models import (
    canonical_digest,
    canonical_json,
    run_binding_digest,
    validate_run_bindings,
    validate_run_state,
    validate_run_state_transition,
    validate_run_structure_bindings,
    validate_retry_evidence,
    validate_worker_usage_with_schema,
)


_WORKER_USAGE_SCHEMA = (
    Path(__file__).resolve().parents[1] / "schemas" / "worker-usage.schema.json"
)


def _read_file_no_follow(path: Path, root: Path, *, label: str) -> bytes:
    return read_no_follow(path, root, label=label, max_bytes=16_777_216)


def _write_new_file(path: Path, payload: bytes, root: Path, *, label: str) -> None:
    write_new_no_follow(path, payload, root, label=label)


class StateStore:
    """Validate and persist one controller-owned run under its checkout."""

    def __init__(
        self,
        repository: Path,
        run_spec: Mapping[str, object],
        wave_plan: Mapping[str, object],
        git_environment: GitEnvironment | None = None,
    ) -> None:
        self.git_environment = (
            validate_git_environment(git_environment) if git_environment is not None else None
        )
        self.repository = resolve_repository(Path(repository), self.git_environment)
        try:
            self.spec, self.plan, _ = validate_run_structure_bindings(run_spec, wave_plan)
        except ValueError as exc:
            raise StateError(f"run bindings are invalid: {exc}") from exc
        self.run_id = str(self.spec["runId"])
        self.control_root = self.repository.root / ".compass-builder"
        self.run_root = self.control_root / "runs" / self.run_id
        repository_key = hashlib.sha256(
            os.path.normcase(str(self.repository.root)).encode("utf-8")
        ).hexdigest()[:32]
        self.workspace_control_root = (
            Path(tempfile.gettempdir()).resolve(strict=True)
            / "compass-builder-workspaces"
            / repository_key
        )
        self.worktree_root = self.workspace_control_root / self.run_id
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
                env=(dict(self.git_environment.environment) if self.git_environment else None),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StateError(f"ignore evidence is unavailable: {exc}") from exc
        if result.returncode != 0:
            raise StateError(".compass-builder must be ignored before controller state is used")
        tracked = _git(
            self.repository.root, ["ls-files", "--", ".compass-builder"],
            git_environment=self.git_environment,
        )
        if tracked.strip():
            raise StateError(".compass-builder must be absent from the repository index")
        _require_contained(self.run_root, self.control_root, label="run state path")
        _require_contained(
            self.worktree_root, self.workspace_control_root,
            label="worker checkout registry root",
        )
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
        resolved = _require_contained(
            target, self.worktree_root, label="registered worker checkout"
        )
        forbidden = {
            self.repository.root,
            self.repository.common_git_dir,
            self.repository.git_dir,
            self.control_root.resolve(strict=False),
            self.workspace_control_root.resolve(strict=False),
            self.worktree_root.resolve(strict=False),
        }
        if resolved in forbidden:
            raise StateError(
                "registered worker checkout targets a protected repository/controller path"
            )
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

    def launch_record_path(self, story_id: str, attempt: int) -> Path:
        """Return the collision-free durable filename for one planned launch."""

        if story_id not in self._branches:
            raise StateError(f"story {story_id!r} is not registered in the immutable plan")
        if type(attempt) is not int or attempt not in (1, 2):
            raise StateError("launch attempt must be 1 or 2")
        name = (
            f"{story_id}.json"
            if attempt == 1
            else f"__attempt-2__{story_id}.json"
        )
        return _require_contained(
            self.run_root / "launch-records" / name,
            self.control_root,
            label="controller-owned launch record",
        )

    def observed_integration_head(self) -> str:
        self._reject_grafts()
        ref = f"refs/heads/{self.spec['integrationBranch']}^{{commit}}"
        value = _git(
            self.repository.root, ["rev-parse", "--verify", ref],
            git_environment=self.git_environment,
        ).strip()
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

    def _wave(
        self, index: int, expected_sha: str, *, start_workers: bool = False
    ) -> dict[str, object]:
        plan_wave = self.plan["waves"][index]
        return {
            "waveIndex": index,
            "startExpectedSha": expected_sha,
            "branches": [
                {
                    "storyId": story_id,
                    "branch": self._branches[story_id],
                    "workerState": "running" if start_workers else "pending",
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
        self._reject_grafts()
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repository.root), *arguments],
                check=False, capture_output=True, shell=False, timeout=20,
                env=(dict(self.git_environment.environment) if self.git_environment else None),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StateError(f"Git object evidence is unavailable: {exc}") from exc
        return result.returncode

    def _reject_grafts(self) -> None:
        try:
            reject_active_grafts(self.repository.common_git_dir)
        except GitObjectError as exc:
            raise StateError(str(exc)) from exc

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
        if not accepts_artifacts(names):
            raise StateError("durable run artifact set is partial or contains unknown files")
        if require_bundle and "plan-bundle.json" not in names:
            raise StateError("durable run artifact set is missing its immutable plan bundle")
        gate_artifacts = {"gate-evidence", "gate-execution-intents"} & names
        if gate_artifacts:
            if "plan-bundle.json" not in names:
                raise StateError("durable gate artifacts require an immutable v2 plan bundle")
            try:
                gate_bundle = json.loads(_read_file_no_follow(
                    self.bundle_path, self.run_root, label="durable gated execution bundle"
                ).decode("utf-8-sig"))
                validated_gate_bundle = validate_execution_bundle(
                    gate_bundle, self.repository.root, self.git_environment
                )
            except (OSError, UnicodeError, ValueError) as exc:
                raise StateError(f"durable gate artifact bundle is invalid: {exc}") from exc
            if validated_gate_bundle["schemaVersion"] != "compass-builder.plan-bundle.v2":
                raise StateError("durable gate artifacts are permitted only for a v2 plan bundle")
        for directory_name in RUN_DIRECTORY_ARTIFACTS:
            candidate = self.run_root / directory_name
            if directory_name in names and (
                not candidate.is_dir() or _is_reparse(candidate)
            ):
                raise StateError(f"durable {directory_name} artifact must be a real directory")
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

    def load_durable_state(self) -> dict[str, object]:
        """Load validated durable state without asserting current integration HEAD."""

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
        return self._validate_state(decoded)

    def load(self) -> dict[str, object]:
        state = self.load_durable_state()
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
            bundle = validate_execution_bundle(
                execution_bundle, self.repository.root, self.git_environment
            )
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

    def record_integration_merge(
        self, previous: Mapping[str, object], *, story_id: str, merge_sha: str
    ) -> dict[str, object]:
        """Atomically bind one already-proven Git merge to the durable ordered ledger."""

        before = self._validate_state(previous)
        current = copy.deepcopy(before)
        entries = current["waves"][current["currentWaveIndex"]]["branches"]
        matches = [entry for entry in entries if entry["storyId"] == story_id]
        if len(matches) != 1:
            raise StateError("integration merge does not identify one current-wave story")
        current.update(
            previousState="wave-merging", state="wave-integrated-unverified",
            expectedIntegrationSha=merge_sha,
        )
        matches[0].update(integrationState="merged", mergeSha=merge_sha)
        after = self._validate_state(current)
        try:
            validate_run_state_transition(before, after)
        except ValueError as exc:
            raise StateError(f"post-merge run state transition is invalid: {exc}") from exc
        self._validate_publication()
        self._validate_registry()
        raw = _read_file_no_follow(
            self.path, self.run_root, label="durable pre-merge run state"
        )
        if raw != canonical_json(before, "run-state"):
            raise StateError("durable state changed while Git merge was in progress")
        self.compare_and_swap(merge_sha)
        self._atomic_replace(after)
        return after

    def _record_receipt(self, directory_name: str, record: Mapping[str, object]) -> Path:
        try:
            return ArtifactJournal(self.run_root, self.control_root).record(directory_name, record)
        except (OSError, ValueError) as exc:
            raise StateError(str(exc)) from exc

    def _durable_launch_authority(
        self, story_ids: set[str],
    ) -> tuple[dict[str, object], GitEnvironment, dict[str, str]]:
        """Load immutable inputs needed to authorize durable launch evidence."""

        self._validate_publication(require_bundle=True)
        base_environment = (
            self.git_environment.environment if self.git_environment is not None else None
        )
        try:
            environment = load_git_environment(
                self.run_root / "git-environment",
                base_environment=base_environment,
            )
            payload = _read_file_no_follow(
                self.bundle_path, self.run_root, label="durable execution bundle"
            )
            decoded = decode_canonical_mapping(
                payload, label="durable execution bundle"
            )
            bundle = validate_execution_bundle(
                decoded, self.repository.root, environment
            )
        except (OSError, ValueError) as exc:
            raise StateError(
                f"durable launch authority is unavailable or invalid: {exc}"
            ) from exc
        if bundle != decoded:
            raise StateError("durable execution bundle changes under canonical validation")
        if (
            canonical_json(bundle["runSpec"]) != canonical_json(self.spec)
            or canonical_json(bundle["wavePlan"]) != canonical_json(self.plan)
        ):
            raise StateError(
                "durable execution bundle does not match this store's immutable run inputs"
            )
        state = self.load_durable_state()
        starts: dict[str, str] = {}
        for story_id in story_ids:
            matching = [
                str(wave["startExpectedSha"])
                for wave in state["waves"]
                if any(
                    entry["storyId"] == story_id for entry in wave["branches"]
                )
            ]
            if len(matching) != 1:
                raise StateError(
                    "durable state does not contain one authoritative story wave start"
                )
            starts[story_id] = matching[0]
        return bundle, environment, starts

    def _bind_retry_evidence(
        self, record: Mapping[str, object],
        launches: Mapping[tuple[str, int], Mapping[str, object]],
    ) -> dict[str, object]:
        try:
            normalized = validate_retry_evidence(record)
        except ValueError as exc:
            raise StateError(f"durable retry evidence is invalid: {exc}") from exc
        if normalized["runId"] != self.run_id:
            raise StateError("durable retry evidence belongs to another run")
        story_id = str(normalized["storyId"])
        if story_id not in self._branches:
            raise StateError("durable retry evidence does not identify a planned story")
        previous = launches.get((story_id, 1))
        if previous is None:
            raise StateError("durable retry evidence has no authoritative first launch")
        if normalized["previousLaunchDigest"] != canonical_digest(previous):
            raise StateError("durable retry evidence does not bind the first launch digest")
        return normalized

    def _retry_evidence_records_for_launches(
        self, launches: Mapping[tuple[str, int], Mapping[str, object]],
    ) -> tuple[dict[str, object], ...]:
        try:
            raw = ArtifactJournal(
                self.run_root, self.control_root
            ).read("retry-evidence")
        except (OSError, ValueError) as exc:
            raise StateError(f"durable retry evidence is unavailable: {exc}") from exc
        records = tuple(self._bind_retry_evidence(item, launches) for item in raw)
        identities = [
            (item["runId"], item["storyId"], item["attempt"], item["previousLaunchDigest"])
            for item in records
        ]
        if len(set(identities)) != len(identities):
            raise StateError("durable retry evidence contains an ambiguous attempt identity")
        return tuple(sorted(
            records,
            key=lambda item: (
                str(item["storyId"]), int(item["attempt"]),
                str(item["evidenceDigest"]),
            ),
        ))

    def _validated_launch_records(
        self, *, first_only: bool = False,
    ) -> dict[tuple[str, int], dict[str, object]]:
        directory = self.run_root / "launch-records"
        _require_contained(
            directory, self.control_root, label="durable launch record directory"
        )
        if not directory.is_dir() or _is_reparse(directory):
            raise StateError("durable launch record directory is missing or unsafe")
        expected = {
            self.launch_record_path(story_id, attempt).name: (story_id, attempt)
            for story_id in self._branches
            for attempt in (1, 2)
        }
        try:
            entries = tuple(directory.iterdir())
        except OSError as exc:
            raise StateError(f"durable launch records are unavailable: {exc}") from exc
        if len(entries) > len(expected):
            raise StateError("durable launch records are ambiguous or exceed their bound")
        parsed: dict[tuple[str, int], dict[str, object]] = {}
        for path in entries:
            identity = expected.get(path.name)
            if identity is None:
                raise StateError("durable launch records contain an unknown or ambiguous entry")
            if _is_reparse(path) or not path.is_file():
                raise StateError("durable launch record is unsafe")
            try:
                payload = read_no_follow(
                    path, self.control_root, label="durable launch record",
                    max_bytes=1_048_576,
                )
                decoded = decode_canonical_mapping(
                    payload, label="durable launch record"
                )
                normalized = validate_launch_record(decoded)
            except (OSError, ValueError) as exc:
                raise StateError(f"durable launch record is invalid: {exc}") from exc
            if normalized != decoded:
                raise StateError("durable launch record changes under canonical validation")
            story_id, attempt = identity
            key = (story_id, attempt)
            if key in parsed:
                raise StateError("durable launch records are ambiguous")
            parsed[key] = normalized
        bundle, environment, starts = self._durable_launch_authority({
            story_id for story_id, _attempt in parsed
        })
        launches: dict[tuple[str, int], dict[str, object]] = {}
        for key in sorted(parsed):
            story_id, attempt = key
            if attempt != 1:
                continue
            launch = parsed[key]
            try:
                launches[key] = validate_launch_authority(
                    launch, bundle["runSpec"], bundle["wavePlan"],
                    bundle["hostCapabilities"],
                    planning_timestamp=str(bundle["planningTimestamp"]),
                    story_id=story_id, attempt=attempt,
                    registered_worktree=self.registered_worktree(story_id),
                    worker_start_sha=starts[story_id],
                    git_environment=environment,
                )
            except ValueError as exc:
                raise StateError(
                    f"durable launch record lacks controller authority: {exc}"
                ) from exc
        if first_only:
            return launches
        retry_records = self._retry_evidence_records_for_launches(launches)
        for key in sorted(parsed):
            story_id, attempt = key
            if attempt != 2:
                continue
            launch = parsed[key]
            previous = launches.get((story_id, 1))
            if previous is None:
                raise StateError("second launch has no authoritative first launch")
            matching = [
                item for item in retry_records
                if item["storyId"] == story_id
                and item["attempt"] == attempt
                and item["previousLaunchDigest"] == canonical_digest(previous)
                and item["evidenceDigest"] == launch["retryEvidenceDigest"]
            ]
            if len(matching) != 1:
                raise StateError(
                    "second launch does not have one exact durable retry evidence record"
                )
            try:
                launches[key] = validate_launch_authority(
                    launch, bundle["runSpec"], bundle["wavePlan"],
                    bundle["hostCapabilities"],
                    planning_timestamp=str(bundle["planningTimestamp"]),
                    story_id=story_id, attempt=attempt,
                    registered_worktree=self.registered_worktree(story_id),
                    worker_start_sha=starts[story_id],
                    git_environment=environment,
                    previous_launch=previous,
                    retry_evidence=matching[0],
                )
            except ValueError as exc:
                raise StateError(
                    f"durable launch record lacks controller authority: {exc}"
                ) from exc
        return launches

    def record_retry_evidence(
        self, record: Mapping[str, object]
    ) -> dict[str, object]:
        """Persist one launch-bound authorization candidate for attempt two."""

        try:
            normalized = validate_retry_evidence(record)
        except ValueError as exc:
            raise StateError(f"durable retry evidence is invalid: {exc}") from exc
        if normalized["runId"] != self.run_id:
            raise StateError("durable retry evidence belongs to another run")
        if normalized["storyId"] not in self._branches:
            raise StateError("durable retry evidence does not identify a planned story")
        first_launches = self._validated_launch_records(first_only=True)
        normalized = self._bind_retry_evidence(normalized, first_launches)
        existing = self.retry_evidence_records()
        identity = (
            normalized["runId"], normalized["storyId"], normalized["attempt"],
            normalized["previousLaunchDigest"],
        )
        for item in existing:
            item_identity = (
                item["runId"], item["storyId"], item["attempt"],
                item["previousLaunchDigest"],
            )
            if item_identity == identity and item != normalized:
                raise StateError(
                    "retry evidence attempt already has different durable authority"
                )
        self._record_receipt("retry-evidence", normalized)
        return normalized

    def retry_evidence_records(self) -> tuple[dict[str, object], ...]:
        """Read canonical, uniquely launch-bound retry evidence."""

        self._validate_publication()
        try:
            raw = ArtifactJournal(
                self.run_root, self.control_root
            ).read("retry-evidence")
        except (OSError, ValueError) as exc:
            raise StateError(f"durable retry evidence is unavailable: {exc}") from exc
        if not raw:
            return ()
        first_launches = self._validated_launch_records(first_only=True)
        return self._retry_evidence_records_for_launches(first_launches)

    def _validate_worker_usage_record(
        self, record: Mapping[str, object],
        launches: Mapping[tuple[str, int], Mapping[str, object]] | None = None,
    ) -> dict[str, object]:
        try:
            schema = json.loads(_WORKER_USAGE_SCHEMA.read_text(encoding="utf-8"))
            normalized = validate_worker_usage_with_schema(schema, record)
        except (OSError, UnicodeError, ValueError) as exc:
            raise StateError(f"worker usage evidence is invalid: {exc}") from exc
        if normalized["runId"] != self.run_id:
            raise StateError("worker usage evidence belongs to another run")
        if normalized["storyId"] not in self._branches:
            raise StateError("worker usage evidence does not identify a planned story")
        if normalized["exactModel"] != self.spec["exactModel"]:
            raise StateError("worker usage evidence does not bind the run's exact model")
        durable_launches = (
            self._validated_launch_records() if launches is None else launches
        )
        identity = (str(normalized["storyId"]), int(normalized["attempt"]))
        launch = durable_launches.get(identity)
        if launch is None:
            raise StateError("worker usage evidence has no matching durable launch record")
        for field in ("runId", "storyId", "attempt", "exactModel", "effort"):
            if normalized[field] != launch[field]:
                raise StateError(
                    f"worker usage evidence does not match durable launch {field}"
                )
        if normalized["launchDigest"] != canonical_digest(launch):
            raise StateError(
                "worker usage evidence launchDigest does not match its durable launch"
            )
        return normalized

    def record_worker_usage(
        self, record: Mapping[str, object]
    ) -> dict[str, object]:
        """Persist one validated immutable usage record for a launch attempt."""

        normalized = self._validate_worker_usage_record(record)
        existing = self.worker_usage_records()
        identity = (normalized["storyId"], normalized["attempt"])
        for item in existing:
            if (item["storyId"], item["attempt"]) == identity and item != normalized:
                raise StateError("worker usage attempt already has different durable evidence")
        self._record_receipt("worker-usage", normalized)
        return normalized

    def worker_usage_records(self) -> tuple[dict[str, object], ...]:
        """Read and fully validate deterministic usage evidence for this run."""

        self._validate_publication()
        try:
            raw = ArtifactJournal(
                self.run_root, self.control_root
            ).read("worker-usage")
        except (OSError, ValueError) as exc:
            raise StateError(str(exc)) from exc
        if not raw:
            return ()
        launches = self._validated_launch_records()
        records = tuple(
            self._validate_worker_usage_record(item, launches) for item in raw
        )
        identities = [(item["storyId"], item["attempt"]) for item in records]
        if len(set(identities)) != len(identities):
            raise StateError("durable worker usage contains duplicate launch attempts")
        return tuple(sorted(
            records, key=lambda item: (str(item["storyId"]), int(item["attempt"]))
        ))

    def record_merge_intent(
        self, previous: Mapping[str, object], *, story_id: str,
        expected_sha: str, verified_head_sha: str,
    ) -> dict[str, object]:
        """Persist immutable recovery evidence before changing integration Git."""

        before = self._validate_state(previous)
        if before["state"] != "wave-merging" or before["expectedIntegrationSha"] != expected_sha:
            raise StateError("merge intent does not bind the active integration CAS")
        record = {
            "schemaVersion": "compass-builder.merge-intent.v1", "runId": self.run_id,
            "storyId": story_id, "expectedSha": expected_sha,
            "verifiedHeadSha": verified_head_sha,
        }
        self._record_receipt("merge-intents", record)
        return record

    def record_blocker(
        self,
        previous: Mapping[str, object],
        *,
        reason: str,
        evidence_digest: str,
        story_id: str | None = None,
        phase: str = "controller",
        resume_state: str | None = None,
    ) -> dict[str, object] | None:
        """Best-effort active blocker plus immutable failure evidence."""

        before = self._validate_state(previous)
        self.record_failure_evidence(
            blocked_from_state=str(before["state"]), reason=reason,
            evidence_digest=evidence_digest, story_id=story_id,
        )
        blocked = copy.deepcopy(before)
        if phase == "verification":
            if before["state"] != "wave-workers-complete" or story_id is None:
                raise StateError("verification blocker requires one completed current-wave story")
            entries = blocked["waves"][blocked["currentWaveIndex"]]["branches"]
            matches = [entry for entry in entries if entry["storyId"] == story_id]
            if len(matches) != 1:
                raise StateError("verification blocker does not identify one current-wave story")
            matches[0].update(verificationState="failed", integrationState="blocked")
        elif phase != "controller":
            raise StateError("record_blocker received an unsupported phase")
        blocker = {
            "blockerId": f"controller-{evidence_digest[7:23]}",
            "blockedFromState": before["state"], "phase": phase,
            "storyId": story_id, "reason": reason[:2000],
            "evidenceDigest": evidence_digest,
            "resumeState": resume_state or before["state"],
        }
        blocked.update(
            previousState=before["state"], state="blocked", activeBlocker=blocker,
            blockerHistory=[*blocked["blockerHistory"], blocker],
        )
        try:
            return self.write_transition(before, blocked)
        except StateError:
            return None

    def record_failure_evidence(
        self, *, blocked_from_state: str, reason: str,
        evidence_digest: str, story_id: str | None = None,
        observed_head: str | None = None,
    ) -> dict[str, object]:
        """Append immutable failure evidence without changing shared run state."""

        record = {
            "schemaVersion": "compass-builder.failure.v1", "runId": self.run_id,
            "blockedFromState": blocked_from_state, "storyId": story_id,
            "reason": reason[:2000], "evidenceDigest": evidence_digest,
            "observedHead": observed_head,
        }
        self._record_receipt("failure-records", record)
        return record

    def failure_records(self) -> tuple[dict[str, object], ...]:
        self._validate_publication()
        try:
            return ArtifactJournal(self.run_root, self.control_root).read("failure-records")
        except (OSError, ValueError) as exc:
            raise StateError(str(exc)) from exc

    def cleanup_progress(self) -> tuple[dict[str, object], ...]:
        self._validate_publication()
        try:
            return ArtifactJournal(self.run_root, self.control_root).read("cleanup-progress")
        except (OSError, ValueError) as exc:
            raise StateError(str(exc)) from exc

    def merge_intents(self) -> tuple[dict[str, object], ...]:
        self._validate_publication()
        try:
            records = ArtifactJournal(self.run_root, self.control_root).read("merge-intents")
        except (OSError, ValueError) as exc:
            raise StateError(str(exc)) from exc
        expected = {
            "schemaVersion", "runId", "storyId", "expectedSha", "verifiedHeadSha",
        }
        for record in records:
            if set(record) != expected or record["schemaVersion"] != "compass-builder.merge-intent.v1":
                raise StateError("durable merge intent is malformed")
            if record["runId"] != self.run_id:
                raise StateError("durable merge intent belongs to another run")
            for field in ("expectedSha", "verifiedHeadSha"):
                value = record[field]
                if not isinstance(value, str) or len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
                    raise StateError("durable merge intent contains an invalid commit identity")
        return records

    def record_cleanup_progress(
        self, *, story_id: str, worktree: str, head_sha: str, status: str
    ) -> dict[str, object]:
        if status not in {"removing", "removed"}:
            raise StateError("cleanup progress status is invalid")
        record = {
            "schemaVersion": "compass-builder.cleanup-progress.v1", "runId": self.run_id,
            "storyId": story_id, "worktree": worktree, "headSha": head_sha,
            "status": status,
        }
        self._record_receipt("cleanup-progress", record)
        return record

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
        self, verified: Mapping[str, object], *, start_workers: bool = False
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
        current["waves"].append(self._wave(
            next_index, str(previous["expectedIntegrationSha"]),
            start_workers=start_workers,
        ))
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


from .execution_bundle import (
    build_execution_bundle, build_gated_execution_bundle, load_run_bundle, load_run_inputs,
    validate_execution_bundle,
)


__all__ = [
    "RepositoryIdentity", "StateError", "StateStore", "build_execution_bundle",
    "build_gated_execution_bundle",
    "load_run_bundle", "load_run_inputs", "resolve_repository",
    "validate_execution_bundle",
]
