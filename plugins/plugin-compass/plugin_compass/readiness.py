"""Static, capability-local checks of explicit plugin-root file references.

This is not dependency resolution, a security scanner, or execution verification.
Only file existence inside the declared root is inspected. Skill text stays data.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import EvidenceRecord, ExecutionReadiness


MAX_REFERENCES = 64
ROOT_REFERENCE = re.compile(
    r"<(?:plugin-dir|plugin-root|plugin-install-path|[a-z0-9-]+-plugin-root)>[/\\]"
    r"((?:[A-Za-z0-9_.-]+[/\\])*[A-Za-z0-9_.-]+\.(?:py|sh|cjs|mjs|js|ps1|cmd|bat|exe|template))\b",
    re.IGNORECASE,
)


def inspect_readiness(
    root: Path, skill_path: Path, text: str,
) -> tuple[ExecutionReadiness, tuple[EvidenceRecord, ...]]:
    references = sorted({match.group(1).replace("\\", "/") for match in ROOT_REFERENCE.finditer(text)})
    if not references:
        return ExecutionReadiness("not_declared", str(root)), ()
    evidence: list[EvidenceRecord] = []
    statuses: list[str] = []
    if len(references) > MAX_REFERENCES:
        statuses.append("unknown")
        evidence.append(EvidenceRecord.create(
            "runtime-file", str(skill_path), skill_path.parent.name,
            "Local runtime reference limit exceeded; remaining references were not inspected.",
            status="unknown", target_root=str(root),
        ))
    for reference in references[:MAX_REFERENCES]:
        status = "unknown"
        try:
            if ".." in Path(reference).parts:
                raise ValueError("parent traversal is not a local runtime reference")
            candidate = root / reference
            candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
            parents = (candidate, *candidate.parents)
            if not any(
                path.is_symlink() for path in parents if path != root and root in path.parents
            ):
                status = "present" if candidate.is_file() else "missing"
        except (OSError, ValueError, RuntimeError):
            pass
        statuses.append(status)
        evidence.append(EvidenceRecord.create(
            "runtime-file", str(skill_path), reference,
            f"Plugin-root file reference is {status}. Existence only; alternatives, dependencies and execution are unverified.",
            status=status, target_root=str(root),
        ))
    status = "missing_files" if "missing" in statuses else "unknown" if "unknown" in statuses else "files_present"
    return ExecutionReadiness(status, str(root), tuple(references[:MAX_REFERENCES])), tuple(evidence)
