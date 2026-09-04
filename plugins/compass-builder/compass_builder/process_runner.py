"""No-shell subprocess execution with hard time and output bounds."""

from __future__ import annotations

import subprocess
import threading
import os
import shlex
import signal
import time
import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Mapping, Sequence


PROCESS_TIMEOUT_SECONDS = 30.0
MAX_CAPTURE_BYTES = 1_048_576
READ_CHUNK_BYTES = 8192


class BoundedProcessError(RuntimeError):
    """A subprocess exceeded a security bound or could not be executed."""

    def __init__(self, message: str, *, stdout: bytes = b"", stderr: bytes = b""):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class _WindowsJob:
    def __init__(self, process: subprocess.Popen[bytes]):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)

        class Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class Io(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", Basic), ("IoInfo", Io),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel.CloseHandle(handle)
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        if not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(process._handle)):
            kernel.CloseHandle(handle)
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        self._kernel = kernel
        self._handle = handle

    def terminate(self) -> None:
        if self._handle:
            self._kernel.TerminateJobObject(self._handle, 1)

    def resume(self, process: subprocess.Popen[bytes]) -> None:
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        ntdll.NtResumeProcess.restype = ctypes.c_long
        status = ntdll.NtResumeProcess(wintypes.HANDLE(process._handle))
        if status != 0:
            raise OSError(status, "NtResumeProcess failed")

    def close(self) -> None:
        if self._handle:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


def parse_command(command: str, *, platform: str | None = None) -> list[str]:
    """Parse a no-shell validation command using the host platform's argv rules."""

    selected = platform or ("windows" if os.name == "nt" else "posix")
    if selected == "posix":
        return shlex.split(command, posix=True)
    if selected != "windows":
        raise ValueError("unsupported command parsing platform")
    if os.name != "nt":
        return shlex.split(command, posix=False)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    count = ctypes.c_int()
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    pointer = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not pointer:
        raise ValueError("Windows command line is malformed")
    try:
        return [pointer[index] for index in range(count.value)]
    finally:
        kernel.LocalFree(pointer)


def run_bounded(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    stdin: bytes | None = None,
    timeout: float = PROCESS_TIMEOUT_SECONDS,
    max_output_bytes: int = MAX_CAPTURE_BYTES,
    terminate_process_group_on_parent_exit: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    """Run argv without a shell while retaining at most ``max_output_bytes`` per pipe."""

    if not argv or timeout <= 0 or max_output_bytes <= 0:
        raise ValueError("bounded process arguments and limits must be positive")
    popen_options: dict[str, object] = {}
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000004
    else:
        popen_options["start_new_session"] = True
    try:
        process = subprocess.Popen(
            list(argv), cwd=cwd, env=None if environment is None else dict(environment),
            shell=False, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen_options,
        )
    except OSError as exc:
        raise BoundedProcessError(f"process could not start: {exc}") from exc

    tree = None
    try:
        tree = _WindowsJob(process) if os.name == "nt" else None
        if tree is not None:
            tree.resume(process)
    except OSError as exc:
        if tree is not None:
            tree.terminate()
            tree.close()
        else:
            process.kill()
        process.wait()
        raise BoundedProcessError(f"process tree ownership failed: {exc}") from exc

    captures = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = threading.Event()
    reader_errors: list[BaseException] = []
    capture_lock = threading.Lock()

    def read_pipe(name: str, pipe) -> None:
        try:
            while True:
                chunk = pipe.read(READ_CHUNK_BYTES)
                if not chunk:
                    return
                with capture_lock:
                    target = captures[name]
                    remaining = max_output_bytes - len(target)
                    if remaining > 0:
                        target.extend(chunk[:remaining])
                    too_large = len(chunk) > remaining
                if too_large:
                    overflow.set()
                    _terminate_tree(process, tree)
                    return
        except BaseException as exc:
            reader_errors.append(exc)
            _terminate_tree(process, tree)
        finally:
            try:
                pipe.close()
            except OSError:
                pass

    assert process.stdout is not None and process.stderr is not None
    readers = [
        threading.Thread(target=read_pipe, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=read_pipe, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()

    pipe_workers = list(readers)
    writer_errors: list[BaseException] = []
    if stdin is not None:
        assert process.stdin is not None
        def write_input() -> None:
            try:
                process.stdin.write(stdin)
                process.stdin.flush()
            except BrokenPipeError:
                pass
            except BaseException as exc:
                writer_errors.append(exc)
                _terminate_tree(process, tree)
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    pass

        writer = threading.Thread(target=write_input, daemon=True)
        writer.start()
        pipe_workers.append(writer)

    timed_out = False
    termination_timed_out = False
    deadline = time.monotonic() + timeout
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_tree(process, tree)
        try:
            process.wait(timeout=max(0.05, min(1.0, timeout)))
        except subprocess.TimeoutExpired:
            termination_timed_out = True
    finally:
        if terminate_process_group_on_parent_exit:
            _terminate_tree(process, tree, include_exited_posix_parent=True)
        if tree is not None:
            tree.close()
        elif process.poll() is None:
            _terminate_tree(process, tree)
        for worker in pipe_workers:
            worker.join(timeout=max(0.01, deadline - time.monotonic() + 0.25))
        if any(worker.is_alive() for worker in pipe_workers):
            try:
                process.kill()
            except OSError:
                pass
            raise BoundedProcessError(
                "process pipe worker did not terminate",
                stdout=bytes(captures["stdout"]), stderr=bytes(captures["stderr"]),
            )

    stdout = bytes(captures["stdout"])
    stderr = bytes(captures["stderr"])
    if reader_errors:
        raise BoundedProcessError(
            f"process output reader failed: {reader_errors[0]}", stdout=stdout, stderr=stderr
        )
    if writer_errors:
        raise BoundedProcessError(
            f"process input writer failed: {writer_errors[0]}", stdout=stdout, stderr=stderr
        )
    if termination_timed_out:
        raise BoundedProcessError(
            "process tree did not terminate within the wall bound",
            stdout=stdout, stderr=stderr,
        )
    if timed_out:
        raise BoundedProcessError(
            f"process timed out after {timeout:g} seconds", stdout=stdout, stderr=stderr
        )
    if overflow.is_set():
        raise BoundedProcessError(
            f"process output exceeded {max_output_bytes} bytes", stdout=stdout, stderr=stderr
        )
    return subprocess.CompletedProcess(list(argv), process.returncode, stdout, stderr)


def _terminate_tree(
    process: subprocess.Popen[bytes],
    tree: _WindowsJob | None,
    *,
    include_exited_posix_parent: bool = False,
) -> None:
    try:
        if tree is not None:
            tree.terminate()
        elif include_exited_posix_parent or process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        try:
            process.kill()
        except OSError:
            pass


def completed_text(
    result: subprocess.CompletedProcess[object], *, max_output_bytes: int = MAX_CAPTURE_BYTES
) -> subprocess.CompletedProcess[str]:
    """Normalize an injected result while enforcing the same captured-output ceiling."""

    values: list[str] = []
    for name in ("stdout", "stderr"):
        raw = getattr(result, name, "")
        if raw is None:
            text = ""
        elif isinstance(raw, bytes):
            if len(raw) > max_output_bytes:
                raise BoundedProcessError(f"process {name} exceeded {max_output_bytes} bytes")
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(raw)
            if len(text.encode("utf-8")) > max_output_bytes:
                raise BoundedProcessError(f"process {name} exceeded {max_output_bytes} bytes")
        values.append(text)
    return subprocess.CompletedProcess(
        result.args, result.returncode, values[0], values[1]
    )


def run_bounded_text(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    stdin: str | None = None,
    timeout: float = PROCESS_TIMEOUT_SECONDS,
    max_output_bytes: int = MAX_CAPTURE_BYTES,
    terminate_process_group_on_parent_exit: bool = False,
) -> subprocess.CompletedProcess[str]:
    return completed_text(run_bounded(
        argv, cwd=cwd, environment=environment,
        stdin=None if stdin is None else stdin.encode("utf-8"), timeout=timeout,
        max_output_bytes=max_output_bytes,
        terminate_process_group_on_parent_exit=terminate_process_group_on_parent_exit,
    ), max_output_bytes=max_output_bytes)


__all__ = [
    "BoundedProcessError", "MAX_CAPTURE_BYTES", "completed_text", "parse_command", "run_bounded",
    "run_bounded_text",
]
