"""Canonical repository identity and isolated Git evidence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import StateError
from .git_environment import GitEnvironment, validate_git_environment
from .process_runner import BoundedProcessError, run_bounded_text
from .secure_files import is_reparse, reject_reparse_components


@dataclass(frozen=True)
class RepositoryIdentity:
    root: Path
    common_git_dir: Path
    git_dir: Path


def git_text(
    repo: Path, arguments: list[str], *, check: bool = True,
    git_environment: GitEnvironment | None = None,
) -> str:
    environment = None if git_environment is None else validate_git_environment(git_environment).environment
    try:
        result = run_bounded_text(
            ["git", "-C", str(repo), *arguments], environment=environment, timeout=20,
        )
    except BoundedProcessError as exc:
        raise StateError(f"Git repository evidence is unavailable: {exc}") from exc
    if check and result.returncode:
        raise StateError(
            "Git repository evidence failed closed: "
            + (result.stderr.strip() or result.stdout.strip() or "Git command failed")
        )
    return result.stdout


def resolve_repository(
    repository: Path, git_environment: GitEnvironment | None = None,
) -> RepositoryIdentity:
    raw = Path(repository)
    if not raw.is_absolute():
        raise StateError("repository path must be absolute")
    reject_reparse_components(raw, label="repository path")
    try:
        requested = raw.resolve(strict=True)
    except OSError as exc:
        raise StateError(f"repository path is missing or unreadable: {exc}") from exc
    if not requested.is_dir() or is_reparse(requested):
        raise StateError("repository root must be a real non-reparse directory")
    output = git_text(
        requested,
        ["rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir", "--git-dir"],
        git_environment=git_environment,
    ).splitlines()
    if len(output) != 3:
        raise StateError("Git returned ambiguous repository identity evidence")
    try:
        paths = tuple(Path(item) for item in output)
        for item in paths:
            reject_reparse_components(item, label="Git repository identity")
        root, common, git_dir = (item.resolve(strict=True) for item in paths)
    except OSError as exc:
        raise StateError(f"Git repository identity contains an unreadable path: {exc}") from exc
    if root != requested:
        raise StateError("repository path must name the canonical checkout root exactly")
    if any(not path.is_dir() or is_reparse(path) for path in (root, common, git_dir)):
        raise StateError("repository identity contains a non-directory or reparse target")
    return RepositoryIdentity(root, common, git_dir)


__all__ = ["RepositoryIdentity", "git_text", "resolve_repository"]
