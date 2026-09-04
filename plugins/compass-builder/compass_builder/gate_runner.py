"""Exact-approval, sequential execution for outcome-gate commands.

This module deliberately does not participate in controller completion.  It accepts a
validated D1 ledger plus explicit operator approval records, executes only exact direct
commands, and returns evidence records for later D3 binding.
"""

from __future__ import annotations

import copy
import hashlib
import os
import platform as host_platform
import re
import shlex
import stat
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .gate_approval import (
    APPROVAL_SCHEMA_VERSION,
    DIRECT_SHELL_IDENTITY,
    ApprovalBoundaryError,
    GateRunnerError,
    POSIX_ISOLATION_MODE,
    TrustedGateApproval,
    WINDOWS_ISOLATION_MODE,
    artifact_marker_digest,
    canonical_executable_path,
    consume_trusted_gate_approval,
    digest_file,
    validate_gate_approval,
)
from .gate_snapshot import GateSnapshotError, staged_execution_surface
from ._validation import ContractValidationError, canonical_digest
from .models import validate_outcome_gate_ledger
from .process_runner import (
    BoundedProcessError,
    completed_text,
    parse_command,
    run_bounded_text,
)
from .secure_files import (
    SecureFileError,
    read_no_follow,
    reject_reparse_components,
    require_contained,
)


RESULT_SCHEMA_VERSION = "compass-builder.gate-command-result.v1"
MAX_APPROVALS = 512
_DIGEST_PREFIX = "sha256:"


@dataclass(frozen=True)
class GateExecutionResult:
    """One ordered D2 result; it is evidence, not a completion decision."""

    gate_id: str
    state: str
    executed: bool
    execution_identity_digest: str | None
    evidence_digest: str
    verified_at: str
    return_code: int | None
    stdout: str
    stderr: str
    stdout_digest: str
    stderr_digest: str
    artifact_before_digest: str | None
    artifact_digest: str | None
    elapsed_ms: int
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": RESULT_SCHEMA_VERSION,
            "gateId": self.gate_id,
            "state": self.state,
            "executed": self.executed,
            "executionIdentityDigest": self.execution_identity_digest,
            "evidenceDigest": self.evidence_digest,
            "verifiedAt": self.verified_at,
            "returnCode": self.return_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdoutDigest": self.stdout_digest,
            "stderrDigest": self.stderr_digest,
            "artifactBeforeDigest": self.artifact_before_digest,
            "artifactDigest": self.artifact_digest,
            "elapsedMs": self.elapsed_ms,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class _PreparedGate:
    gate: dict[str, Any]
    approval: dict[str, Any]
    argv: tuple[str, ...]
    repository_root: Path
    cwd: Path


def current_platform_identity() -> str:
    """Return the exact host family/architecture identity used by D2."""

    system = host_platform.system().lower() or os.name.lower()
    machine = host_platform.machine().lower()
    architecture = {
        "amd64": "amd64",
        "x86_64": "amd64",
        "x64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "i386": "x86",
        "i686": "x86",
        "x86": "x86",
    }.get(machine, machine or "unknown")
    return f"{system}-{architecture}"


def environment_digest(
    environment: Mapping[str, str], platform_identity: str | None = None
) -> str:
    """Digest the exact environment mapping without serializing it into evidence."""

    if not isinstance(environment, Mapping):
        raise GateRunnerError("environment must be a string-to-string mapping")
    selected_platform = platform_identity or current_platform_identity()
    windows = selected_platform.startswith("windows-")
    normalized: dict[str, str] = {}
    for raw_name, raw_value in environment.items():
        if not isinstance(raw_name, str) or not raw_name or "\x00" in raw_name or "=" in raw_name:
            raise GateRunnerError("environment contains an invalid variable name")
        if not isinstance(raw_value, str) or "\x00" in raw_value:
            raise GateRunnerError(f"environment variable {raw_name!r} has an invalid value")
        name = raw_name.casefold() if windows else raw_name
        if name in normalized:
            raise GateRunnerError("environment contains duplicate platform-equivalent names")
        normalized[name] = raw_value
    return canonical_digest(
        {
            "platform": selected_platform,
            "variables": [
                {"name": name, "value": normalized[name]} for name in sorted(normalized)
            ],
        }
    )


def render_direct_command(
    argv: Sequence[str], platform_identity: str | None = None
) -> str:
    """Render exact direct argv using the selected host's command-line rules."""

    if not argv or any(not isinstance(item, str) or not item or "\x00" in item for item in argv):
        raise GateRunnerError("direct command argv must contain non-empty safe strings")
    selected = platform_identity or current_platform_identity()
    if selected.startswith("windows-"):
        return subprocess.list2cmdline(list(argv))
    return shlex.join(argv)


def _canonical_repository_root(value: Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        raise GateRunnerError("repository root must be an explicit absolute path")
    try:
        canonical = candidate.resolve(strict=True)
    except OSError as exc:
        raise GateRunnerError(f"repository root canonical identity is unavailable: {exc}") from exc
    if not canonical.is_dir():
        raise GateRunnerError("repository root is not an existing directory")
    lexical_identity = os.path.normcase(os.path.normpath(str(candidate)))
    if lexical_identity != os.path.normcase(str(canonical)):
        raise GateRunnerError("repository root must not use a link or noncanonical alias")
    try:
        reject_reparse_components(canonical, label="gate repository root")
    except SecureFileError as exc:
        raise GateRunnerError(str(exc)) from exc
    return canonical


def _static_path_pattern(path: str, *, windows: bool) -> re.Pattern[str]:
    """Match static spellings canonically equal through separators and dot segments."""

    spelling = path.replace("\\", "/")
    if windows:
        spelling = spelling.casefold()
    parts = spelling.split("/")
    separator = r"[\\/](?:\.[\\/])*"
    prefix = r"(?:\.[\\/])*" if not Path(path).is_absolute() else ""
    body = separator.join(re.escape(part) for part in parts)
    path_character = r"[\w.\\/\-]"
    return re.compile(rf"(?<!{path_character}){prefix}{body}(?!{path_character})")


def _contains_static_reference(
    value: str, *, reference_paths: Sequence[str], windows: bool
) -> bool:
    inspected = value.casefold() if windows else value
    return any(
        _static_path_pattern(path, windows=windows).search(inspected) is not None
        for path in reference_paths
    )


def _windows_extended_path_aliases(path: Path) -> tuple[str, ...]:
    """Return Windows namespace spellings that name the same absolute path."""

    import ctypes

    spelling = str(path)
    if spelling.startswith("\\\\"):
        return (spelling, "\\\\?\\UNC\\" + spelling.lstrip("\\"))
    drive, tail = os.path.splitdrive(spelling)
    if not drive:
        raise GateRunnerError("Windows reference path has no drive identity")
    device_name = ctypes.create_unicode_buffer(32768)
    query_dos_device = ctypes.windll.kernel32.QueryDosDeviceW
    if not query_dos_device(drive, device_name, len(device_name)):
        raise GateRunnerError("Windows reference drive device identity is unavailable")
    return (
        spelling,
        "\\\\?\\" + spelling,
        "\\\\.\\" + spelling,
        "\\\\?\\GLOBALROOT" + device_name.value + tail,
    )


def _prepare_gate(
    gate: dict[str, Any],
    approval: dict[str, Any],
    *,
    repository_root: Path,
    environment: Mapping[str, str],
) -> _PreparedGate:
    execution = approval["execution"]
    if approval["gateId"] != gate["id"]:
        raise GateRunnerError("approval gateId does not match the runnable gate")
    if approval["gateDefinitionDigest"] != canonical_digest(gate):
        raise GateRunnerError("approval gate definition digest does not match the runnable gate")
    for field in (
        "command",
        "successMarker",
        "workingDirectory",
        "shell",
        "platform",
        "environmentDigest",
    ):
        if execution[field] != gate[field]:
            raise GateRunnerError(f"approval {field} does not match the runnable gate")
    actual_platform = current_platform_identity()
    if execution["platform"] != actual_platform:
        raise GateRunnerError(
            f"approved platform {execution['platform']!r} does not match host {actual_platform!r}"
        )
    if execution["environmentDigest"] != environment_digest(environment, actual_platform):
        raise GateRunnerError("approved environment identity does not match the exact runtime environment")

    root = repository_root
    try:
        if execution["workingDirectory"] == ".":
            cwd = require_contained(
                root, root, label="gate working directory", allow_root=True
            )
        else:
            cwd = require_contained(
                root.joinpath(*execution["workingDirectory"].split("/")),
                root,
                label="gate working directory",
                allow_root=True,
            )
    except SecureFileError as exc:
        raise GateRunnerError(str(exc)) from exc
    if not cwd.is_dir():
        raise GateRunnerError("approved working directory is not an existing directory")

    parsing_platform = "windows" if actual_platform.startswith("windows-") else "posix"
    try:
        argv = parse_command(execution["command"], platform=parsing_platform)
    except (ValueError, OSError) as exc:
        raise GateRunnerError(f"approved direct command is malformed: {exc}") from exc
    if not argv:
        raise GateRunnerError("approved direct command has no executable")
    try:
        executable = canonical_executable_path(
            execution["executable"]["path"], "approved executable path"
        )
        command_executable = canonical_executable_path(argv[0], "command argv[0]")
    except GateRunnerError:
        raise
    if os.path.normcase(str(command_executable)) != os.path.normcase(str(executable)):
        raise GateRunnerError("command executable does not match the exact approved executable path")
    bindings = execution["referenceBindings"]
    reference_paths = [reference["path"] for reference in execution["referencedFiles"]]
    binding_paths = [binding["path"] for binding in bindings]
    if sorted(binding_paths) != sorted(reference_paths):
        raise GateRunnerError(
            "approval reference bindings do not exactly match declared referenced-file paths"
        )
    bound_indexes = {binding["argvIndex"] for binding in bindings}
    for binding in bindings:
        index = binding["argvIndex"]
        if index >= len(argv) or argv[index] != binding["path"]:
            raise GateRunnerError(
                f"referenced file {binding['path']} must occupy exact argv slot {index}"
            )
    windows = actual_platform.startswith("windows-")
    static_reference_paths: list[str] = []
    for reference in execution["referencedFiles"]:
        source = root.joinpath(*reference["path"].split("/"))
        static_reference_paths.append(reference["path"])
        if windows:
            static_reference_paths.extend(_windows_extended_path_aliases(source))
        else:
            static_reference_paths.append(str(source))
    for index, argument in enumerate(argv[1:], start=1):
        if index in bound_indexes:
            continue
        if _contains_static_reference(
            argument, reference_paths=static_reference_paths, windows=windows
        ):
            raise GateRunnerError("referenced paths may not be embedded in opaque argv strings")
    for value in environment.values():
        if _contains_static_reference(
            value, reference_paths=static_reference_paths, windows=windows
        ):
            raise GateRunnerError("referenced paths may not be embedded in approved environment values")
    limits = execution["limits"]
    actual_executable_digest = digest_file(
        executable, max_bytes=limits["maxExecutableBytes"]
    )
    if actual_executable_digest != execution["executable"]["digest"]:
        raise GateRunnerError("approved executable content identity changed before launch")
    isolation = execution["isolation"]
    if windows:
        if isolation["mode"] != WINDOWS_ISOLATION_MODE:
            raise GateRunnerError("approved Windows executable isolation mode changed")
    else:
        if isolation["mode"] != POSIX_ISOLATION_MODE:
            raise GateRunnerError("approved POSIX executable isolation mode changed")
        actual_source_mode = stat.S_IMODE(executable.stat(follow_symlinks=False).st_mode)
        if actual_source_mode != isolation["sourceExecutableMode"]:
            raise GateRunnerError("approved executable mode changed before launch")

    for reference in execution["referencedFiles"]:
        target = root.joinpath(*reference["path"].split("/"))
        try:
            payload = read_no_follow(
                target,
                root,
                label=f"gate referenced file {reference['path']}",
                max_bytes=limits["maxReferenceBytes"],
            )
        except SecureFileError as exc:
            raise GateRunnerError(str(exc)) from exc
        actual = _DIGEST_PREFIX + hashlib.sha256(payload).hexdigest()
        if actual != reference["digest"]:
            raise GateRunnerError(
                f"gate referenced file {reference['path']} content identity changed before launch"
            )
    return _PreparedGate(gate, approval, tuple(argv), root, cwd)


def _now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _payload_digest(value: str) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _result(
    *,
    gate_id: str,
    state: str,
    executed: bool,
    execution_identity_digest: str | None,
    verified_at: str,
    return_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    artifact_before_digest: str | None = None,
    artifact_digest: str | None = None,
    elapsed_ms: int = 0,
    reason: str,
) -> GateExecutionResult:
    evidence = {
        "schemaVersion": RESULT_SCHEMA_VERSION,
        "gateId": gate_id,
        "state": state,
        "executed": executed,
        "executionIdentityDigest": execution_identity_digest,
        "verifiedAt": verified_at,
        "returnCode": return_code,
        "stdoutDigest": _payload_digest(stdout),
        "stderrDigest": _payload_digest(stderr),
        "artifactBeforeDigest": artifact_before_digest,
        "artifactDigest": artifact_digest,
        "elapsedMs": elapsed_ms,
        "reason": reason,
    }
    return GateExecutionResult(
        gate_id=gate_id,
        state=state,
        executed=executed,
        execution_identity_digest=execution_identity_digest,
        evidence_digest=canonical_digest(evidence),
        verified_at=verified_at,
        return_code=return_code,
        stdout=stdout,
        stderr=stderr,
        stdout_digest=evidence["stdoutDigest"],
        stderr_digest=evidence["stderrDigest"],
        artifact_before_digest=artifact_before_digest,
        artifact_digest=artifact_digest,
        elapsed_ms=elapsed_ms,
        reason=reason,
    )


def _decode_error_output(value: bytes, maximum: int) -> str:
    return value[:maximum].decode("utf-8", errors="replace")


def _evaluate_marker(
    prepared: _PreparedGate,
    result: subprocess.CompletedProcess[str],
    artifact_cwd: Path,
    artifact_before_digest: str | None,
) -> tuple[bool, str | None, str]:
    gate, execution = prepared.gate, prepared.approval["execution"]
    limits = execution["limits"]
    marker = gate["successMarker"]
    if marker.startswith("stdout-exact:"):
        met = result.stdout == marker[len("stdout-exact:"):]
        reason = "stdout exactly matched the approved decisive marker"
        if not met:
            reason = "stdout did not exactly match the approved decisive marker"
        return met, None, reason
    expected_artifact = artifact_marker_digest(marker)
    artifact_path = execution["artifactPath"]
    try:
        payload = read_no_follow(
            artifact_cwd.joinpath(*artifact_path.split("/")),
            artifact_cwd,
            label=f"gate artifact {artifact_path}",
            max_bytes=limits["maxArtifactBytes"],
        )
    except SecureFileError as exc:
        if artifact_before_digest is not None:
            return (
                False,
                None,
                f"artifact did not make a fresh content transition during this gate run: {exc}",
            )
        return False, None, f"approved decisive artifact could not be read safely: {exc}"
    artifact_digest = _DIGEST_PREFIX + hashlib.sha256(payload).hexdigest()
    met = artifact_digest == expected_artifact and artifact_digest != artifact_before_digest
    if artifact_digest == artifact_before_digest:
        reason = "artifact did not make a fresh content transition during this gate run"
    elif artifact_digest != expected_artifact:
        reason = "artifact digest did not match the approved decisive marker"
    else:
        reason = "artifact digest freshly transitioned to the approved decisive marker"
    return met, artifact_digest, reason


def _execute_prepared(
    prepared: _PreparedGate,
    *,
    environment: Mapping[str, str],
    process_runner: Callable[..., subprocess.CompletedProcess[Any]],
    verified_at: Callable[[], str],
) -> GateExecutionResult:
    gate, approval = prepared.gate, prepared.approval
    execution = approval["execution"]
    limits = execution["limits"]
    identity_digest = approval["executionIdentityDigest"]
    started = time.monotonic()
    artifact_before_digest = None
    process_entered = False
    try:
        with staged_execution_surface(
            repository_root=prepared.repository_root,
            source_cwd=prepared.cwd,
            argv=prepared.argv,
            executable_path=Path(execution["executable"]["path"]),
            executable_digest=execution["executable"]["digest"],
            isolation=execution["isolation"],
            max_executable_bytes=limits["maxExecutableBytes"],
            references=execution["referencedFiles"],
            reference_bindings=execution["referenceBindings"],
            max_reference_bytes=limits["maxReferenceBytes"],
            artifact_path=execution["artifactPath"],
            max_artifact_bytes=limits["maxArtifactBytes"],
        ) as snapshot:
            artifact_before_digest = snapshot.artifact_before_digest
            process_entered = True
            raw_result = process_runner(
                list(snapshot.argv),
                cwd=snapshot.cwd,
                environment=dict(environment),
                stdin=None,
                timeout=limits["timeoutMs"] / 1000,
                max_output_bytes=limits["maxOutputBytes"],
                terminate_process_group_on_parent_exit=True,
            )
            result = completed_text(raw_result, max_output_bytes=limits["maxOutputBytes"])
            marker_met, artifact_digest, marker_reason = _evaluate_marker(
                prepared, result, snapshot.cwd, artifact_before_digest
            )
    except BoundedProcessError as exc:
        elapsed = max(0, round((time.monotonic() - started) * 1000))
        return _result(
            gate_id=gate["id"],
            state="blocked",
            executed=process_entered,
            execution_identity_digest=identity_digest,
            verified_at=verified_at(),
            stdout=_decode_error_output(exc.stdout, limits["maxOutputBytes"]),
            stderr=_decode_error_output(exc.stderr, limits["maxOutputBytes"]),
            artifact_before_digest=artifact_before_digest,
            elapsed_ms=elapsed,
            reason=str(exc),
        )
    except (OSError, ValueError, GateSnapshotError) as exc:
        elapsed = max(0, round((time.monotonic() - started) * 1000))
        return _result(
            gate_id=gate["id"],
            state="blocked",
            executed=process_entered,
            execution_identity_digest=identity_digest,
            verified_at=verified_at(),
            artifact_before_digest=artifact_before_digest,
            elapsed_ms=elapsed,
            reason=f"approved process could not be evaluated safely: {exc}",
        )

    elapsed = max(0, round((time.monotonic() - started) * 1000))
    return_code_met = result.returncode == 0
    state = "met" if return_code_met and marker_met else "unmet"
    if not return_code_met:
        marker_reason = f"command exited with {result.returncode}; {marker_reason}"
    return _result(
        gate_id=gate["id"],
        state=state,
        executed=True,
        execution_identity_digest=identity_digest,
        verified_at=verified_at(),
        return_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        artifact_digest=artifact_digest,
        artifact_before_digest=artifact_before_digest,
        elapsed_ms=elapsed,
        reason=marker_reason,
    )


def run_approved_gates(
    outcome_gate_ledger: Mapping[str, Any],
    approvals: Sequence[TrustedGateApproval],
    *,
    repository_root: Path,
    environment: Mapping[str, str],
    process_runner: Callable[..., subprocess.CompletedProcess[Any]] = run_bounded_text,
    verified_at: Callable[[], str] = _now_rfc3339,
    before_launch: Callable[[str], None] | None = None,
    selected_gate_ids: Sequence[str] | None = None,
) -> tuple[GateExecutionResult, ...]:
    """Run explicitly approved command gates serially in immutable ledger order.

    Missing, malformed, stale, or mismatched approval yields a blocked gate result.
    Manual-review gates are never executed.  Unknown or duplicate approval identities
    reject the batch before any command can launch.
    """

    root = _canonical_repository_root(repository_root)
    try:
        environment_snapshot = dict(environment)
    except (TypeError, ValueError) as exc:
        raise GateRunnerError("environment could not be snapshotted exactly once") from exc
    # Validate the detached snapshot now; the caller-owned mapping is never read again.
    environment_digest(environment_snapshot, current_platform_identity())
    try:
        ledger = validate_outcome_gate_ledger(outcome_gate_ledger)
    except ContractValidationError as exc:
        raise GateRunnerError(f"outcome-gate ledger is invalid: {exc}") from exc
    if not isinstance(approvals, Sequence) or isinstance(approvals, (str, bytes)):
        raise GateRunnerError("approvals must be an ordered sequence")
    if len(approvals) > MAX_APPROVALS:
        raise GateRunnerError(f"approval count exceeds {MAX_APPROVALS}")
    gate_ids = {gate["id"] for gate in ledger["gates"]}
    selected_ids = gate_ids
    if selected_gate_ids is not None:
        if (
            not isinstance(selected_gate_ids, Sequence)
            or isinstance(selected_gate_ids, (str, bytes))
            or any(not isinstance(item, str) for item in selected_gate_ids)
            or len(set(selected_gate_ids)) != len(selected_gate_ids)
            or not set(selected_gate_ids) <= gate_ids
        ):
            raise GateRunnerError("selected gate IDs must be unique known ledger gate IDs")
        selected_ids = set(selected_gate_ids)
    approvals_by_gate: dict[str, dict[str, Any]] = {}
    approval_errors: dict[str, str] = {}
    for index, raw in enumerate(approvals):
        try:
            record = consume_trusted_gate_approval(raw)
        except ApprovalBoundaryError as exc:
            raise GateRunnerError(f"approval[{index}] is untrusted: {exc}") from exc
        gate_id = record.get("gateId")
        if not isinstance(gate_id, str) or gate_id not in gate_ids:
            raise GateRunnerError(f"approval[{index}] has an unknown gateId")
        if gate_id in approvals_by_gate or gate_id in approval_errors:
            raise GateRunnerError(f"duplicate or ambiguous approval for gate {gate_id!r}")
        try:
            approvals_by_gate[gate_id] = validate_gate_approval(record)
        except GateRunnerError as exc:
            approval_errors[gate_id] = str(exc)

    results: list[GateExecutionResult] = []
    for gate in ledger["gates"]:
        if gate["id"] not in selected_ids:
            continue
        gate_id = gate["id"]
        if gate["verificationType"] != "command":
            results.append(
                _result(
                    gate_id=gate_id,
                    state="blocked",
                    executed=False,
                    execution_identity_digest=None,
                    verified_at=verified_at(),
                    reason="manual review gate is not executable by the D2 command runner",
                )
            )
            continue
        if gate_id in approval_errors:
            results.append(
                _result(
                    gate_id=gate_id,
                    state="blocked",
                    executed=False,
                    execution_identity_digest=None,
                    verified_at=verified_at(),
                    reason=f"approval is invalid: {approval_errors[gate_id]}",
                )
            )
            continue
        approval = approvals_by_gate.get(gate_id)
        if approval is None:
            results.append(
                _result(
                    gate_id=gate_id,
                    state="blocked",
                    executed=False,
                    execution_identity_digest=None,
                    verified_at=verified_at(),
                    reason="explicit exact operator approval is missing",
                )
            )
            continue
        try:
            if before_launch is not None:
                before_launch(gate_id)
            prepared = _prepare_gate(
                gate,
                approval,
                repository_root=root,
                environment=environment_snapshot,
            )
        except (GateRunnerError, SecureFileError) as exc:
            results.append(
                _result(
                    gate_id=gate_id,
                    state="blocked",
                    executed=False,
                    execution_identity_digest=approval["executionIdentityDigest"],
                    verified_at=verified_at(),
                    reason=str(exc),
                )
            )
            continue
        results.append(
            _execute_prepared(
                prepared,
                environment=environment_snapshot,
                process_runner=process_runner,
                verified_at=verified_at,
            )
        )
    return tuple(results)


def validate_gate_execution_identity(
    gate: Mapping[str, Any],
    approval_record: Mapping[str, Any],
    *,
    repository_root: Path,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    """Revalidate detached execution audit data without granting launch authority."""

    try:
        selected_gate = copy.deepcopy(dict(gate))
    except (TypeError, ValueError) as exc:
        raise GateRunnerError("gate must be one detached mapping") from exc
    validated = validate_gate_approval(approval_record)
    _prepare_gate(
        selected_gate,
        validated,
        repository_root=_canonical_repository_root(repository_root),
        environment=dict(environment),
    )
    return validated


__all__ = [
    "APPROVAL_SCHEMA_VERSION",
    "RESULT_SCHEMA_VERSION",
    "DIRECT_SHELL_IDENTITY",
    "GateExecutionResult",
    "GateRunnerError",
    "current_platform_identity",
    "digest_file",
    "environment_digest",
    "render_direct_command",
    "run_approved_gates",
    "validate_gate_execution_identity",
    "validate_gate_approval",
]
