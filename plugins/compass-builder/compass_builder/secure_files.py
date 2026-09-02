"""Descriptor-safe, reparse-aware filesystem primitives for controller state."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
import os
import stat
from pathlib import Path
from typing import Iterator

from .errors import StateError


class SecureFileError(StateError):
    pass


def is_reparse(path: Path) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    value = path.lstat()
    return path.is_symlink() or bool(
        getattr(value, "st_file_attributes", 0)
        & getattr(value, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def reject_reparse_components(path: Path, *, label: str) -> None:
    raw = Path(path).absolute()
    for component in (raw, *raw.parents):
        if (component.exists() or component.is_symlink()) and is_reparse(component):
            raise SecureFileError(f"{label} contains a symlink or reparse ancestor: {component}")


def require_contained(path: Path, root: Path, *, label: str, allow_root: bool = False) -> Path:
    lexical, lexical_root = Path(path).absolute(), Path(root).absolute()
    try:
        lexical.relative_to(lexical_root)
    except ValueError as exc:
        raise SecureFileError(f"{label} escapes its registered controller root") from exc
    if lexical == lexical_root and not allow_root:
        raise SecureFileError(f"{label} may not target the controller root")
    reject_reparse_components(lexical, label=label)
    reject_reparse_components(lexical_root, label=label)
    try:
        resolved, resolved_root = lexical.resolve(strict=False), lexical_root.resolve(strict=False)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise SecureFileError(f"{label} escapes its registered controller root") from exc
    return lexical


def _guarded_directories(target: Path, root: Path) -> tuple[Path, ...]:
    relative_parent = target.parent.relative_to(root)
    directories = [root]
    current = root
    for component in relative_parent.parts:
        current /= component
        directories.append(current)
    return tuple(directories)


@contextmanager
def _posix_directory_guard(target: Path, root: Path, *, label: str) -> Iterator[int]:
    descriptors: list[int] = []
    flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(root, flags)
        descriptors.append(descriptor)
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise SecureFileError(f"{label} controller root is not a directory")
        for component in target.parent.relative_to(root).parts:
            descriptor = os.open(component, flags, dir_fd=descriptors[-1])
            descriptors.append(descriptor)
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise SecureFileError(f"{label} ancestor is not a directory")
        yield descriptors[-1]
    except OSError as exc:
        raise SecureFileError(f"{label} containment guard failed: {exc}") from exc
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _windows_kernel32():
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD), ("creation", FILETIME),
            ("last_access", FILETIME), ("last_write", FILETIME),
            ("volume_serial", wintypes.DWORD), ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32, BY_HANDLE_FILE_INFORMATION


@contextmanager
def _windows_directory_guard(target: Path, root: Path, *, label: str) -> Iterator[None]:
    kernel32, information_type = _windows_kernel32()
    handles = []
    active_error = False
    file_list_directory = 0x0001
    file_read_attributes = 0x0080
    share_read_write = 0x00000001 | 0x00000002
    open_existing = 3
    backup_semantics = 0x02000000
    open_reparse_point = 0x00200000
    directory_attribute = 0x00000010
    reparse_attribute = 0x00000400
    invalid_handle = ctypes.c_void_p(-1).value
    try:
        for directory in _guarded_directories(target, root):
            handle = kernel32.CreateFileW(
                str(directory), file_list_directory | file_read_attributes,
                share_read_write, None,
                open_existing, backup_semantics | open_reparse_point, None,
            )
            if ctypes.c_void_p(handle).value == invalid_handle:
                raise SecureFileError(
                    f"{label} containment guard could not open {directory}: "
                    f"Windows error {ctypes.get_last_error()}"
                )
            handles.append(handle)
            information = information_type()
            if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
                raise SecureFileError(
                    f"{label} containment guard could not inspect {directory}: "
                    f"Windows error {ctypes.get_last_error()}"
                )
            if not information.attributes & directory_attribute or information.attributes & reparse_attribute:
                raise SecureFileError(f"{label} ancestor is not a real non-reparse directory")
            try:
                named = os.stat(directory, follow_symlinks=False)
            except OSError as exc:
                raise SecureFileError(f"{label} ancestor identity is unavailable: {exc}") from exc
            file_index = (information.file_index_high << 32) | information.file_index_low
            if (
                not stat.S_ISDIR(named.st_mode)
                or getattr(named, "st_file_attributes", 0) & reparse_attribute
                or named.st_ino != file_index
            ):
                raise SecureFileError(f"{label} ancestor identity changed while it was guarded")
        yield None
    except BaseException:
        active_error = True
        raise
    finally:
        close_failed = False
        for handle in reversed(handles):
            if not kernel32.CloseHandle(handle):
                close_failed = True
        if close_failed and not active_error:
            raise SecureFileError(f"{label} containment guard could not close safely")


@contextmanager
def _directory_guard(target: Path, root: Path, *, label: str) -> Iterator[int | None]:
    if os.name == "nt":
        with _windows_directory_guard(target, root, label=label):
            yield None
    else:
        with _posix_directory_guard(target, root, label=label) as parent_descriptor:
            yield parent_descriptor


def _open_at(target: Path, flags: int, mode: int, parent_descriptor: int | None) -> int:
    if parent_descriptor is None:
        return os.open(target, flags, mode)
    return os.open(target.name, flags, mode, dir_fd=parent_descriptor)


def _stat_at(target: Path, parent_descriptor: int | None) -> os.stat_result:
    if parent_descriptor is None:
        return os.stat(target, follow_symlinks=False)
    return os.stat(target.name, dir_fd=parent_descriptor, follow_symlinks=False)


def read_no_follow(path: Path, root: Path, *, label: str, max_bytes: int) -> bytes:
    lexical_root = Path(root).absolute()
    target = require_contained(path, lexical_root, label=label)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    with _directory_guard(target, lexical_root, label=label) as parent_descriptor:
        try:
            descriptor = _open_at(target, flags, 0o600, parent_descriptor)
        except OSError as exc:
            raise SecureFileError(f"{label} is unavailable: {exc}") from exc
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > max_bytes:
                raise SecureFileError(f"{label} is not a bounded regular file")
            chunks = []
            remaining = max_bytes + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) > max_bytes:
                raise SecureFileError(f"{label} exceeds its byte bound")
            named = _stat_at(target, parent_descriptor)
            if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
                raise SecureFileError(f"{label} changed while it was read")
            return payload
        except OSError as exc:
            raise SecureFileError(f"{label} could not be read safely: {exc}") from exc
        finally:
            os.close(descriptor)


def write_new_no_follow(path: Path, payload: bytes, root: Path, *, label: str) -> None:
    lexical_root = Path(root).absolute()
    target = require_contained(path, lexical_root, label=label)
    reject_reparse_components(target.parent, label=label)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    with _directory_guard(target, lexical_root, label=label) as parent_descriptor:
        try:
            descriptor = _open_at(target, flags, 0o600, parent_descriptor)
            try:
                written = 0
                while written < len(payload):
                    count = os.write(descriptor, payload[written:])
                    if count <= 0:
                        raise OSError("short immutable publication write")
                    written += count
                os.fsync(descriptor)
                opened = os.fstat(descriptor)
                named = _stat_at(target, parent_descriptor)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
                ):
                    raise SecureFileError(f"{label} changed while it was published")
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise SecureFileError(f"{label} publication failed: {exc}") from exc


__all__ = [
    "SecureFileError", "is_reparse", "read_no_follow", "reject_reparse_components",
    "require_contained", "write_new_no_follow",
]
