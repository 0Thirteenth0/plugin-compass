"""Immutable/staged check-use surface for D2 gate execution."""

from __future__ import annotations

import ctypes
import hashlib
import os
import stat
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .secure_files import SecureFileError, read_no_follow, write_new_no_follow


class GateSnapshotError(RuntimeError):
    """An approved execution surface could not be frozen safely."""


@dataclass(frozen=True)
class GateExecutionSnapshot:
    argv: tuple[str, ...]
    cwd: Path
    artifact_before_digest: str | None


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@contextmanager
def _windows_executable_lock(path: Path) -> Iterator[None]:
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    generic_read = 0x80000000
    share_read = 0x00000001
    open_existing = 3
    open_reparse_point = 0x00200000
    handle = kernel.CreateFileW(
        str(path), generic_read, share_read, None, open_existing, open_reparse_point, None
    )
    if ctypes.c_void_p(handle).value == ctypes.c_void_p(-1).value:
        raise GateSnapshotError(
            f"approved executable could not be mutation-locked: Windows error {ctypes.get_last_error()}"
        )
    try:
        yield
    finally:
        if not kernel.CloseHandle(handle):
            raise GateSnapshotError("approved executable mutation lock could not be released")


@contextmanager
def staged_execution_surface(
    *,
    repository_root: Path,
    source_cwd: Path,
    argv: Sequence[str],
    executable_path: Path,
    executable_digest: str,
    isolation: Mapping[str, Any],
    max_executable_bytes: int,
    references: Sequence[Mapping[str, Any]],
    reference_bindings: Sequence[Mapping[str, Any]],
    max_reference_bytes: int,
    artifact_path: str | None,
    max_artifact_bytes: int,
) -> Iterator[GateExecutionSnapshot]:
    """Freeze approved inputs and retain the surface until the process terminates."""

    root = repository_root
    try:
        cwd_relative = source_cwd.relative_to(root)
    except ValueError as exc:
        raise GateSnapshotError("gate cwd is outside the registered repository root") from exc
    with tempfile.TemporaryDirectory(prefix=".compass-builder-gate-", dir=root) as temporary:
        stage_root = Path(temporary) / "workspace"
        stage_root.mkdir()
        stage_cwd = stage_root / cwd_relative
        stage_cwd.mkdir(parents=True, exist_ok=True)
        staged_references: dict[str, Path] = {}
        for reference in references:
            source = root.joinpath(*reference["path"].split("/"))
            try:
                payload = read_no_follow(
                    source,
                    root,
                    label=f"gate referenced file {reference['path']}",
                    max_bytes=max_reference_bytes,
                )
            except SecureFileError as exc:
                raise GateSnapshotError(str(exc)) from exc
            if _digest(payload) != reference["digest"]:
                raise GateSnapshotError(
                    f"gate referenced file {reference['path']} changed before immutable staging"
                )
            destination = stage_root.joinpath(*reference["path"].split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                write_new_no_follow(
                    destination,
                    payload,
                    stage_root,
                    label=f"staged gate reference {reference['path']}",
                )
                source_mode = source.stat(follow_symlinks=False).st_mode
                destination.chmod(stat.S_IMODE(source_mode) & ~0o222)
            except (OSError, SecureFileError) as exc:
                raise GateSnapshotError(
                    f"gate referenced file {reference['path']} could not be staged: {exc}"
                ) from exc
            staged_references[reference["path"]] = destination

        if artifact_path is not None:
            source_artifact = source_cwd.joinpath(*artifact_path.split("/"))
            artifact_before_digest = None
            if source_artifact.exists() or source_artifact.is_symlink():
                try:
                    artifact_before = read_no_follow(
                        source_artifact,
                        source_cwd,
                        label=f"gate pre-run artifact {artifact_path}",
                        max_bytes=max_artifact_bytes,
                    )
                except SecureFileError as exc:
                    raise GateSnapshotError(str(exc)) from exc
                artifact_before_digest = _digest(artifact_before)
            stage_cwd.joinpath(*artifact_path.split("/")).parent.mkdir(
                parents=True, exist_ok=True
            )
        else:
            artifact_before_digest = None

        staged_argv = list(argv)
        for binding in reference_bindings:
            staged_reference = staged_references.get(binding["path"])
            if staged_reference is None:
                raise GateSnapshotError(
                    "gate reference binding does not exactly match a staged referenced file"
                )
            index = binding["argvIndex"]
            if index >= len(staged_argv):
                raise GateSnapshotError("gate reference binding argv index is out of range")
            staged_argv[index] = str(staged_reference)

        executable = Path(executable_path)
        with ExitStack() as stack:
            if os.name == "nt":
                if isolation["mode"] != "windows-locked-original-v1":
                    raise GateSnapshotError("Windows executable isolation identity is invalid")
                stack.enter_context(_windows_executable_lock(executable))
                try:
                    payload = read_no_follow(
                        executable,
                        executable.parent,
                        label="approved executable",
                        max_bytes=max_executable_bytes,
                    )
                except SecureFileError as exc:
                    raise GateSnapshotError(str(exc)) from exc
                if _digest(payload) != executable_digest:
                    raise GateSnapshotError("approved executable changed before mutation lock")
                staged_argv[0] = str(executable)
            else:
                if isolation["mode"] != "posix-staged-copy-v1":
                    raise GateSnapshotError("POSIX executable isolation identity is invalid")
                actual_source_mode = stat.S_IMODE(
                    executable.stat(follow_symlinks=False).st_mode
                )
                if actual_source_mode != isolation["sourceExecutableMode"]:
                    raise GateSnapshotError("approved executable mode changed before staging")
                try:
                    payload = read_no_follow(
                        executable,
                        executable.parent,
                        label="approved executable",
                        max_bytes=max_executable_bytes,
                    )
                except SecureFileError as exc:
                    raise GateSnapshotError(str(exc)) from exc
                if _digest(payload) != executable_digest:
                    raise GateSnapshotError("approved executable changed before immutable staging")
                staged_executable = stage_root / ".executable" / executable.name
                staged_executable.parent.mkdir()
                write_new_no_follow(
                    staged_executable,
                    payload,
                    stage_root,
                    label="staged approved executable",
                )
                staged_executable.chmod(isolation["stagedExecutableMode"])
                staged_argv[0] = str(staged_executable)
            yield GateExecutionSnapshot(
                tuple(staged_argv), stage_cwd, artifact_before_digest
            )


__all__ = ["GateExecutionSnapshot", "GateSnapshotError", "staged_execution_surface"]
