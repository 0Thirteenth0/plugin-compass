"""Bounded repository authority and structural context discovery."""

from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path

from .models import EvidenceRecord, RepositoryContext, sorted_unique


MAX_FILES = 5_000
MAX_AUTHORITY_BYTES = 256 * 1024

SKIP_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "__pycache__",
}

SECRET_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "auth.json",
    "credentials.json",
    "id_ed25519",
    "id_rsa",
}

AUTHORITY_NAMES = {
    "agents.md",
    "agents.override.md",
    "architecture.md",
    "contract.md",
    "governance.md",
    "policy.md",
    "product_contract.md",
    "security.md",
}

LANGUAGE_BY_EXTENSION = {
    ".c": "C",
    ".cpp": "C++",
    ".cs": "C#",
    ".go": "Go",
    ".java": "Java",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".kt": "Kotlin",
    ".mjs": "JavaScript",
    ".php": "PHP",
    ".ps1": "PowerShell",
    ".py": "Python",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".sh": "Shell",
    ".swift": "Swift",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
}

PROHIBITION_PATTERN = re.compile(
    r"(?:do\s+not\s+use|must\s+not\s+use|prohibit(?:ed|s)?|disallow(?:ed|s)?)"
    r"\s+(?:the\s+)?(?:plugin\s+)?[`\"']?"
    r"([a-z0-9](?:[a-z0-9_.@-]*[a-z0-9])?)",
    re.I,
)


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root).as_posix()
    except ValueError:
        return str(path.resolve(strict=False))


def _authority_candidates(root: Path) -> tuple[Path, ...]:
    candidates: set[Path] = set()
    for directory in (root, root / "docs"):
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_file() and path.name.casefold() in AUTHORITY_NAMES:
                candidates.add(path)
    return tuple(sorted(candidates, key=lambda item: str(item).casefold()))


def _structural_inventory(root: Path) -> tuple[int, tuple[str, ...]]:
    count = 0
    languages: Counter[str] = Counter()
    for current, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = sorted(
            [name for name in directories if name not in SKIP_DIRECTORIES],
            key=str.casefold,
        )
        for filename in sorted(filenames, key=str.casefold):
            folded = filename.casefold()
            if folded in SECRET_FILENAMES or folded.startswith(".env."):
                continue
            path = Path(current) / filename
            if path.is_symlink():
                continue
            count += 1
            language = LANGUAGE_BY_EXTENSION.get(path.suffix.casefold())
            if language:
                languages[language] += 1
            if count >= MAX_FILES:
                ordered = tuple(
                    name for name, _ in sorted(languages.items(), key=lambda item: (-item[1], item[0]))
                )
                return count, ordered
    ordered = tuple(
        name for name, _ in sorted(languages.items(), key=lambda item: (-item[1], item[0]))
    )
    return count, ordered


def inspect_repository(path: Path) -> RepositoryContext:
    root = path.expanduser().resolve(strict=False)
    if not root.is_dir():
        evidence = EvidenceRecord.create(
            "repository-missing",
            str(root),
            "repository context",
            "Repository path does not exist or is not a directory.",
            status="unknown",
        )
        return RepositoryContext(root=str(root), exists=False, empty=True, evidence=(evidence,))

    file_count, languages = _structural_inventory(root)
    authority = _authority_candidates(root)
    prohibited: list[str] = []
    evidence: list[EvidenceRecord] = []
    for authority_file in authority:
        relative = _relative(root, authority_file)
        try:
            if authority_file.stat().st_size > MAX_AUTHORITY_BYTES:
                evidence.append(
                    EvidenceRecord.create(
                        "authority-file",
                        str(authority_file.resolve(strict=False)),
                        relative,
                        "Authority file exceeds the bounded read limit.",
                        status="unknown",
                    )
                )
                continue
            content = authority_file.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            evidence.append(
                EvidenceRecord.create(
                    "authority-file",
                    str(authority_file.resolve(strict=False)),
                    relative,
                    "Authority file could not be read.",
                    status="unknown",
                )
            )
            continue
        matches = [
            match.group(1).strip("`\"'").casefold()
            for match in PROHIBITION_PATTERN.finditer(content)
        ]
        prohibited.extend(matches)
        evidence.append(
            EvidenceRecord.create(
                "authority-file",
                str(authority_file.resolve(strict=False)),
                relative,
                f"Authority file inspected; explicit plugin prohibitions={len(matches)}.",
            )
        )
    if not authority:
        evidence.append(
            EvidenceRecord.create(
                "authority-absence",
                str(root),
                "repository authority",
                "No recognized root or docs authority file was found.",
                status="unknown",
            )
        )
    evidence.append(
        EvidenceRecord.create(
            "repository-structure",
            str(root),
            "repository context",
            f"Bounded structural inventory observed files={file_count}; languages={len(languages)}.",
        )
    )
    return RepositoryContext(
        root=str(root),
        exists=True,
        empty=file_count == 0,
        authority_files=tuple(_relative(root, item) for item in authority),
        languages=languages,
        prohibited_plugins=sorted_unique(prohibited),
        has_authority_system=bool(authority),
        evidence=tuple(evidence),
    )
