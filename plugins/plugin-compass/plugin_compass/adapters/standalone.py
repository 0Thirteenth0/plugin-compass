"""Discovery for explicitly configured standalone Codex skill roots.

Codex plugin inventory does not enumerate standalone skills. This adapter therefore
accepts only caller-supplied roots and never guesses roots from plugin caches or turns a
standalone skill into a plugin record. Skill documents are treated as inert text.
"""

from __future__ import annotations

import os
import re
import stat
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from ..skill_models import SkillRecord, StandaloneDiscoverySummary


STANDALONE_SOURCE_TYPES = (
    "standalone-user",
    "standalone-project",
    "system",
)
DEFAULT_MAX_SKILL_BYTES = 128 * 1024
DEFAULT_MAX_DEPTH = 8
DEFAULT_MAX_SKILLS = 2_000
DEFAULT_MAX_RUNTIME_SECONDS = 5.0
DEFAULT_MAX_ROOTS = 64
DEFAULT_MAX_DIRECTORY_ENTRIES = 20_000
DEFAULT_MAX_READINESS_REFERENCES = 64

SKILL_DIR_REFERENCE = re.compile(
    r"(?:\$\{?SKILL_DIR\}?|%SKILL_DIR%|<(?:skill-dir|skill-root)>)[/\\]"
    r"([A-Za-z0-9_./:\\-]+)",
    re.IGNORECASE,
)
READINESS_SUFFIXES = {
    ".py", ".sh", ".cjs", ".mjs", ".js", ".ps1", ".cmd", ".bat", ".exe", ".template",
}
FRONTMATTER_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
FRONTMATTER_NON_STRING = re.compile(
    r"^[+-]?(?:(?:0x[0-9a-f_]+|0o[0-7_]+|0b[01_]+)|"
    r"(?:(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)"
    r"(?:e[+-]?\d[\d_]*)?|\.inf|\.nan))$",
    re.IGNORECASE,
)
FRONTMATTER_NON_STRING_WORDS = {
    "null", "~", "true", "false", "yes", "no", "on", "off",
}
FRONTMATTER_TIMESTAMP = re.compile(
    r"^\d{4}-\d{1,2}-\d{1,2}"
    r"(?:[Tt ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
    r"(?:[Zz]|[+-]\d{2}(?::?\d{2})?)?)?$"
)


class _RuntimeLimitReached(RuntimeError):
    """Internal control flow for a deadline observed after a bounded operation."""


class _UnsafePathAccess(OSError):
    """The named path and securely opened object do not share one safe identity."""


class _OversizedSkill(ValueError):
    """The securely opened skill exceeds its configured byte bound."""


@dataclass(frozen=True, slots=True)
class ConfiguredSkillRoot:
    path: Path
    source_type: str
    source_identity: str

    def __post_init__(self) -> None:
        if self.source_type not in STANDALONE_SOURCE_TYPES:
            raise ValueError(f"unsupported standalone source type: {self.source_type}")
        if not self.source_identity.strip():
            raise ValueError("standalone source identity must be non-empty")
        object.__setattr__(self, "path", Path(self.path).expanduser())
        object.__setattr__(self, "source_identity", self.source_identity.strip())


@dataclass(frozen=True, slots=True)
class StandaloneDiscoveryResult:
    skills: tuple[SkillRecord, ...]
    diagnostics: tuple["DiscoveryDiagnostic", ...] = ()

    @property
    def status(self) -> str:
        return "degraded" if self.diagnostics else "complete"

    def to_summary(self, *, configured: bool = True) -> StandaloneDiscoverySummary:
        return StandaloneDiscoverySummary.create(
            self.status if configured else "not_configured",
            (item.to_dict() for item in self.diagnostics) if configured else (),
        )

    def to_dict(self, *, configured: bool = True) -> dict[str, object]:
        return self.to_summary(configured=configured).to_dict()


@dataclass(frozen=True, slots=True)
class DiscoveryDiagnostic:
    code: str
    source_type: str
    source_identity: str
    path: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "source_type": self.source_type,
            "source_identity": self.source_identity,
            "path": self.path,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class DiscoveryLimits:
    max_skill_bytes: int = DEFAULT_MAX_SKILL_BYTES
    max_depth: int = DEFAULT_MAX_DEPTH
    max_skills: int = DEFAULT_MAX_SKILLS
    max_runtime_seconds: float = DEFAULT_MAX_RUNTIME_SECONDS
    max_roots: int = DEFAULT_MAX_ROOTS
    max_directory_entries: int = DEFAULT_MAX_DIRECTORY_ENTRIES
    max_readiness_references: int = DEFAULT_MAX_READINESS_REFERENCES

    def __post_init__(self) -> None:
        if self.max_skill_bytes < 1:
            raise ValueError("max_skill_bytes must be positive")
        if self.max_depth < 0:
            raise ValueError("max_depth must be non-negative")
        if self.max_skills < 1:
            raise ValueError("max_skills must be positive")
        if self.max_runtime_seconds <= 0:
            raise ValueError("max_runtime_seconds must be positive")
        if self.max_roots < 1:
            raise ValueError("max_roots must be positive")
        if self.max_directory_entries < 1:
            raise ValueError("max_directory_entries must be positive")
        if self.max_readiness_references < 1:
            raise ValueError("max_readiness_references must be positive")


def _portable_key(value: str) -> tuple[str, str, str]:
    normalized = value.replace("\\", "/")
    return normalized.casefold(), normalized, value


def _path_key(path: Path) -> tuple[str, ...]:
    name_key = _portable_key(path.name)
    path_key = _portable_key(str(path))
    return (*name_key, *path_key)


def _root_key(root: ConfiguredSkillRoot) -> tuple[str, ...]:
    return (
        *_portable_key(root.source_type),
        *_portable_key(root.source_identity),
        *_portable_key(str(root.path.absolute())),
    )


def _logical_path_identity(path: Path) -> str:
    """Compare paths using host filesystem case rules without following links."""
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _diagnostic(
    configured: ConfiguredSkillRoot,
    code: str,
    path: Path | str,
    detail: str,
) -> DiscoveryDiagnostic:
    return DiscoveryDiagnostic(
        code=code,
        source_type=configured.source_type,
        source_identity=configured.source_identity,
        path=str(path),
        detail=detail,
    )


def _runtime_expired(
    configured: ConfiguredSkillRoot,
    path: Path | str,
    detail: str,
    deadline: float,
    monotonic: Callable[[], float],
    diagnostics: list[DiscoveryDiagnostic],
) -> bool:
    if monotonic() < deadline:
        return False
    diagnostics.append(_diagnostic(configured, "runtime-limit", path, detail))
    return True


def _frontmatter_string(value: str) -> str | None:
    """Parse the supported flat YAML scalar-string subset without a YAML runtime."""
    candidate = value.strip()
    if not candidate:
        return ""
    if candidate[0] in {'"', "'"}:
        quote_character = candidate[0]
        if len(candidate) < 2 or candidate[-1] != quote_character:
            return None
        candidate = candidate[1:-1]
        if quote_character in candidate:
            return None
    else:
        # This is deliberately narrower than YAML plain-scalar syntax: an unquoted
        # string starts with a Unicode letter/digit or underscore, contains neither
        # YAML comment/mapping/collection delimiters, and is not a YAML-like typed
        # scalar. Anything richer must use the balanced-quote form above.
        if (
            not (candidate[0].isalnum() or candidate[0] == "_")
            or any(character in ":#[]{}" for character in candidate)
            or candidate.casefold() in FRONTMATTER_NON_STRING_WORDS
            or FRONTMATTER_NON_STRING.fullmatch(candidate)
            or FRONTMATTER_TIMESTAMP.fullmatch(candidate)
        ):
            return None
    if any(ord(character) < 32 and character != "\t" for character in candidate):
        return None
    return candidate


def _frontmatter(text: str) -> tuple[str, str, str]:
    """Read only flat key/string pairs; reject all other YAML-equivalent syntax."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "", "", "malformed"
    values: dict[str, str] = {}
    observed_keys: set[str] = set()
    closed = False
    for line in lines[1:]:
        if line.strip() == "---":
            closed = True
            break
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in line or line[:1].isspace():
            return "", "", "malformed"
        key, value = line.split(":", 1)
        raw_key = key.strip()
        canonical_key = raw_key.strip("'\"").casefold()
        if canonical_key in observed_keys:
            return "", "", "malformed"
        observed_keys.add(canonical_key)
        if not FRONTMATTER_KEY.fullmatch(raw_key):
            return "", "", "malformed"
        parsed_value = _frontmatter_string(value)
        if parsed_value is None:
            return "", "", "malformed"
        values[canonical_key] = parsed_value
    if not closed:
        return "", "", "malformed"
    name = values.get("name", "")
    description = values.get("description", "")
    return name, description, "complete" if name and description else "partial"


def _readiness(
    configured_root: Path,
    skill_root: Path,
    text: str,
    max_references: int,
    deadline: float,
    monotonic: Callable[[], float],
) -> tuple[str, tuple[str, ...], tuple[str, ...], bool, bool]:
    observed: set[str] = set()
    reference_limit_reached = False
    runtime_limit_reached = False
    for match in SKILL_DIR_REFERENCE.finditer(text):
        if monotonic() >= deadline:
            runtime_limit_reached = True
            break
        reference = match.group(1).replace("\\", "/").rstrip(".,;)")
        if PurePosixPath(reference).suffix.casefold() not in READINESS_SUFFIXES:
            continue
        if reference in observed:
            continue
        if len(observed) >= max_references:
            reference_limit_reached = True
            break
        observed.add(reference)
    references = tuple(sorted(observed, key=_portable_key))
    if not references:
        status = "unknown" if reference_limit_reached or runtime_limit_reached else "not_declared"
        return status, (), (), reference_limit_reached, runtime_limit_reached
    statuses: list[str] = []
    rejected: list[str] = []
    for reference in references:
        if monotonic() >= deadline:
            runtime_limit_reached = True
            statuses.append("unknown")
            break
        parts = PurePosixPath(reference).parts
        if (
            reference.startswith("/")
            or re.match(r"^[A-Za-z]:", reference)
            or ".." in parts
        ):
            rejected.append(reference)
            statuses.append("unknown")
            continue
        candidate = skill_root.joinpath(*parts)
        safe_candidate = _safe_resolved_path(skill_root, candidate)
        if monotonic() >= deadline:
            runtime_limit_reached = True
            statuses.append("unknown")
            break
        if safe_candidate is None:
            rejected.append(reference)
            statuses.append("unknown")
            continue
        try:
            safe_candidate.relative_to(configured_root)
        except ValueError:
            rejected.append(reference)
            statuses.append("unknown")
            continue
        reference_is_file = _regular_file_no_follow(safe_candidate, skill_root)
        if reference_is_file is None:
            rejected.append(reference)
            statuses.append("unknown")
            continue
        if monotonic() >= deadline:
            runtime_limit_reached = True
            statuses.append("unknown")
            break
        statuses.append("present" if reference_is_file else "missing")
    status = (
        "unknown" if "unknown" in statuses or reference_limit_reached or runtime_limit_reached
        else "missing_files" if "missing" in statuses
        else "files_present"
    )
    return (
        status,
        references,
        tuple(sorted(rejected, key=_portable_key)),
        reference_limit_reached,
        runtime_limit_reached,
    )


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    if os.name != "nt":
        return False
    try:
        path_status = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(path_status, "st_file_attributes", None)
    if attributes is None:
        raise OSError("Windows reparse-point classification is unavailable")
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _safe_resolved_path(root: Path, candidate: Path) -> Path | None:
    try:
        current = candidate
        while current != root:
            if _is_reparse_point(current):
                return None
            parent = current.parent
            if parent == current:
                return None
            current = parent
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved


def _file_identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _is_stat_reparse(value: os.stat_result) -> bool:
    return bool(
        getattr(value, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _open_regular_no_follow(path: Path, root: Path) -> tuple[int, tuple[int, int]]:
    if _safe_resolved_path(root, path) is None:
        raise _UnsafePathAccess("path is not safely contained")
    named = os.stat(path, follow_symlinks=False)
    if _is_stat_reparse(named) or not stat.S_ISREG(named.st_mode):
        raise _UnsafePathAccess("path is not a non-reparse regular file")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _file_identity(opened) != _file_identity(named)
        ):
            raise _UnsafePathAccess("path identity changed before use")
        _verify_open_identity(path, root, _file_identity(opened))
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, _file_identity(opened)


def _verify_open_identity(
    path: Path,
    root: Path,
    expected_identity: tuple[int, int],
) -> None:
    named = os.stat(path, follow_symlinks=False)
    if (
        _is_stat_reparse(named)
        or not stat.S_ISREG(named.st_mode)
        or _file_identity(named) != expected_identity
        or _safe_resolved_path(root, path) is None
    ):
        raise _UnsafePathAccess("path identity or containment changed during use")


def _read_bounded_no_follow(path: Path, root: Path, max_bytes: int) -> bytes:
    descriptor, identity = _open_regular_no_follow(path, root)
    try:
        opened = os.fstat(descriptor)
        if opened.st_size > max_bytes:
            raise _OversizedSkill
        _verify_open_identity(path, root, identity)
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > max_bytes:
            raise _OversizedSkill
        _verify_open_identity(path, root, identity)
        return raw
    finally:
        os.close(descriptor)


def _regular_file_no_follow(path: Path, root: Path) -> bool | None:
    """Return regular-file presence, or None when safe identity is unavailable."""
    if _safe_resolved_path(root, path) is None:
        return None
    try:
        named = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    if _is_stat_reparse(named):
        return None
    if not stat.S_ISREG(named.st_mode):
        return False
    try:
        descriptor, identity = _open_regular_no_follow(path, root)
        try:
            _verify_open_identity(path, root, identity)
        finally:
            os.close(descriptor)
    except OSError:
        return None
    return True


def _bounded_skill_paths(
    root: Path,
    configured: ConfiguredSkillRoot,
    limits: DiscoveryLimits,
    diagnostics: list[DiscoveryDiagnostic],
    max_paths: int,
    max_entries: int,
    deadline: float,
    monotonic: Callable[[], float],
) -> tuple[tuple[Path, ...], bool, int]:
    pending = deque([(root, 0)])
    skills: list[Path] = []
    entries_visited = 0
    while pending:
        directory, depth = pending.popleft()
        if _runtime_expired(
            configured,
            root,
            "Discovery stopped because the runtime limit was reached.",
            deadline,
            monotonic,
            diagnostics,
        ):
            return (), True, entries_visited
        entries: list[Path] = []
        try:
            entry_iterator = iter(directory.iterdir())
        except OSError:
            diagnostics.append(DiscoveryDiagnostic(
                code="directory-unreadable",
                source_type=configured.source_type,
                source_identity=configured.source_identity,
                path=str(directory.absolute()),
                detail="Directory entries could not be enumerated.",
            ))
            continue
        while True:
            if _runtime_expired(
                configured,
                root,
                "Discovery stopped because the runtime limit was reached.",
                deadline,
                monotonic,
                diagnostics,
            ):
                return (), True, entries_visited
            try:
                entry = next(entry_iterator)
            except StopIteration:
                break
            except OSError:
                diagnostics.append(DiscoveryDiagnostic(
                    code="directory-unreadable",
                    source_type=configured.source_type,
                    source_identity=configured.source_identity,
                    path=str(directory.absolute()),
                    detail="Directory entries could not be enumerated.",
                ))
                entries = []
                break
            entries_visited += 1
            if entries_visited > max_entries:
                diagnostics.append(DiscoveryDiagnostic(
                    code="entry-limit",
                    source_type=configured.source_type,
                    source_identity=configured.source_identity,
                    path=str(root),
                    detail=(
                        "Root was discarded because the bounded directory entry "
                        "limit was exceeded."
                    ),
                ))
                return (), True, entries_visited
            entries.append(entry)
        for entry in sorted(entries, key=_path_key):
            try:
                is_reparse_point = _is_reparse_point(entry)
            except OSError:
                diagnostics.append(DiscoveryDiagnostic(
                    code="entry-unreadable",
                    source_type=configured.source_type,
                    source_identity=configured.source_identity,
                    path=str(entry.absolute()),
                    detail="Directory entry type could not be inspected.",
                ))
                continue
            if _runtime_expired(
                configured,
                root,
                "Discovery stopped after entry classification reached the runtime limit.",
                deadline,
                monotonic,
                diagnostics,
            ):
                return (), True, entries_visited
            if is_reparse_point:
                diagnostics.append(DiscoveryDiagnostic(
                    code="path-rejected",
                    source_type=configured.source_type,
                    source_identity=configured.source_identity,
                    path=str(entry.absolute()),
                    detail="Standalone skill traversal does not follow symlinks or junctions.",
                ))
                continue
            try:
                is_file = entry.is_file()
            except OSError:
                diagnostics.append(DiscoveryDiagnostic(
                    code="entry-unreadable",
                    source_type=configured.source_type,
                    source_identity=configured.source_identity,
                    path=str(entry.absolute()),
                    detail="Directory entry type could not be inspected.",
                ))
                continue
            if _runtime_expired(
                configured,
                root,
                "Discovery stopped after entry classification reached the runtime limit.",
                deadline,
                monotonic,
                diagnostics,
            ):
                return (), True, entries_visited
            is_dir = False
            if not is_file:
                try:
                    is_dir = entry.is_dir()
                except OSError:
                    diagnostics.append(DiscoveryDiagnostic(
                        code="entry-unreadable",
                        source_type=configured.source_type,
                        source_identity=configured.source_identity,
                        path=str(entry.absolute()),
                        detail="Directory entry type could not be inspected.",
                    ))
                    continue
                if _runtime_expired(
                    configured,
                    root,
                    "Discovery stopped after entry classification reached the runtime limit.",
                    deadline,
                    monotonic,
                    diagnostics,
                ):
                    return (), True, entries_visited
            if is_file and entry.name == "SKILL.md":
                if len(skills) >= max_paths:
                    diagnostics.append(DiscoveryDiagnostic(
                        code="skill-limit",
                        source_type=configured.source_type,
                        source_identity=configured.source_identity,
                        path=str(entry.absolute()),
                        detail="Discovery stopped because the global skill count limit was reached.",
                    ))
                    return tuple(skills), True, entries_visited
                skills.append(entry)
            elif is_dir:
                if depth >= limits.max_depth:
                    diagnostics.append(DiscoveryDiagnostic(
                        code="depth-limit",
                        source_type=configured.source_type,
                        source_identity=configured.source_identity,
                        path=str(entry.absolute()),
                        detail="Directory was not traversed because the depth limit was reached.",
                    ))
                else:
                    pending.append((entry, depth + 1))
    return tuple(skills), False, entries_visited


@dataclass(frozen=True, slots=True)
class _SkillInspection:
    record: SkillRecord | None
    diagnostics: tuple[DiscoveryDiagnostic, ...] = ()
    stop: bool = False


def _inspect_skill(
    root: Path,
    configured: ConfiguredSkillRoot,
    skill_path: Path,
    limits: DiscoveryLimits,
    deadline: float,
    monotonic: Callable[[], float],
) -> _SkillInspection:
    diagnostics: list[DiscoveryDiagnostic] = []
    if monotonic() >= deadline:
        diagnostics.append(_diagnostic(
            configured,
            "runtime-limit",
            root,
            "SKILL.md was not read because the runtime limit was reached.",
        ))
        return _SkillInspection(None, tuple(diagnostics), stop=True)

    resolved_skill_path = _safe_resolved_path(root, skill_path)
    if monotonic() >= deadline:
        diagnostics.append(_diagnostic(
            configured,
            "runtime-limit",
            root,
            "Discovery stopped after path inspection reached the runtime limit.",
        ))
        return _SkillInspection(None, tuple(diagnostics), stop=True)
    if resolved_skill_path is None:
        diagnostics.append(_diagnostic(
            configured,
            "path-rejected",
            skill_path.absolute(),
            "SKILL.md is reached through a symlink, junction, or path escape.",
        ))
        return _SkillInspection(None, tuple(diagnostics))

    try:
        raw = _read_bounded_no_follow(
            skill_path, root, limits.max_skill_bytes,
        )
        if monotonic() >= deadline:
            raise _RuntimeLimitReached
        text = raw.decode("utf-8-sig")
        if monotonic() >= deadline:
            raise _RuntimeLimitReached
    except _RuntimeLimitReached:
        diagnostics.append(_diagnostic(
            configured,
            "runtime-limit",
            root,
            "Discovery stopped after SKILL.md I/O reached the runtime limit.",
        ))
        return _SkillInspection(None, tuple(diagnostics), stop=True)
    except _UnsafePathAccess:
        name, description, metadata_status = "", "", "unreadable"
        readiness_status, readiness_references = "unknown", ()
        diagnostics.append(_diagnostic(
            configured,
            "path-rejected",
            resolved_skill_path,
            "SKILL.md identity or containment changed during its bounded read.",
        ))
    except UnicodeDecodeError:
        name, description, metadata_status = "", "", "malformed"
        readiness_status, readiness_references = "unknown", ()
        diagnostics.append(_diagnostic(
            configured,
            "skill-invalid-encoding",
            resolved_skill_path,
            "SKILL.md is not valid UTF-8 and was not parsed.",
        ))
    except _OversizedSkill:
        name, description, metadata_status = "", "", "oversized"
        readiness_status, readiness_references = "unknown", ()
        diagnostics.append(_diagnostic(
            configured,
            "skill-oversized",
            resolved_skill_path,
            "SKILL.md exceeds the configured metadata byte limit and was not parsed.",
        ))
    except OSError:
        name, description, metadata_status = "", "", "unreadable"
        readiness_status, readiness_references = "unknown", ()
        diagnostics.append(_diagnostic(
            configured,
            "skill-unreadable",
            resolved_skill_path,
            "SKILL.md could not be read and was not parsed.",
        ))
    else:
        name, description, metadata_status = _frontmatter(text)
        (
            readiness_status,
            readiness_references,
            rejected_references,
            readiness_limit_reached,
            readiness_runtime_reached,
        ) = _readiness(
            root,
            resolved_skill_path.parent,
            text,
            limits.max_readiness_references,
            deadline,
            monotonic,
        )
        if rejected_references:
            diagnostics.append(_diagnostic(
                configured,
                "readiness-reference-rejected",
                resolved_skill_path,
                f"Rejected {len(rejected_references)} unsafe skill-local readiness reference(s).",
            ))
        if readiness_limit_reached:
            diagnostics.append(_diagnostic(
                configured,
                "readiness-reference-limit",
                resolved_skill_path,
                "Readiness references exceeded the bounded inspection limit.",
            ))
        if readiness_runtime_reached:
            diagnostics.append(_diagnostic(
                configured,
                "runtime-limit",
                root,
                "Readiness inspection stopped because the runtime limit was reached.",
            ))
            return _SkillInspection(None, tuple(diagnostics), stop=True)
        if metadata_status == "malformed":
            diagnostics.append(_diagnostic(
                configured,
                "skill-metadata-malformed",
                resolved_skill_path,
                "SKILL.md has no complete bounded frontmatter block.",
            ))
        elif metadata_status == "partial":
            diagnostics.append(_diagnostic(
                configured,
                "skill-metadata-incomplete",
                resolved_skill_path,
                "SKILL.md frontmatter requires both name and description.",
            ))

    record = SkillRecord.create(
        name=name or resolved_skill_path.parent.name,
        description=description,
        path=str(resolved_skill_path),
        relative_path=skill_path.relative_to(root).as_posix(),
        source_type=configured.source_type,
        source_identity=configured.source_identity,
        metadata_status=metadata_status,
        readiness_status=readiness_status,
        readiness_root=str(resolved_skill_path.parent),
        readiness_references=readiness_references,
    )
    return _SkillInspection(record, tuple(diagnostics))


def discover_standalone_skills(
    roots: Iterable[ConfiguredSkillRoot],
    *,
    limits: DiscoveryLimits = DiscoveryLimits(),
    monotonic: Callable[[], float] = time.monotonic,
) -> StandaloneDiscoveryResult:
    records: list[SkillRecord] = []
    diagnostics: list[DiscoveryDiagnostic] = []
    deadline = monotonic() + limits.max_runtime_seconds
    configured_roots: list[ConfiguredSkillRoot] = []
    root_iterator = iter(roots)
    while len(configured_roots) <= limits.max_roots:
        if monotonic() >= deadline:
            return StandaloneDiscoveryResult(skills=(), diagnostics=(DiscoveryDiagnostic(
                code="runtime-limit",
                source_type="discovery",
                source_identity="configured-roots",
                path="<configured-roots>",
                detail="Configured roots were not scanned because the runtime limit was reached.",
            ),))
        try:
            configured_roots.append(next(root_iterator))
        except StopIteration:
            break
    if len(configured_roots) > limits.max_roots:
        return StandaloneDiscoveryResult(skills=(), diagnostics=(DiscoveryDiagnostic(
            code="root-limit",
            source_type="discovery",
            source_identity="configured-roots",
            path="<configured-roots>",
            detail="Configured roots exceed the bounded discovery limit; none were scanned.",
        ),))
    ordered_roots = sorted(
        configured_roots,
        key=_root_key,
    )
    unique_roots: list[ConfiguredSkillRoot] = []
    roots_by_identity: dict[tuple[str, str], list[ConfiguredSkillRoot]] = {}
    for configured in ordered_roots:
        roots_by_identity.setdefault(
            (configured.source_type, configured.source_identity), [],
        ).append(configured)
    for configured_group in roots_by_identity.values():
        distinct_paths = {
            _logical_path_identity(item.path)
            for item in configured_group
        }
        if len(distinct_paths) > 1:
            for configured in configured_group:
                diagnostics.append(DiscoveryDiagnostic(
                    code="conflicting-root-identity",
                    source_type=configured.source_type,
                    source_identity=configured.source_identity,
                    path=str(configured.path.absolute()),
                    detail=(
                        "Logical skill root identity maps to conflicting paths; "
                        "none were scanned."
                    ),
                ))
            continue
        unique_roots.append(configured_group[0])
        for configured in configured_group[1:]:
            diagnostics.append(DiscoveryDiagnostic(
                code="duplicate-root",
                source_type=configured.source_type,
                source_identity=configured.source_identity,
                path=str(configured.path.absolute()),
                detail="Exact duplicate logical skill root was not scanned again.",
            ))
    directory_entries_visited = 0
    for configured in unique_roots:
        if monotonic() >= deadline:
            diagnostics.append(DiscoveryDiagnostic(
                code="runtime-limit",
                source_type=configured.source_type,
                source_identity=configured.source_identity,
                path=str(configured.path.absolute()),
                detail="Root was not scanned because the runtime limit was reached.",
            ))
            break
        try:
            root_is_reparse_point = _is_reparse_point(configured.path)
        except OSError:
            diagnostics.append(DiscoveryDiagnostic(
                code="root-unreadable",
                source_type=configured.source_type,
                source_identity=configured.source_identity,
                path=str(configured.path.absolute()),
                detail="Configured standalone skill root could not be inspected.",
            ))
            continue
        if monotonic() >= deadline:
            diagnostics.append(DiscoveryDiagnostic(
                code="runtime-limit",
                source_type=configured.source_type,
                source_identity=configured.source_identity,
                path=str(configured.path.absolute()),
                detail="Root inspection reached the runtime limit.",
            ))
            break
        if root_is_reparse_point:
            diagnostics.append(DiscoveryDiagnostic(
                code="path-rejected",
                source_type=configured.source_type,
                source_identity=configured.source_identity,
                path=str(configured.path.absolute()),
                detail="Configured standalone skill root is a symlink or junction.",
            ))
            continue
        try:
            root = configured.path.resolve(strict=False)
            if monotonic() >= deadline:
                raise _RuntimeLimitReached
            root_is_directory = root.is_dir()
            if monotonic() >= deadline:
                raise _RuntimeLimitReached
        except _RuntimeLimitReached:
            diagnostics.append(DiscoveryDiagnostic(
                code="runtime-limit",
                source_type=configured.source_type,
                source_identity=configured.source_identity,
                path=str(configured.path.absolute()),
                detail="Root inspection reached the runtime limit.",
            ))
            break
        except (OSError, RuntimeError):
            diagnostics.append(DiscoveryDiagnostic(
                code="root-unreadable",
                source_type=configured.source_type,
                source_identity=configured.source_identity,
                path=str(configured.path.absolute()),
                detail="Configured standalone skill root could not be inspected.",
            ))
            continue
        if not root_is_directory:
            diagnostics.append(DiscoveryDiagnostic(
                code="root-missing",
                source_type=configured.source_type,
                source_identity=configured.source_identity,
                path=str(root),
                detail="Configured optional standalone skill root is unavailable.",
            ))
            continue
        remaining = limits.max_skills - len(records)
        if remaining <= 0:
            diagnostics.append(DiscoveryDiagnostic(
                code="skill-limit",
                source_type=configured.source_type,
                source_identity=configured.source_identity,
                path=str(root),
                detail="Root was not scanned because the global skill count limit was reached.",
            ))
            break
        remaining_entries = limits.max_directory_entries - directory_entries_visited
        if remaining_entries <= 0:
            diagnostics.append(DiscoveryDiagnostic(
                code="entry-limit",
                source_type=configured.source_type,
                source_identity=configured.source_identity,
                path=str(root),
                detail="Root was not scanned because the directory entry limit was reached.",
            ))
            break
        skill_paths, limit_reached, visited = _bounded_skill_paths(
            root,
            configured,
            limits,
            diagnostics,
            remaining,
            remaining_entries,
            deadline,
            monotonic,
        )
        directory_entries_visited += visited
        record_ids = {record.skill_id for record in records}
        for skill_path in skill_paths:
            inspection = _inspect_skill(
                root,
                configured,
                skill_path,
                limits,
                deadline,
                monotonic,
            )
            diagnostics.extend(inspection.diagnostics)
            if inspection.stop:
                limit_reached = True
                break
            if inspection.record is None:
                continue
            if inspection.record.skill_id in record_ids:
                diagnostics.append(_diagnostic(
                    configured,
                    "duplicate-skill",
                    inspection.record.path,
                    "Duplicate qualified skill identity was not emitted.",
                ))
                continue
            records.append(inspection.record)
            record_ids.add(inspection.record.skill_id)
        if limit_reached:
            break
    return StandaloneDiscoveryResult(
        skills=tuple(sorted(records, key=lambda item: item.skill_id)),
        diagnostics=tuple(sorted(
            diagnostics,
            key=lambda item: (
                *_portable_key(item.code),
                *_portable_key(item.source_type),
                *_portable_key(item.source_identity),
                *_portable_key(item.path),
                *_portable_key(item.detail),
            ),
        )),
    )
