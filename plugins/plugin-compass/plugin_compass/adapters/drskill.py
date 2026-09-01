"""DrSkill 0.7.x JSONL finding adapter."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..models import EvidenceRecord, FindingRecord


class DrSkillEvidenceError(RuntimeError):
    """Raised when DrSkill finding evidence cannot be collected or parsed."""


def parse_jsonl(
    text: str,
    *,
    source: str,
    source_version: str | None = None,
) -> tuple[tuple[FindingRecord, ...], tuple[EvidenceRecord, ...]]:
    findings: list[FindingRecord] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload: Any = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DrSkillEvidenceError(
                f"DrSkill JSONL is invalid at {source}:{line_number}"
            ) from exc
        if not isinstance(payload, dict):
            raise DrSkillEvidenceError(
                f"DrSkill JSONL item at {source}:{line_number} must be an object"
            )
        check_id = str(payload.get("check_id") or "unknown-check")
        severity = str(payload.get("severity") or "unknown")
        message = str(payload.get("message") or "")
        subjects = payload.get("contributor_names")
        paths = payload.get("contributors")
        fixes = payload.get("fix_commands")
        finding = FindingRecord.create(
            source_tool="drskill",
            source_version=source_version,
            check_id=check_id,
            severity=severity,
            message=message,
            subjects=(str(item) for item in subjects) if isinstance(subjects, list) else (),
            paths=(str(item) for item in paths) if isinstance(paths, list) else (),
            fix_commands=(str(item) for item in fixes) if isinstance(fixes, list) else (),
        )
        findings.append(finding)
    evidence = EvidenceRecord.create(
        "drskill-report",
        source,
        "Codex loadout findings",
        f"Parsed {len(findings)} DrSkill JSONL finding(s).",
        status="observed",
        source_version=source_version,
    )
    return (
        tuple(sorted({item.finding_id: item for item in findings}.values(), key=lambda item: item.finding_id)),
        (evidence,),
    )


def load_report(
    path: Path,
    *,
    source_version: str | None = None,
) -> tuple[tuple[FindingRecord, ...], tuple[EvidenceRecord, ...]]:
    resolved = path.expanduser().resolve(strict=False)
    try:
        text = resolved.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise DrSkillEvidenceError(f"Unable to read DrSkill report: {resolved}") from exc
    return parse_jsonl(text, source=str(resolved), source_version=source_version)


def _executable() -> str:
    found = shutil.which("drskill")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "drskill.exe"
    if candidate.is_file():
        return str(candidate)
    raise DrSkillEvidenceError("drskill executable was not found")


def collect_codex_scan(
    *,
    timeout_seconds: float = 120.0,
    source_version: str | None = None,
) -> tuple[tuple[FindingRecord, ...], tuple[EvidenceRecord, ...]]:
    try:
        completed = subprocess.run(
            [_executable(), "scan", "--harness", "codex", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DrSkillEvidenceError("Unable to collect DrSkill Codex scan") from exc
    if completed.returncode not in (0, 1):
        stderr = (completed.stderr or "").strip()
        raise DrSkillEvidenceError(
            f"DrSkill scan exited {completed.returncode}: {stderr[:300]}"
        )
    return parse_jsonl(
        completed.stdout,
        source="command:drskill scan --harness codex --json",
        source_version=source_version,
    )
