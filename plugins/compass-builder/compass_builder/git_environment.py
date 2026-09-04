"""Controller-owned Git environment isolation for Compass Builder workers."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


class GitEnvironmentError(ValueError):
    """A sanitized Git environment could not be created safely."""


@dataclass(frozen=True)
class GitEnvironment:
    """A process environment plus the digest of its controller-owned Git settings."""

    environment: Mapping[str, str]
    digest: str
    root: Path
    global_config: Path
    global_attributes: Path
    template_directory: Path
    hooks_directory: Path


_IDENTITY_NAME = "Compass Builder Worker"
_IDENTITY_EMAIL = "compass-builder@localhost.invalid"
_FIXED_GIT_DATE = "2000-01-01T00:00:00Z"


def _fixed_config(template_directory: Path, hooks_directory: Path) -> tuple[tuple[str, str], ...]:
    return (
        ("user.name", _IDENTITY_NAME),
        ("user.email", _IDENTITY_EMAIL),
        ("commit.gpgSign", "false"),
        ("tag.gpgSign", "false"),
        ("core.hooksPath", str(hooks_directory)),
        ("init.templateDir", str(template_directory)),
        ("core.autocrlf", "false"),
        ("core.filemode", "false"),
        ("core.eol", "lf"),
        ("core.safecrlf", "true"),
    )


def _controlled_environment(
    global_config: Path,
    global_attributes: Path,
    template_directory: Path,
    hooks_directory: Path,
) -> dict[str, str]:
    config = _fixed_config(template_directory, hooks_directory) + (
        ("core.attributesFile", str(global_attributes)),
    )
    controlled = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(global_config),
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TEMPLATE_DIR": str(template_directory),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_AUTHOR_NAME": _IDENTITY_NAME,
        "GIT_AUTHOR_EMAIL": _IDENTITY_EMAIL,
        "GIT_AUTHOR_DATE": _FIXED_GIT_DATE,
        "GIT_COMMITTER_NAME": _IDENTITY_NAME,
        "GIT_COMMITTER_EMAIL": _IDENTITY_EMAIL,
        "GIT_COMMITTER_DATE": _FIXED_GIT_DATE,
        "GIT_CONFIG_COUNT": str(len(config)),
    }
    for index, (key, value) in enumerate(config):
        controlled[f"GIT_CONFIG_KEY_{index}"] = key
        controlled[f"GIT_CONFIG_VALUE_{index}"] = value
    return controlled


def _is_reparse(path: Path) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    stat = path.lstat()
    return path.is_symlink() or bool(
        getattr(stat, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _require_owned_path(path: Path, root: Path) -> None:
    try:
        path.resolve(strict=False).relative_to(root)
    except (OSError, ValueError) as exc:
        raise GitEnvironmentError(
            f"controller-owned Git path escapes its registered root: {path}"
        ) from exc
    current = path
    while current != root.parent and current.exists():
        if _is_reparse(current):
            raise GitEnvironmentError(
                f"controller-owned Git path contains a reparse point: {current}"
            )
        if current == root:
            break
        current = current.parent


def _prepare_empty_file(path: Path, root: Path) -> None:
    _require_owned_path(path, root)
    if path.exists():
        if not path.is_file() or path.stat().st_size != 0:
            raise GitEnvironmentError(f"registered Git isolation file is not empty: {path}")
        return
    path.touch(exist_ok=False)


def _prepare_empty_directory(path: Path, root: Path) -> None:
    _require_owned_path(path, root)
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise GitEnvironmentError(
                f"registered Git isolation directory is not empty: {path}"
            )
        return
    path.mkdir()


def _require_empty_file(path: Path, root: Path) -> None:
    _require_owned_path(path, root)
    try:
        if not path.exists():
            raise GitEnvironmentError(f"registered Git isolation file is missing: {path}")
        if not path.is_file() or path.stat().st_size != 0:
            raise GitEnvironmentError(f"registered Git isolation file is not empty: {path}")
    except GitEnvironmentError:
        raise
    except OSError as exc:
        raise GitEnvironmentError(
            f"unable to validate registered Git isolation file {path}: {exc}"
        ) from exc


def _require_empty_directory(path: Path, root: Path) -> None:
    _require_owned_path(path, root)
    try:
        if not path.exists():
            raise GitEnvironmentError(f"registered Git isolation directory is missing: {path}")
        if not path.is_dir() or any(path.iterdir()):
            raise GitEnvironmentError(
                f"registered Git isolation directory is not empty: {path}"
            )
    except GitEnvironmentError:
        raise
    except OSError as exc:
        raise GitEnvironmentError(
            f"unable to validate registered Git isolation directory {path}: {exc}"
        ) from exc


def _canonical_digest(value: Mapping[str, str]) -> str:
    payload = json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def validate_git_environment(bundle: GitEnvironment) -> GitEnvironment:
    """Fail closed if a prepared bundle was altered or controller paths drifted."""

    if not isinstance(bundle, GitEnvironment):
        raise GitEnvironmentError("worker Git environment must be controller-prepared")
    if not isinstance(bundle.root, Path):
        raise GitEnvironmentError("controller-owned Git root path has an invalid type")
    if not isinstance(bundle.environment, Mapping):
        raise GitEnvironmentError("controller-owned Git process environment is not a mapping")
    try:
        root = bundle.root.resolve(strict=True)
        if not root.is_dir() or _is_reparse(root):
            raise GitEnvironmentError("controller-owned Git root is no longer a real directory")
    except GitEnvironmentError:
        raise
    except OSError as exc:
        raise GitEnvironmentError(
            f"controller-owned Git root is missing or unreadable: {bundle.root}"
        ) from exc
    expected_paths = {
        "global_config": root / "empty-global.gitconfig",
        "global_attributes": root / "empty-global.gitattributes",
        "template_directory": root / "empty-template",
        "hooks_directory": root / "empty-hooks",
    }
    try:
        actual_names = {entry.name for entry in root.iterdir()}
    except OSError as exc:
        raise GitEnvironmentError(
            f"controller-owned Git root contents are unavailable: {exc}"
        ) from exc
    expected_names = {path.name for path in expected_paths.values()}
    missing_names = expected_names - actual_names
    if missing_names:
        raise GitEnvironmentError(
            "controller-owned Git evidence is missing or unreadable: "
            + ", ".join(sorted(missing_names))
        )
    if actual_names - expected_names:
        raise GitEnvironmentError(
            "controller-owned Git root contains ambiguous evidence"
        )
    for field, expected in expected_paths.items():
        candidate = getattr(bundle, field)
        if not isinstance(candidate, Path):
            raise GitEnvironmentError(f"controller-owned Git {field} path has an invalid type")
        try:
            actual = candidate.resolve(strict=True)
        except OSError as exc:
            raise GitEnvironmentError(
                f"controller-owned Git {field} path is missing or unreadable"
            ) from exc
        if actual != expected:
            raise GitEnvironmentError(f"controller-owned Git {field} path is not registered")
    _require_empty_file(bundle.global_config, root)
    _require_empty_file(bundle.global_attributes, root)
    _require_empty_directory(bundle.template_directory, root)
    _require_empty_directory(bundle.hooks_directory, root)
    expected = _controlled_environment(
        bundle.global_config, bundle.global_attributes,
        bundle.template_directory, bundle.hooks_directory,
    )
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in bundle.environment.items()
    ):
        raise GitEnvironmentError(
            "controller-owned Git process environment contains non-text data"
        )
    try:
        actual_git = {
            key: value for key, value in bundle.environment.items()
            if isinstance(key, str) and key.upper().startswith("GIT_")
        }
    except (AttributeError, TypeError, ValueError) as exc:
        raise GitEnvironmentError(
            "controller-owned Git process environment cannot be inspected"
        ) from exc
    if actual_git != expected:
        raise GitEnvironmentError("controller-owned Git environment was altered or extended")
    if not isinstance(bundle.digest, str) or bundle.digest != _canonical_digest(expected):
        raise GitEnvironmentError("controller-owned Git environment digest is stale")
    return bundle


def prepare_git_environment(
    controller_root: Path,
    *,
    base_environment: Mapping[str, str] | None = None,
) -> GitEnvironment:
    """Create and return a sanitized environment without changing ``HOME``.

    Caller-provided ``GIT_*`` variables are removed case-insensitively. Git then sees
    only empty system/global configuration surfaces and fixed controller settings.
    The returned digest covers those Git-specific settings, not unrelated inherited
    values such as credentials or ``PATH``.
    """

    raw_root = Path(controller_root)
    if not raw_root.is_absolute():
        raise GitEnvironmentError("controller-owned Git root must be absolute")
    raw_root.mkdir(parents=True, exist_ok=True)
    root = raw_root.resolve(strict=True)
    if not root.is_dir() or _is_reparse(root):
        raise GitEnvironmentError("controller-owned Git root must be a real directory")

    global_config = root / "empty-global.gitconfig"
    global_attributes = root / "empty-global.gitattributes"
    template_directory = root / "empty-template"
    hooks_directory = root / "empty-hooks"
    _prepare_empty_file(global_config, root)
    _prepare_empty_file(global_attributes, root)
    _prepare_empty_directory(template_directory, root)
    _prepare_empty_directory(hooks_directory, root)

    source = os.environ if base_environment is None else base_environment
    environment: dict[str, str] = {}
    for key, value in source.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise GitEnvironmentError("process environment keys and values must be text")
        if key.upper().startswith("GIT_"):
            continue
        environment[key] = value

    controlled = _controlled_environment(
        global_config, global_attributes, template_directory, hooks_directory
    )
    environment.update(controlled)

    return GitEnvironment(
        environment=MappingProxyType(environment),
        digest=_canonical_digest(controlled),
        root=root,
        global_config=global_config,
        global_attributes=global_attributes,
        template_directory=template_directory,
        hooks_directory=hooks_directory,
    )


def load_git_environment(
    controller_root: Path,
    *,
    base_environment: Mapping[str, str] | None = None,
) -> GitEnvironment:
    """Load an already-prepared isolation bundle without creating or repairing it."""

    raw_root = Path(controller_root)
    if not raw_root.is_absolute():
        raise GitEnvironmentError("controller-owned Git root must be absolute")
    try:
        root = raw_root.resolve(strict=True)
    except OSError as exc:
        raise GitEnvironmentError("controller-owned Git root is missing") from exc
    global_config = root / "empty-global.gitconfig"
    global_attributes = root / "empty-global.gitattributes"
    template_directory = root / "empty-template"
    hooks_directory = root / "empty-hooks"
    source = os.environ if base_environment is None else base_environment
    environment: dict[str, str] = {}
    for key, value in source.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise GitEnvironmentError("process environment keys and values must be text")
        if not key.upper().startswith("GIT_"):
            environment[key] = value
    controlled = _controlled_environment(
        global_config, global_attributes, template_directory, hooks_directory
    )
    environment.update(controlled)
    bundle = GitEnvironment(
        environment=MappingProxyType(environment), digest=_canonical_digest(controlled),
        root=root, global_config=global_config, global_attributes=global_attributes,
        template_directory=template_directory, hooks_directory=hooks_directory,
    )
    return validate_git_environment(bundle)


__all__ = [
    "GitEnvironment", "GitEnvironmentError", "load_git_environment", "prepare_git_environment",
    "validate_git_environment",
]
