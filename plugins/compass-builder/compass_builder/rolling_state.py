"""Durable v2 rolling state and event publication without worker execution."""

from __future__ import annotations

import copy
import ctypes
import errno
import json
import os
import secrets
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import secure_files as _secure_files
from ._rolling_models import (
    MAX_PIPELINE_EVENTS,
    validate_dispatch_record_bindings,
    validate_pipeline_event_chain,
    validate_pipeline_event_shape,
    validate_rolling_execution_bundle,
    validate_rolling_state_bindings,
)
from ._validation import (
    canonical_data, canonical_digest, digest, enum, identifier, integer, run_id,
)
from .errors import StateError
from .repository import git_text, resolve_repository
from .secure_files import (
    is_reparse, read_no_follow, reject_reparse_components, require_contained,
)


MAX_STATE_BYTES = 2_097_152
MAX_BUNDLE_BYTES = 4_194_304
MAX_RECORD_BYTES = 1_048_576
_ROOT_NAMES = {
    "controller.json", "create-transaction.json", "dispatch-records", "events",
    "execution-bundle.json", "state.json", "transactions",
}
_TRANSACTION_FIELDS = {
    "schemaVersion", "runId", "sequence", "operation", "previousStateDigest",
    "eventDigest", "nextStateDigest", "evidenceDigest", "input", "inputDigest",
}
_ArtifactMap = dict[int, dict[str, Any]]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise StateError(message)


class RollingStateStore:
    """Own one isolated v2 rolling run's canonical durable state and evidence."""

    def __init__(self, repository: Path, execution_bundle: Mapping[str, object]) -> None:
        self.repository = resolve_repository(Path(repository))
        if not isinstance(execution_bundle, Mapping):
            raise StateError("rolling execution bundle must be a mapping/decoded JSON object")
        try:
            self.bundle = validate_rolling_execution_bundle(execution_bundle)
        except (TypeError, ValueError) as exc:
            raise StateError(f"rolling execution bundle is invalid: {exc}") from exc
        if len(canonical_data(self.bundle)) > MAX_BUNDLE_BYTES:
            raise StateError("rolling execution bundle exceeds its byte bound")
        self.spec = self.bundle["runSpec"]
        self.plan = self.bundle["pipelinePlan"]
        self.run_id = str(self.spec["runId"])
        self.control_root = self.repository.root / ".compass-builder"
        self.rolling_root = self.control_root / "rolling-runs"
        self.run_root = self.rolling_root / self.run_id
        self.state_path = self.run_root / "state.json"
        self.execution_bundle_path = self.run_root / "execution-bundle.json"
        self.controller_path = self.run_root / "controller.json"
        self.create_transaction_path = self.run_root / "create-transaction.json"
        self.transactions_path = self.run_root / "transactions"
        self.events_path = self.run_root / "events"
        self.dispatch_records_path = self.run_root / "dispatch-records"
        self._validate_controller_root_prerequisite()
        self._require_safe_target()

    def _validate_controller_root_prerequisite(self) -> None:
        run = f".compass-builder/rolling-runs/{self.run_id}"
        staging = f".compass-builder/rolling-runs/.{self.run_id}.create-probe"
        roots = (".compass-builder/", ".compass-builder/rolling-runs", run, staging)
        root_files = ("controller.json", "create-transaction.json",
                      "execution-bundle.json", "state.json")
        collections = ("transactions", "events", "dispatch-records")
        probes = (*roots, *(f"{run}/{name}" for name in root_files),
                  *(f"{staging}/{name}" for name in root_files),
                  *(f"{run}/{name}" for name in collections),
                  *(f"{run}/{name}/00000001.json" for name in collections),
                  f"{run}/state-candidate.tmp", f"{run}/state-displaced.tmp")
        ignored = git_text(self.repository.root,
                           ["check-ignore", "--no-index", "--", *probes], check=False)
        if ignored.splitlines() != list(probes):
            raise StateError(".compass-builder control root and every rolling artifact "
                             "class must be ignored before rolling state is used")
        tracked = git_text(self.repository.root, ["ls-files", "--", ".compass-builder"])
        if tracked.strip():
            raise StateError(".compass-builder must be absent from the repository index")

    def _require_safe_target(self) -> None:
        require_contained(self.rolling_root, self.control_root, label="rolling publication root")
        require_contained(self.run_root, self.rolling_root, label="rolling run directory")
        if self.run_root.exists() and is_reparse(self.run_root):
            raise StateError("rolling run directory may not be a symlink or reparse point")

    def _repository_identity(self) -> dict[str, object]:
        return {
            "repositoryRoot": str(self.repository.root),
            "commonGitDir": str(self.repository.common_git_dir),
            "gitDir": str(self.repository.git_dir),
        }

    def _controller(self) -> dict[str, object]:
        identity = self._repository_identity()
        return {
            "schemaVersion": "compass-builder.rolling-controller.v2",
            "runId": self.run_id,
            "planDigest": canonical_digest(self.plan),
            "executionBundleDigest": canonical_digest(self.bundle),
            "repositoryIdentity": identity,
            "repositoryIdentityDigest": canonical_digest(identity),
        }

    def _create_transaction(self, state: Mapping[str, object]) -> dict[str, object]:
        return {
            "schemaVersion": "compass-builder.rolling-create-transaction.v2",
            "runId": self.run_id,
            "executionBundleDigest": canonical_digest(self.bundle),
            "controllerDigest": canonical_digest(self._controller()),
            "initialStateDigest": canonical_digest(state),
        }

    def _state_story(self, planned: Mapping[str, object]) -> dict[str, object]:
        return {
            "storyId": planned["storyId"], "integrationOrdinal": planned["integrationOrdinal"],
            "lifecycle": "never-launched", "blockedFromLifecycle": None, "attempt": 0,
            "workerStartSha": None, "branch": planned["branch"],
            "registeredCloneDigest": None, "workerReceiptDigest": None,
            "verificationEvidenceDigest": None, "importEvidenceDigest": None,
            "mergeIntentDigest": None, "integrationSha": None, "postCheckEvidenceDigest": None,
            "gateEvidenceDigests": [],
        }

    def initial_state(self) -> dict[str, object]:
        initial = str(self.plan["integrationExpectedSha"])
        state = {
            "schemaVersion": "compass-builder.pipeline-state.v2", "runId": self.run_id,
            "planDigest": canonical_digest(self.plan), "baseSha": self.plan["baseSha"],
            "integrationBranch": self.plan["integrationBranch"],
            "initialIntegrationSha": initial, "currentIntegrationSha": initial,
            "lastVerifiedIntegrationSha": initial, "previousState": None, "state": "planned",
            "lastEventSequence": 0, "lastEventDigest": None, "activeOwners": [],
            "integrationQueue": [], "activeBlocker": None, "blockerHistory": [],
            "stories": [self._state_story(item) for item in self.plan["stories"]],
        }
        return self._validate_state(state)

    def _validate_state(self, value: Mapping[str, object]) -> dict[str, object]:
        try:
            _plan, state = validate_rolling_state_bindings(self.plan, value)
        except (TypeError, ValueError) as exc:
            raise StateError(f"rolling state is invalid: {exc}") from exc
        if len(canonical_data(state)) > MAX_STATE_BYTES:
            raise StateError("rolling state exceeds its byte bound")
        return state

    def _read_mapping(self, path: Path, *, label: str, max_bytes: int = MAX_RECORD_BYTES
                      ) -> tuple[dict[str, Any], bytes]:
        try:
            raw = read_no_follow(path, self.run_root, label=label, max_bytes=max_bytes)
        except (OSError, StateError) as exc:
            raise StateError(f"{label} is unavailable: {exc}") from exc
        try:
            decoded = json.loads(raw.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise ValueError("must be a JSON object")
            expected = canonical_data(decoded)
        except (UnicodeError, TypeError, ValueError) as exc:
            raise StateError(f"{label} is malformed: {exc}") from exc
        if raw != expected:
            raise StateError(f"{label} is not canonical JSON")
        return decoded, raw

    def _write_new_bytes_at(self, target: Path, payload: bytes, *, label: str,
                            parent_descriptor: int | None) -> None:
        flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        )
        descriptor: int | None = None
        try:
            descriptor = _secure_files._open_at(target, flags, 0o600, parent_descriptor)
            written = 0
            while written < len(payload):
                count = os.write(descriptor, payload[written:])
                if count <= 0:
                    raise OSError("short immutable publication write")
                written += count
            os.fsync(descriptor)
            opened = os.fstat(descriptor)
            named = _secure_files._stat_at(target, parent_descriptor)
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
                    or is_reparse(target)):
                raise StateError(f"{label} changed while it was published")
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _write_new_bytes(self, path: Path, payload: bytes, *, label: str) -> None:
        target = require_contained(path, self.run_root, label=label)
        reject_reparse_components(target.parent, label=label)
        try:
            with _secure_files._directory_guard(target, self.run_root,
                                                label=label) as parent_descriptor:
                self._write_new_bytes_at(target, payload, label=label,
                                         parent_descriptor=parent_descriptor)
            reject_reparse_components(target, label=label)
        except (OSError, StateError) as exc:
            raise StateError(f"{label} could not be published: {exc}") from exc

    def _write_exact(self, path: Path, value: Mapping[str, object], *, label: str) -> None:
        expected = canonical_data(value)
        if path.exists() or path.is_symlink():
            _decoded, existing = self._read_mapping(path, label=label)
            if existing != expected:
                raise StateError(f"{label} has different immutable contents")
            return
        try:
            self._write_new_bytes(path, expected, label=label)
        except StateError as exc:
            if path.exists() or path.is_symlink():
                _decoded, existing = self._read_mapping(path, label=label)
                if existing == expected:
                    return
            raise StateError(f"{label} publication failed closed: {exc}") from exc

    @staticmethod
    def _windows_flush_directory(path: Path) -> None:
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        flush = kernel.FlushFileBuffers
        flush.argtypes = (wintypes.HANDLE,)
        flush.restype = wintypes.BOOL
        close = kernel.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        handle = create_file(str(path), 0x40000000, 7, None, 3, 0x02000000, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not flush(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            close(handle)

    def _sync_directory(self, path: Path, root: Path, *, label: str) -> None:
        directory = require_contained(path, root, label=label, allow_root=True)
        try:
            with _secure_files._directory_guard(
                directory / ".durability-probe", root, label=label,
            ) as descriptor:
                if descriptor is None:
                    self._windows_flush_directory(directory)
                else:
                    opened = os.fstat(descriptor)
                    os.fsync(descriptor)
                    named = os.stat(directory, follow_symlinks=False)
                    _require(
                        self._real_directory(opened)
                        and self._identity(opened) == self._identity(named)
                        and not is_reparse(directory),
                        f"{label} directory changed during durability sync",
                    )
        except (OSError, StateError) as exc:
            raise StateError(f"{label} failed: {exc}") from exc

    def _sync_evidence_directory(self, path: Path) -> None:
        self._sync_directory(path, self.run_root,
                             label="rolling evidence directory sync")

    @staticmethod
    def _identity(value: os.stat_result) -> tuple[int, int]:
        return value.st_dev, value.st_ino

    @staticmethod
    def _real_directory(value: os.stat_result) -> bool:
        attributes = getattr(value, "st_file_attributes", 0)
        return stat.S_ISDIR(value.st_mode) and not attributes & 0x400

    def _check_directory(self, path: Path, value: os.stat_result,
                         expected: tuple[int, int] | None, *, label: str) -> None:
        if (not self._real_directory(value) or is_reparse(path)
                or expected is not None and self._identity(value) != expected):
            raise StateError(f"{label} changed or is unsafe")

    @staticmethod
    def _mkdir_at(path: Path, parent_descriptor: int | None) -> None:
        if parent_descriptor is None:
            path.mkdir()
        else:
            os.mkdir(path.name, dir_fd=parent_descriptor)

    @staticmethod
    def _linux_rename_at(source: Path, target: Path, parent_descriptor: int | None,
                         flag: int) -> None:
        if not sys.platform.startswith("linux") or parent_descriptor is None:
            raise StateError("atomic Linux rename primitive is unsupported on this host")
        try:
            renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
        except AttributeError as exc:
            raise StateError("atomic Linux renameat2 primitive is unavailable") from exc
        renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                              ctypes.c_char_p, ctypes.c_uint)
        renameat2.restype = ctypes.c_int
        if renameat2(parent_descriptor, os.fsencode(source.name), parent_descriptor,
                     os.fsencode(target.name), flag):
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise FileExistsError(error, "atomic no-replace destination exists", target)
            raise OSError(error, os.strerror(error), target)

    def _publish_run_directory(self, staging: Path, parent_descriptor: int | None) -> None:
        if os.name == "nt":
            os.rename(staging, self.run_root)
        else:
            self._linux_rename_at(staging, self.run_root, parent_descriptor, 1)

    def _ensure_directory(self, path: Path, root: Path, *, label: str) -> None:
        target = require_contained(path, root, label=label)
        with _secure_files._directory_guard(target, root, label=label) as parent:
            try:
                named = _secure_files._stat_at(target, parent)
            except FileNotFoundError:
                self._mkdir_at(target, parent)
                named = _secure_files._stat_at(target, parent)
            self._check_directory(target, named, None, label=label)
            self._sync_directory(target.parent, root, label=f"{label} entry")

    def _bounded_names(self, directory: Path, *, maximum: int, label: str,
                       directory_descriptor: int | None) -> tuple[str, ...]:
        try:
            iterator = (directory.iterdir() if directory_descriptor is None
                        else os.scandir(directory_descriptor))
            names: list[str] = []
            try:
                for entry in iterator:
                    if len(names) == maximum:
                        raise StateError(f"{label} exceeds its entry bound")
                    names.append(entry.name)
            finally:
                close = getattr(iterator, "close", None)
                if close is not None:
                    close()
        except OSError as exc:
            raise StateError(f"{label} is unavailable: {exc}") from exc
        return tuple(names)

    def create(self) -> dict[str, object]:
        state = self.initial_state()
        self._validate_controller_root_prerequisite()
        self._require_safe_target()
        if self.run_root.exists() or self.run_root.is_symlink():
            self._sync_directory(self.rolling_root, self.control_root,
                                 label="existing rolling run directory entry")
            return self.load()
        try:
            self._ensure_directory(self.control_root, self.repository.root,
                                   label="rolling controller root")
            self._ensure_directory(self.rolling_root, self.control_root,
                                   label="rolling publication root")
        except (OSError, StateError) as exc:
            raise StateError(f"rolling publication root is unsafe: {exc}") from exc
        prefix = f".{self.run_id}.create-"
        try:
            with _secure_files._directory_guard(self.run_root, self.rolling_root,
                    label="rolling create publication") as rolling_descriptor:
                names = self._bounded_names(self.rolling_root, maximum=MAX_PIPELINE_EVENTS,
                    label="rolling publication root", directory_descriptor=rolling_descriptor)
                if any(name.startswith(prefix) for name in names):
                    raise StateError("an interrupted rolling create publication exists")
                staging = self.rolling_root / f"{prefix}{secrets.token_hex(16)}"
                self._mkdir_at(staging, rolling_descriptor)
                staged_identity = self._identity(_secure_files._stat_at(
                    staging, rolling_descriptor))
                self._sync_directory(
                    self.rolling_root, self.control_root,
                    label="rolling create staging directory entry",
                )
                with _secure_files._directory_guard(staging / "state.json", self.rolling_root,
                        label="rolling create transaction") as staging_descriptor:
                    opened = (os.fstat(staging_descriptor) if staging_descriptor is not None
                              else _secure_files._stat_at(staging, rolling_descriptor))
                    self._check_directory(staging, opened, staged_identity,
                                          label="rolling create staging directory")
                    for name in ("transactions", "events", "dispatch-records"):
                        child = staging / name
                        self._mkdir_at(child, staging_descriptor)
                        self._check_directory(child, _secure_files._stat_at(
                            child, staging_descriptor), None, label="rolling create collection")
                    publications = (
                        ("create-transaction.json", self._create_transaction(state),
                         "rolling create transaction"),
                        ("execution-bundle.json", self.bundle, "rolling execution bundle"),
                        ("controller.json", self._controller(), "rolling controller identity"),
                        ("state.json", state, "rolling initial state"),
                    )
                    for name, value, label in publications:
                        self._write_new_bytes_at(staging / name, canonical_data(value),
                            label=label, parent_descriptor=staging_descriptor)
                    self._sync_directory(staging, self.rolling_root,
                                         label="rolling create staging contents")
                staged = _secure_files._stat_at(staging, rolling_descriptor)
                self._check_directory(staging, staged, staged_identity,
                                      label="rolling create staging directory")
                try:
                    _secure_files._stat_at(self.run_root, rolling_descriptor)
                except FileNotFoundError:
                    pass
                else:
                    raise StateError("rolling run target appeared during publication")
                self._publish_run_directory(staging, rolling_descriptor)
                self._sync_directory(self.rolling_root, self.control_root,
                                     label="rolling run directory publication")
        except (OSError, StateError) as exc:
            raise StateError(f"rolling create publication failed closed: {exc}") from exc
        return self.load()

    def _sequence_path(self, directory: Path, sequence: int, *, label: str) -> Path:
        if type(sequence) is not int or not 1 <= sequence <= MAX_PIPELINE_EVENTS:
            raise StateError(f"{label} sequence is outside its bound")
        return require_contained(
            directory / f"{sequence:08d}.json", self.run_root, label=label
        )

    def transaction_path(self, sequence: int) -> Path:
        return self._sequence_path(self.transactions_path, sequence,
                                   label="rolling transaction")

    def event_path(self, sequence: int) -> Path:
        return self._sequence_path(self.events_path, sequence, label="rolling event")

    def dispatch_record_path(self, sequence: int) -> Path:
        return self._sequence_path(self.dispatch_records_path, sequence,
                                   label="rolling dispatch record")

    def _read_collection(self, directory: Path, *, label: str,
                         maximum: int) -> _ArtifactMap:
        if not directory.is_dir() or is_reparse(directory):
            raise StateError(f"{label} collection is missing or unsafe")
        try:
            with _secure_files._directory_guard(
                directory / "entry", self.run_root, label=label
            ) as directory_descriptor:
                names = self._bounded_names(
                    directory, maximum=maximum, label=f"{label} collection",
                    directory_descriptor=directory_descriptor,
                )
        except (OSError, StateError) as exc:
            raise StateError(f"{label} collection is unavailable: {exc}") from exc
        result: dict[int, dict[str, Any]] = {}
        for name in names:
            if len(name) != 13 or not name.endswith(".json") or not name[:8].isdigit():
                raise StateError(f"{label} collection contains an unknown file")
            sequence = int(name[:8])
            if sequence < 1 or sequence > MAX_PIPELINE_EVENTS or sequence in result:
                raise StateError(f"{label} collection contains an ambiguous sequence")
            path = directory / name
            value, _raw = self._read_mapping(path, label=label)
            result[sequence] = value
        return result

    def _validate_transaction(self, value: Mapping[str, Any],
                              sequence: int) -> dict[str, Any]:
        _require(set(value) == _TRANSACTION_FIELDS,
                 "rolling transaction has an unknown or missing field")
        try:
            if value["schemaVersion"] != "compass-builder.rolling-transaction.v2":
                raise ValueError("unknown schema version")
            run_id(value["runId"], "transaction.runId")
            if value["runId"] != self.run_id:
                raise ValueError("run identity mismatch")
            if integer(value["sequence"], "transaction.sequence", minimum=1) != sequence:
                raise ValueError("sequence mismatch")
            enum(value["operation"], "transaction.operation", {"dispatch", "completion"})
            for field in ("previousStateDigest", "eventDigest", "nextStateDigest",
                          "evidenceDigest", "inputDigest"):
                digest(value[field], f"transaction.{field}")
            input_value = value["input"]
            if not isinstance(input_value, dict):
                raise ValueError("input must be an object")
            expected_input_fields = ({"dispatchRecordDigest", "ownerId"}
                if value["operation"] == "dispatch"
                else {"storyId", "attempt", "workerReceiptDigest"})
            if set(input_value) != expected_input_fields:
                raise ValueError("input has an unknown or missing field")
            if value["operation"] == "dispatch":
                digest(input_value["dispatchRecordDigest"], "transaction.input.dispatchRecordDigest")
                identifier(input_value["ownerId"], "transaction.input.ownerId")
            else:
                identifier(input_value["storyId"], "transaction.input.storyId")
                integer(input_value["attempt"], "transaction.input.attempt", minimum=1)
                digest(input_value["workerReceiptDigest"], "transaction.input.workerReceiptDigest")
            if canonical_digest(input_value) != value["inputDigest"]:
                raise ValueError("input digest mismatch")
        except (TypeError, ValueError) as exc:
            raise StateError(f"rolling transaction is invalid: {exc}") from exc
        return dict(value)

    def _validate_root_names(self) -> None:
        self._require_safe_target()
        _require(self.run_root.is_dir() and not is_reparse(self.run_root),
                 "rolling run artifact set is missing or unsafe")
        try:
            with _secure_files._directory_guard(
                self.state_path, self.run_root, label="rolling run artifact set"
            ) as run_descriptor:
                names = set(self._bounded_names(
                    self.run_root, maximum=len(_ROOT_NAMES) + 2,
                    label="rolling run artifact set with unknown files",
                    directory_descriptor=run_descriptor,
                ))
        except (OSError, StateError) as exc:
            raise StateError(f"rolling run artifact set is unavailable: {exc}") from exc
        if any(name.startswith("state-") and name.endswith(".tmp") for name in names):
            raise StateError("an interrupted atomic rolling state publication exists")
        missing = _ROOT_NAMES - names
        unknown = names - _ROOT_NAMES
        if missing:
            raise StateError("rolling run artifact set is missing: " + ", ".join(sorted(missing)))
        if unknown:
            raise StateError("rolling run artifact set contains unknown files: "
                             + ", ".join(sorted(unknown)))

    def _load_publication(self, *, allow_interrupted: bool) -> tuple[
        dict[str, object], bytes, _ArtifactMap, _ArtifactMap, _ArtifactMap,
    ]:
        self._validate_root_names()
        durable_bundle, bundle_raw = self._read_mapping(self.execution_bundle_path,
            label="rolling execution bundle", max_bytes=MAX_BUNDLE_BYTES)
        try:
            normalized_bundle = validate_rolling_execution_bundle(durable_bundle)
        except (TypeError, ValueError) as exc:
            raise StateError(f"rolling execution bundle is invalid: {exc}") from exc
        if normalized_bundle != self.bundle or bundle_raw != canonical_data(self.bundle):
            raise StateError("rolling execution bundle does not bind this immutable run")
        controller, _raw = self._read_mapping(
            self.controller_path, label="rolling controller identity")
        if controller != self._controller():
            raise StateError("rolling controller identity does not bind this repository and run")
        state_value, state_raw = self._read_mapping(self.state_path, label="rolling state",
                                                    max_bytes=MAX_STATE_BYTES)
        state = self._validate_state(state_value)
        create_intent, _raw = self._read_mapping(
            self.create_transaction_path, label="rolling create transaction")
        if create_intent != self._create_transaction(self.initial_state()):
            raise StateError("rolling create transaction does not bind immutable inputs")
        transactions = self._read_collection(
            self.transactions_path, label="rolling transaction", maximum=MAX_PIPELINE_EVENTS)
        events = self._read_collection(
            self.events_path, label="rolling event", maximum=MAX_PIPELINE_EVENTS)
        dispatches = self._read_collection(
            self.dispatch_records_path, label="rolling dispatch record",
            maximum=len(self.plan["stories"]))
        self._validate_history(state, transactions, events, dispatches,
                               allow_interrupted=allow_interrupted)
        return state, state_raw, transactions, events, dispatches

    def _validate_history(self, state: Mapping[str, object],
                          transactions: Mapping[int, Mapping[str, Any]],
                          events: Mapping[int, Mapping[str, Any]],
                          dispatches: Mapping[int, Mapping[str, Any]], *,
                          allow_interrupted: bool) -> None:
        committed = int(state["lastEventSequence"])
        transaction_sequences = sorted(transactions)
        _require(transaction_sequences == list(range(1, len(transaction_sequences) + 1)),
                 "rolling transaction history is not contiguous")
        _require(len(transaction_sequences) <= committed + 1,
                 "rolling transaction history is ambiguous")
        _require(len(transaction_sequences) >= committed,
                 "rolling committed transaction history is missing")
        event_sequences = sorted(events)
        _require(event_sequences == list(range(1, len(event_sequences) + 1)),
                 "rolling event history is not contiguous")
        _require(committed <= len(event_sequences) <= len(transaction_sequences),
                 "rolling committed event history is missing or ambiguous")
        _require(allow_interrupted or len(transaction_sequences) == committed,
                 "rolling publication is interrupted after its durable intent")
        if events:
            try:
                validate_pipeline_event_chain(
                    [events[index] for index in event_sequences])
            except (TypeError, ValueError) as exc:
                raise StateError(f"rolling event history is invalid: {exc}") from exc
        for event in events.values():
            _require(event.get("runId") == self.run_id,
                     "rolling event run identity does not bind this run")
        derived = self.initial_state()
        committed_state = derived
        expected_dispatches: set[int] = set()
        for sequence in transaction_sequences:
            transaction = self._validate_transaction(transactions[sequence], sequence)
            _require(transaction["previousStateDigest"] == canonical_digest(derived),
                     "rolling transaction previous state digest is not derived history")
            record = None
            if transaction["operation"] == "dispatch":
                expected_dispatches.add(sequence)
                record = dispatches.get(sequence)
                if record is None and sequence <= committed:
                    raise StateError("rolling committed dispatch evidence is missing")
                if record is not None:
                    record_digest = canonical_digest(record)
                    _require(record_digest == transaction["evidenceDigest"],
                             "rolling dispatch record digest is invalid")
                    _require(transaction["input"]["dispatchRecordDigest"] == record_digest,
                             "rolling transaction input does not bind its dispatch record")
            event = events.get(sequence)
            if event is None:
                if sequence <= committed:
                    raise StateError("rolling committed event evidence is missing")
                continue
            _require(transaction["eventDigest"] == canonical_digest(event),
                     "rolling transaction does not bind its event")
            _require(transaction["inputDigest"] == event["payloadDigest"],
                     "rolling transaction does not bind its event payload")
            _require(transaction["operation"] == event["eventType"],
                     "rolling transaction operation does not match its event")
            _require(transaction["evidenceDigest"] == event["evidenceDigest"],
                     "rolling transaction does not bind its evidence")
            derived, _record = self._derive_transition(
                derived, event, transaction["input"], dispatch_record=record
            )
            _require(transaction["nextStateDigest"] == canonical_digest(derived),
                     "rolling transaction next state digest is not derived history")
            if sequence <= committed:
                committed_state = derived
        if set(dispatches) - expected_dispatches:
            raise StateError("rolling dispatch evidence has no transaction authority")
        _require(state == committed_state,
                 "rolling durable state does not match reconstructed committed history")

    def load(self) -> dict[str, object]:
        state, _raw, _transactions, _events, _dispatches = self._load_publication(
            allow_interrupted=False)
        return state

    def _event(self, previous: Mapping[str, object], event_type: str, story_id: str,
               event_id: str, occurred_at: str, evidence_digest: str,
               payload_digest: str, state_before: str, state_after: str
               ) -> dict[str, object]:
        event = {
            "schemaVersion": "compass-builder.pipeline-event.v2", "runId": self.run_id,
            "eventId": event_id, "sequence": int(previous["lastEventSequence"]) + 1,
            "previousEventDigest": previous["lastEventDigest"], "eventType": event_type,
            "storyId": story_id, "occurredAt": occurred_at, "stateBefore": state_before,
            "stateAfter": state_after, "evidenceDigest": evidence_digest,
            "payloadDigest": payload_digest,
        }
        try:
            validate_pipeline_event_shape(event)
            canonical_data(event)
        except (TypeError, ValueError) as exc:
            raise StateError(f"rolling event is invalid: {exc}") from exc
        return event

    def _transaction(self, previous: Mapping[str, object],
                     current: Mapping[str, object], event: Mapping[str, object],
                     input_value: Mapping[str, object]) -> dict[str, object]:
        return {
            "schemaVersion": "compass-builder.rolling-transaction.v2", "runId": self.run_id,
            "sequence": event["sequence"], "operation": event["eventType"],
            "previousStateDigest": canonical_digest(previous), "eventDigest": canonical_digest(event),
            "nextStateDigest": canonical_digest(current), "evidenceDigest": event["evidenceDigest"],
            "input": copy.deepcopy(dict(input_value)),
            "inputDigest": canonical_digest(input_value),
        }

    def _read_bytes_at(self, target: Path, *, label: str, parent_descriptor: int | None
                       ) -> tuple[bytes, tuple[int, int]]:
        descriptor: int | None = None
        try:
            flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_BINARY", 0))
            descriptor = _secure_files._open_at(target, flags, 0o600, parent_descriptor)
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_STATE_BYTES:
                raise StateError(f"{label} is not a bounded regular file")
            chunks: list[bytes] = []
            remaining = MAX_STATE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            named = _secure_files._stat_at(target, parent_descriptor)
            _require(len(payload) <= MAX_STATE_BYTES
                     and self._identity(opened) == self._identity(named)
                     and not is_reparse(target), f"{label} changed or exceeded its bound")
            return payload, self._identity(opened)
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _unlink_at(path: Path, parent_descriptor: int | None) -> None:
        if parent_descriptor is None:
            path.unlink()
        else:
            os.unlink(path.name, dir_fd=parent_descriptor)

    @staticmethod
    def _windows_replace_file(target: Path, replacement: Path, backup: Path) -> None:
        from ctypes import wintypes

        replace_file = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
        replace_file.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                 wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID)
        replace_file.restype = wintypes.BOOL
        if not replace_file(str(target), str(replacement), str(backup), 1, None, None):
            raise ctypes.WinError(ctypes.get_last_error())

    def _exchange_state_candidate(self, candidate: Path, displaced: Path,
                                  parent_descriptor: int | None) -> Path:
        if os.name == "nt":
            self._windows_replace_file(self.state_path, candidate, displaced)
            return displaced
        self._linux_rename_at(candidate, self.state_path, parent_descriptor, 2)
        return candidate

    def _restore_displaced_state(self, displaced: Path, candidate: Path,
                                 parent_descriptor: int | None) -> Path:
        if os.name == "nt":
            self._windows_replace_file(self.state_path, displaced, candidate)
            return candidate
        self._linux_rename_at(displaced, self.state_path, parent_descriptor, 2)
        return displaced

    def _discard_owned(self, path: Path, identity: tuple[int, int],
                       parent_descriptor: int | None) -> None:
        try:
            named = _secure_files._stat_at(path, parent_descriptor)
        except FileNotFoundError:
            return
        if self._identity(named) == identity:
            self._unlink_at(path, parent_descriptor)

    def _atomic_replace(self, state: Mapping[str, object], expected_previous: bytes) -> None:
        self._require_safe_target()
        candidate: Path | None = None
        candidate_identity: tuple[int, int] | None = None
        displaced: Path | None = None
        displaced_identity: tuple[int, int] | None = None
        exchanged = False
        try:
            with _secure_files._directory_guard(self.state_path, self.run_root,
                                                label="rolling state CAS") as parent_descriptor:
                try:
                    before, before_identity = self._read_bytes_at(self.state_path,
                        label="rolling state predecessor", parent_descriptor=parent_descriptor)
                    _require(before == expected_previous,
                             "rolling state changed since the exact CAS predecessor")
                    token = secrets.token_hex(16)
                    candidate = self.run_root / f"state-{token}.tmp"
                    displaced = self.run_root / f"state-{token}.displaced.tmp"
                    self._write_new_bytes_at(candidate, canonical_data(state),
                        label="rolling state temporary file", parent_descriptor=parent_descriptor)
                    candidate_identity = self._identity(_secure_files._stat_at(
                        candidate, parent_descriptor))
                    if os.name == "nt":
                        self._write_new_bytes_at(displaced, b"", label="rolling state backup",
                                                 parent_descriptor=parent_descriptor)
                        displaced_identity = self._identity(_secure_files._stat_at(
                            displaced, parent_descriptor))
                    current, current_identity = self._read_bytes_at(self.state_path,
                        label="rolling state predecessor", parent_descriptor=parent_descriptor)
                    _require(current == expected_previous and current_identity == before_identity,
                             "rolling state changed before the exact CAS replacement")
                    displaced = self._exchange_state_candidate(
                        candidate, displaced, parent_descriptor)
                    exchanged = True
                    try:
                        displaced_bytes, _identity = self._read_bytes_at(
                            displaced, label="displaced rolling state predecessor",
                            parent_descriptor=parent_descriptor)
                        _require(displaced_bytes == expected_previous,
                                 "displaced CAS predecessor differs")
                    except (OSError, StateError) as validation_error:
                        try:
                            self._restore_displaced_state(
                                displaced, candidate, parent_descriptor)
                        except (OSError, StateError) as exc:
                            raise StateError(
                                "displaced CAS predecessor differed and atomic restoration failed"
                            ) from exc
                        raise StateError(
                            "displaced CAS predecessor differed; intervening state was restored"
                        ) from validation_error
                    self._sync_directory(self.run_root, self.run_root,
                                         label="rolling state exchange")
                    self._unlink_at(displaced, parent_descriptor)
                    exchanged = False
                    self._sync_directory(self.run_root, self.run_root,
                                         label="rolling state backup deletion")
                finally:
                    if not exchanged:
                        for path, identity in ((candidate, candidate_identity),
                                               (displaced, displaced_identity)):
                            if path is not None and identity is not None:
                                try:
                                    self._discard_owned(path, identity, parent_descriptor)
                                except OSError:
                                    pass
        except (OSError, StateError) as exc:
            raise StateError(f"atomic rolling state persistence failed: {exc}") from exc

    def _publish(self, previous: Mapping[str, object], current: Mapping[str, object],
                 event: Mapping[str, object], *, input_value: Mapping[str, object],
                 dispatch_record: Mapping[str, object] | None = None) -> dict[str, object]:
        self._validate_controller_root_prerequisite()
        before = self._validate_state(previous)
        after = self._validate_state(current)
        transaction = self._transaction(before, after, event, input_value)
        sequence = int(event["sequence"])
        durable, raw, transactions, events, dispatches = self._load_publication(
            allow_interrupted=True)
        if raw == canonical_data(after):
            if (transactions.get(sequence) != transaction or events.get(sequence) != event
                    or dispatch_record is not None
                    and dispatches.get(sequence) != dispatch_record):
                raise StateError("completed publication has mismatched immutable evidence")
            return after
        if raw != canonical_data(before) or durable != before:
            raise StateError("durable state changed since the exact predecessor was read")
        candidate_events = [events[index] for index in sorted(events)]
        if event["sequence"] not in events:
            candidate_events.append(dict(event))
        elif events[event["sequence"]] != event:
            raise StateError("rolling event has different immutable contents")
        try:
            validate_pipeline_event_chain(candidate_events)
        except (TypeError, ValueError) as exc:
            raise StateError(f"rolling candidate event chain is invalid: {exc}") from exc
        self._write_exact(self.transaction_path(sequence), transaction, label="rolling transaction")
        self._sync_evidence_directory(self.transactions_path)
        if dispatch_record is not None:
            self._write_exact(self.dispatch_record_path(sequence), dispatch_record,
                              label="rolling dispatch record")
            self._sync_evidence_directory(self.dispatch_records_path)
        self._write_exact(self.event_path(sequence), event, label="rolling event")
        self._sync_evidence_directory(self.events_path)
        self._atomic_replace(after, raw)
        return self.load()

    def _story(self, state: Mapping[str, object], story_id: str) -> dict[str, Any]:
        matches = [item for item in state["stories"] if item["storyId"] == story_id]
        if len(matches) != 1:
            raise StateError("rolling transition does not identify one planned story")
        return matches[0]

    def _active_owners(self, state: Mapping[str, object]) -> list[dict[str, object]]:
        existing = {item["storyId"]: item for item in state["activeOwners"]}
        return [
            existing[item["storyId"]]
            for item in state["stories"]
            if item["storyId"] in existing
        ]

    def _derive_transition(self, previous: Mapping[str, object],
                           event: Mapping[str, object], input_value: Mapping[str, object], *,
                           dispatch_record: Mapping[str, object] | None = None
                           ) -> tuple[dict[str, object], dict[str, object] | None]:
        before = self._validate_state(previous)
        _require(event.get("runId") == self.run_id,
                 "rolling event run identity does not bind this run")
        _require(event.get("sequence") == int(before["lastEventSequence"]) + 1
                 and event.get("previousEventDigest") == before["lastEventDigest"],
                 "rolling event does not extend the derived event chain")
        _require(event.get("payloadDigest") == canonical_digest(input_value),
                 "rolling event payload does not bind its transition input")
        after = copy.deepcopy(before)
        story_id = str(event.get("storyId"))
        story = self._story(after, story_id)
        operation = event.get("eventType")
        normalized_record = None
        if operation == "dispatch":
            _require(dispatch_record is not None,
                     "rolling dispatch event is missing its exact record")
            try:
                record = copy.deepcopy(dict(dispatch_record))
                clone_digest = canonical_digest(record["registeredClone"])
            except (KeyError, TypeError, ValueError) as exc:
                raise StateError(f"rolling dispatch record is invalid: {exc}") from exc
            record_digest = canonical_digest(record)
            _require(
                input_value.get("dispatchRecordDigest") == record_digest
                and event.get("evidenceDigest") == record_digest
                and event.get("storyId") == record.get("storyId"),
                "rolling dispatch input/event does not bind its exact record",
            )
            _require(story["lifecycle"] == event.get("stateBefore") == "never-launched"
                     and event.get("stateAfter") == "running",
                     "rolling dispatch does not match the derived story lifecycle")
            _require(record.get("attempt") == int(story["attempt"]) + 1,
                     "rolling dispatch attempt does not follow the derived story attempt")
            story.update(
                lifecycle="running", attempt=record["attempt"],
                workerStartSha=before["lastVerifiedIntegrationSha"],
                registeredCloneDigest=clone_digest,
            )
            owner = {
                "storyId": story_id, "ownerId": input_value.get("ownerId"),
                "writeScopes": next(item["writeScopes"] for item in self.plan["stories"]
                                    if item["storyId"] == story_id),
                "workerStartSha": story["workerStartSha"],
                "registeredCloneDigest": clone_digest,
            }
            after["activeOwners"] = self._active_owners(
                {**after, "activeOwners": [*after["activeOwners"], owner]}
            )
            _require(len(after["activeOwners"]) <= int(self.plan["concurrency"]),
                     "rolling dispatch exceeds the pipeline plan concurrency width")
            after["previousState"] = before["state"]
            after["state"] = "running"
            try:
                _spec, _plan, _state, normalized_record = validate_dispatch_record_bindings(
                    self.spec, self.plan, after, record
                )
            except (TypeError, ValueError) as exc:
                raise StateError(f"rolling dispatch record is invalid: {exc}") from exc
            _require(canonical_digest(normalized_record) == record_digest,
                     "rolling dispatch normalization changed its exact digest")
        elif operation == "completion":
            _require(dispatch_record is None,
                     "rolling completion may not carry dispatch evidence")
            _require(
                input_value.get("storyId") == story_id
                and input_value.get("workerReceiptDigest") == event.get("evidenceDigest"),
                "rolling completion input does not bind its event",
            )
            _require(story["lifecycle"] == event.get("stateBefore") == "running"
                     and event.get("stateAfter") == "worker-complete-unverified",
                     "rolling completion does not match the derived story lifecycle")
            _require(input_value.get("attempt") == story["attempt"],
                     "rolling completion attempt does not bind its derived dispatch")
            story["lifecycle"] = "worker-complete-unverified"
            story["workerReceiptDigest"] = input_value["workerReceiptDigest"]
            after["activeOwners"] = [
                item for item in after["activeOwners"] if item["storyId"] != story_id
            ]
            after["previousState"] = before["state"]
            after["state"] = "running"
        else:
            raise StateError("rolling transaction has an unsupported transition operation")
        after["lastEventSequence"] = event["sequence"]
        after["lastEventDigest"] = canonical_digest(event)
        return self._validate_state(after), normalized_record

    def record_dispatch(self, previous: Mapping[str, object],
                        dispatch_record: Mapping[str, object], *, owner_id: str,
                        event_id: str, occurred_at: str) -> dict[str, object]:
        before = self._validate_state(previous)
        try:
            record = copy.deepcopy(dict(dispatch_record))
            story_id = str(record["storyId"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StateError(f"rolling dispatch record is invalid: {exc}") from exc
        if len(canonical_data(record)) > MAX_RECORD_BYTES:
            raise StateError("rolling dispatch record exceeds its byte bound")
        record_digest = canonical_digest(record)
        input_value = {"dispatchRecordDigest": record_digest, "ownerId": owner_id}
        event = self._event(before, "dispatch", story_id, event_id, occurred_at,
                            record_digest, canonical_digest(input_value),
                            "never-launched", "running")
        after, normalized_record = self._derive_transition(
            before, event, input_value, dispatch_record=record
        )
        _require(normalized_record is not None, "rolling dispatch record was not normalized")
        return self._publish(before, after, event, input_value=input_value,
                             dispatch_record=normalized_record)

    def record_completion(self, previous: Mapping[str, object], *, story_id: str,
                          worker_receipt_digest: str, event_id: str,
                          occurred_at: str) -> dict[str, object]:
        before = self._validate_state(previous)
        story = self._story(before, story_id)
        _require(story["lifecycle"] == "running",
                 "rolling completion requires a running story")
        input_value = {
            "storyId": story_id,
            "attempt": story["attempt"],
            "workerReceiptDigest": worker_receipt_digest,
        }
        payload_digest = canonical_digest(input_value)
        event = self._event(before, "completion", story_id, event_id, occurred_at,
            worker_receipt_digest, payload_digest, "running", "worker-complete-unverified")
        after, _record = self._derive_transition(before, event, input_value)
        return self._publish(before, after, event, input_value=input_value)


__all__ = ["RollingStateStore"]
