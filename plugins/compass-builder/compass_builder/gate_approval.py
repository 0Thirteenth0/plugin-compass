"""Validation and trusted capability boundary for D2 gate approvals.

The capability proves only that an explicit decision provider was consulted in this
process. It is not cryptographic proof of a human decision; D3 owns binding the actual
user/operator decision to controller state.
"""

from __future__ import annotations

import copy
import hashlib
import os
import re
import stat
from abc import ABC, abstractmethod
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

from ._validation import ContractValidationError, canonical_digest, scope, timestamp


APPROVAL_SCHEMA_VERSION = "compass-builder.gate-command-approval.v1"
DIRECT_SHELL_IDENTITY = "direct-no-shell-v1"
POSIX_ISOLATION_MODE = "posix-staged-copy-v1"
WINDOWS_ISOLATION_MODE = "windows-locked-original-v1"
MAX_REFERENCES = 256
MAX_TIMEOUT_MS = 300_000
MAX_OUTPUT_BYTES = 1_048_576
MAX_ARTIFACT_BYTES = 67_108_864
MAX_REFERENCE_BYTES = 67_108_864
MAX_EXECUTABLE_BYTES = 536_870_912
_DIGEST_PREFIX = "sha256:"


class GateRunnerError(ValueError):
    """A gate batch or approval record cannot be interpreted safely."""


class ApprovalBoundaryError(GateRunnerError):
    """An approval did not cross the explicit provider boundary."""


class ApprovalDecisionProvider(ABC):
    """Boundary implemented by a UI or other trusted operator-decision provider."""

    @abstractmethod
    def approve(self, candidate: Mapping[str, Any]) -> bool:
        """Return literal True only after an explicit decision for this candidate."""


def _error(path: str, reason: str) -> GateRunnerError:
    return GateRunnerError(f"{path}: {reason}")


def _closed(value: Any, path: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(path, "must be a JSON object")
    missing = sorted(fields - value.keys())
    extra = sorted(value.keys() - fields)
    if missing:
        raise _error(path, f"missing required field(s): {', '.join(missing)}")
    if extra:
        raise _error(path, f"unknown field(s): {', '.join(extra)}")
    return value


def _text(value: Any, path: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise _error(path, "must be a trimmed non-empty string")
    if len(value) > maximum or "\x00" in value:
        raise _error(path, f"exceeds its safe {maximum}-character representation")
    return value


def _digest(value: Any, path: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith(_DIGEST_PREFIX)
        or len(value) != len(_DIGEST_PREFIX) + 64
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise _error(path, "must be a lowercase sha256: digest")
    return value


def _integer(value: Any, path: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise _error(path, f"must be an integer from 1 through {maximum}")
    return value


def _repository_path(value: Any, path: str, *, allow_dot: bool = False) -> str:
    if allow_dot and value == ".":
        return value
    try:
        return scope(value, path)
    except ContractValidationError as exc:
        raise _error(path, str(exc)) from exc


def _executable_path(
    value: Any, path: str, *, live: bool, recorded_platform: str | None = None,
) -> PurePath:
    spelling = _text(value, path, 2048)
    if live:
        candidate: PurePath = Path(spelling)
        components = re.split(r"[\\/]", spelling)
    elif recorded_platform is not None and recorded_platform.startswith("windows-"):
        candidate = PureWindowsPath(spelling)
        components = re.split(r"[\\/]", spelling)
    else:
        candidate = PurePosixPath(spelling)
        components = spelling.split("/")
    if not candidate.is_absolute() or not candidate.name:
        raise _error(
            path,
            "must be an exact absolute executable path, not a root; "
            "PATH lookup and relative forms are forbidden",
        )
    if any(part in {".", ".."} for part in components):
        raise _error(path, "must not contain lexical dot segments")
    if not live:
        return candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise _error(path, f"canonical path is unavailable: {exc}") from exc
    if os.path.normcase(os.path.normpath(str(candidate))) != os.path.normcase(str(resolved)):
        raise _error(path, "must already be the canonical non-link path")
    return resolved


def canonical_executable_path(value: Any, path: str) -> Path:
    return _executable_path(value, path, live=True)


def artifact_marker_digest(marker: str) -> str | None:
    prefix = "artifact-sha256:"
    if not marker.startswith(prefix):
        return None
    candidate = marker[len(prefix):]
    if len(candidate) != 64 or any(character not in "0123456789abcdef" for character in candidate):
        return None
    return _DIGEST_PREFIX + candidate


def digest_file(path: Path, *, max_bytes: int) -> str:
    """Digest a bounded regular file while rejecting link/reparse substitution."""

    if max_bytes <= 0:
        raise GateRunnerError("file digest bound must be positive")
    target = Path(path).absolute()
    try:
        named_before = target.lstat()
    except OSError as exc:
        raise GateRunnerError(f"file identity is unavailable: {target}: {exc}") from exc
    if (
        target.is_symlink()
        or not stat.S_ISREG(named_before.st_mode)
        or getattr(named_before, "st_file_attributes", 0) & 0x400
        or named_before.st_size > max_bytes
    ):
        raise GateRunnerError(f"file is not a bounded non-reparse regular file: {target}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags)
    except OSError as exc:
        raise GateRunnerError(f"file could not be opened safely: {target}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > max_bytes:
            raise GateRunnerError(f"file exceeded its identity bound: {target}")
        remaining = max_bytes + 1
        hasher = hashlib.sha256()
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            hasher.update(chunk)
            remaining -= len(chunk)
        if remaining == 0:
            raise GateRunnerError(f"file exceeded its identity bound: {target}")
        named_after = target.stat(follow_symlinks=False)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
            named_after.st_dev,
            named_after.st_ino,
            named_after.st_size,
        ):
            raise GateRunnerError(f"file identity changed while hashing: {target}")
        return _DIGEST_PREFIX + hasher.hexdigest()
    except OSError as exc:
        raise GateRunnerError(f"file could not be hashed safely: {target}: {exc}") from exc
    finally:
        os.close(descriptor)


def _validate_gate_approval(
    value: Mapping[str, Any], *, live: bool,
) -> dict[str, Any]:

    try:
        approval = copy.deepcopy(dict(value))
    except (TypeError, ValueError) as exc:
        raise GateRunnerError("approval must be a JSON object mapping") from exc
    _closed(
        approval,
        "$",
        {
            "schemaVersion", "approvalKind", "approvalId", "approvedBy", "approvedAt",
            "gateId", "gateDefinitionDigest", "executionIdentityDigest", "execution",
        },
    )
    if approval["schemaVersion"] != APPROVAL_SCHEMA_VERSION:
        raise _error("$.schemaVersion", f"must be {APPROVAL_SCHEMA_VERSION!r}")
    if approval["approvalKind"] != "explicit-operator":
        raise _error("$.approvalKind", "is audit metadata only and must identify explicit-operator")
    _text(approval["approvalId"], "$.approvalId", 96)
    _text(approval["approvedBy"], "$.approvedBy", 256)
    try:
        timestamp(approval["approvedAt"], "$.approvedAt")
    except ContractValidationError as exc:
        raise _error("$.approvedAt", str(exc)) from exc
    _text(approval["gateId"], "$.gateId", 64)
    _digest(approval["gateDefinitionDigest"], "$.gateDefinitionDigest")
    _digest(approval["executionIdentityDigest"], "$.executionIdentityDigest")

    execution = _closed(
        approval["execution"],
        "$.execution",
        {
            "launchMode", "isolation", "command", "successMarker", "workingDirectory", "shell",
            "platform", "environmentDigest", "limits", "executable", "referencedFiles",
            "referenceBindings", "referencesComplete", "artifactPath",
        },
    )
    if execution["launchMode"] != "direct":
        raise _error("$.execution.launchMode", "only the direct no-shell D2 launcher is supported")
    isolation = _closed(
        execution["isolation"],
        "$.execution.isolation",
        {"mode", "sourceExecutableMode", "stagedExecutableMode"},
    )
    _text(execution["command"], "$.execution.command", 4096)
    marker = _text(execution["successMarker"], "$.execution.successMarker", 1024)
    if not (marker.startswith("stdout-exact:") or artifact_marker_digest(marker) is not None):
        raise _error("$.execution.successMarker", "must be stdout-exact or artifact-sha256")
    working_directory = _repository_path(
        execution["workingDirectory"], "$.execution.workingDirectory", allow_dot=True
    )
    shell_identity = _text(execution["shell"], "$.execution.shell", 160)
    if shell_identity != DIRECT_SHELL_IDENTITY:
        raise _error("$.execution.shell", f"direct mode requires canonical identity {DIRECT_SHELL_IDENTITY!r}")
    selected_platform = _text(execution["platform"], "$.execution.platform", 160)
    _digest(execution["environmentDigest"], "$.execution.environmentDigest")

    limits = _closed(
        execution["limits"],
        "$.execution.limits",
        {"timeoutMs", "maxOutputBytes", "maxArtifactBytes", "maxReferenceBytes", "maxExecutableBytes"},
    )
    _integer(limits["timeoutMs"], "$.execution.limits.timeoutMs", MAX_TIMEOUT_MS)
    _integer(limits["maxOutputBytes"], "$.execution.limits.maxOutputBytes", MAX_OUTPUT_BYTES)
    _integer(limits["maxArtifactBytes"], "$.execution.limits.maxArtifactBytes", MAX_ARTIFACT_BYTES)
    _integer(limits["maxReferenceBytes"], "$.execution.limits.maxReferenceBytes", MAX_REFERENCE_BYTES)
    _integer(limits["maxExecutableBytes"], "$.execution.limits.maxExecutableBytes", MAX_EXECUTABLE_BYTES)

    executable = _closed(execution["executable"], "$.execution.executable", {"path", "digest"})
    executable_path = _executable_path(
        executable["path"], "$.execution.executable.path", live=live,
        recorded_platform=selected_platform,
    )
    if selected_platform.startswith("windows-") and executable_path.suffix.casefold() in {".cmd", ".bat"}:
        raise _error("$.execution.executable.path", "direct mode forbids shell-mediated .cmd and .bat executables")
    _digest(executable["digest"], "$.execution.executable.digest")
    if selected_platform.startswith("windows-"):
        if isolation["mode"] != WINDOWS_ISOLATION_MODE:
            raise _error(
                "$.execution.isolation.mode",
                f"Windows execution requires {WINDOWS_ISOLATION_MODE!r}",
            )
        if isolation["sourceExecutableMode"] is not None or isolation["stagedExecutableMode"] is not None:
            raise _error("$.execution.isolation", "Windows locked-original mode has no POSIX mode fields")
    else:
        if isolation["mode"] != POSIX_ISOLATION_MODE:
            raise _error(
                "$.execution.isolation.mode",
                f"POSIX execution requires {POSIX_ISOLATION_MODE!r}",
            )
        source_mode = isolation["sourceExecutableMode"]
        staged_mode = isolation["stagedExecutableMode"]
        if (
            isinstance(source_mode, bool)
            or not isinstance(source_mode, int)
            or not 0 <= source_mode <= 0o777
        ):
            raise _error("$.execution.isolation.sourceExecutableMode", "must be a POSIX permission mode")
        if source_mode & 0o111 == 0:
            raise _error("$.execution.isolation.sourceExecutableMode", "must retain an approved execute bit")
        if staged_mode != source_mode & ~0o222:
            raise _error(
                "$.execution.isolation.stagedExecutableMode",
                "must preserve the approved source mode while removing write bits",
            )
        if live:
            actual_source_mode = stat.S_IMODE(
                executable_path.stat(follow_symlinks=False).st_mode
            )
            if actual_source_mode != source_mode:
                raise _error(
                    "$.execution.isolation.sourceExecutableMode",
                    "does not match the current source executable mode",
                )

    references = execution["referencedFiles"]
    if not isinstance(references, list) or len(references) > MAX_REFERENCES:
        raise _error("$.execution.referencedFiles", f"must be an array of at most {MAX_REFERENCES} files")
    reference_paths: list[str] = []
    seen_platform_paths: set[str] = set()
    for index, item in enumerate(references):
        reference = _closed(item, f"$.execution.referencedFiles[{index}]", {"path", "digest"})
        relative = _repository_path(reference["path"], f"$.execution.referencedFiles[{index}].path")
        identity = relative.casefold() if selected_platform.startswith("windows-") else relative
        if identity in seen_platform_paths:
            raise _error("$.execution.referencedFiles", "contains duplicate platform-equivalent paths")
        seen_platform_paths.add(identity)
        reference_paths.append(relative)
        _digest(reference["digest"], f"$.execution.referencedFiles[{index}].digest")
    bindings = execution["referenceBindings"]
    if not isinstance(bindings, list) or len(bindings) > MAX_REFERENCES:
        raise _error("$.execution.referenceBindings", "must be a bounded array")
    bound_paths: list[str] = []
    bound_indexes: set[int] = set()
    for index, item in enumerate(bindings):
        binding = _closed(
            item, f"$.execution.referenceBindings[{index}]", {"path", "argvIndex"}
        )
        path_value = _repository_path(
            binding["path"], f"$.execution.referenceBindings[{index}].path"
        )
        argv_index = _integer(
            binding["argvIndex"], f"$.execution.referenceBindings[{index}].argvIndex", 4096
        )
        if argv_index in bound_indexes:
            raise _error("$.execution.referenceBindings", "contains duplicate argv indexes")
        bound_indexes.add(argv_index)
        bound_paths.append(path_value)
    if sorted(bound_paths) != sorted(reference_paths):
        raise _error(
            "$.execution.referenceBindings",
            "must match and bind every normalized referenced-file path byte-for-byte exactly once",
        )
    if execution["referencesComplete"] is not True:
        raise _error("$.execution.referencesComplete", "must attest every reviewed script, fixture, and dependency is listed")
    artifact_path = execution["artifactPath"]
    if artifact_path is not None:
        artifact_relative = _repository_path(artifact_path, "$.execution.artifactPath")
        artifact_repository_path = (
            artifact_relative
            if working_directory == "."
            else f"{working_directory}/{artifact_relative}"
        )
        artifact_identity = (
            artifact_repository_path.casefold()
            if selected_platform.startswith("windows-")
            else artifact_repository_path
        )
        if artifact_identity in seen_platform_paths:
            raise _error("$.execution.artifactPath", "must not also be a read-only referenced input")
    if marker.startswith("stdout-exact:") and artifact_path is not None:
        raise _error("$.execution.artifactPath", "must be null for stdout-exact")
    if artifact_marker_digest(marker) is not None and artifact_path is None:
        raise _error("$.execution.artifactPath", "is required for artifact-sha256")
    if approval["executionIdentityDigest"] != canonical_digest(execution):
        raise _error("$.executionIdentityDigest", "does not bind the exact immutable execution identity digest")
    return approval


def validate_detached_gate_approval_audit(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Purely validate closed D2 audit structure without live filesystem checks."""

    return _validate_gate_approval(value, live=False)


def validate_gate_approval(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one live approval candidate without granting execution authority."""

    return _validate_gate_approval(value, live=True)


_CAPABILITY_SEAL = object()


class TrustedGateApproval:
    """Opaque approval capability; ordinary mappings cannot construct a valid instance."""

    __slots__ = ("__record", "__seal", "__consumed")

    def __init__(self, record: Mapping[str, Any], seal: object):
        if seal is not _CAPABILITY_SEAL:
            raise ApprovalBoundaryError("trusted gate approvals are provider-issued only")
        self.__record = copy.deepcopy(dict(record))
        self.__seal = seal
        self.__consumed = False


def issue_trusted_gate_approval(
    record: Mapping[str, Any], provider: ApprovalDecisionProvider
) -> TrustedGateApproval:
    """Validate a candidate and consult an explicit decision provider before issuance."""

    if not isinstance(provider, ApprovalDecisionProvider):
        raise ApprovalBoundaryError("a typed approval decision provider is required")
    validated = validate_gate_approval(record)
    candidate = copy.deepcopy(validated)
    if provider.approve(candidate) is not True:
        raise ApprovalBoundaryError("the approval provider did not grant this exact candidate")
    if candidate != validated:
        raise ApprovalBoundaryError("the approval candidate changed inside the provider boundary")
    return TrustedGateApproval(validated, _CAPABILITY_SEAL)


def consume_trusted_gate_approval(capability: TrustedGateApproval) -> dict[str, Any]:
    """Consume one provider-issued capability exactly once."""

    if not isinstance(capability, TrustedGateApproval):
        raise ApprovalBoundaryError("raw approval mappings are untrusted and cannot execute gates")
    if capability._TrustedGateApproval__seal is not _CAPABILITY_SEAL:
        raise ApprovalBoundaryError("gate approval capability seal is invalid")
    if capability._TrustedGateApproval__consumed:
        raise ApprovalBoundaryError("gate approval capability is single-use and was already consumed")
    capability._TrustedGateApproval__consumed = True
    return copy.deepcopy(capability._TrustedGateApproval__record)


def inspect_trusted_gate_approval(capability: TrustedGateApproval) -> dict[str, Any]:
    """Read a detached audit candidate without consuming its execution authority."""

    if not isinstance(capability, TrustedGateApproval):
        raise ApprovalBoundaryError("raw approval mappings are untrusted")
    if capability._TrustedGateApproval__seal is not _CAPABILITY_SEAL:
        raise ApprovalBoundaryError("gate approval capability seal is invalid")
    if capability._TrustedGateApproval__consumed:
        raise ApprovalBoundaryError("gate approval capability was already consumed")
    return copy.deepcopy(capability._TrustedGateApproval__record)


__all__ = [
    "APPROVAL_SCHEMA_VERSION", "DIRECT_SHELL_IDENTITY", "POSIX_ISOLATION_MODE",
    "WINDOWS_ISOLATION_MODE", "ApprovalBoundaryError",
    "ApprovalDecisionProvider", "GateRunnerError", "TrustedGateApproval", "digest_file",
    "inspect_trusted_gate_approval", "issue_trusted_gate_approval", "validate_gate_approval",
    "validate_detached_gate_approval_audit",
]
