"""HOL plugin-scanner JSON adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import EvidenceRecord, FindingRecord, sorted_unique


class HolEvidenceError(RuntimeError):
    """Raised when a HOL scanner report cannot be read or normalized."""


def parse_report(
    payload: dict[str, Any],
    *,
    source: str,
    base_dir: Path | None = None,
) -> tuple[tuple[FindingRecord, ...], tuple[EvidenceRecord, ...]]:
    schema = str(payload.get("schema_version") or "unknown")
    tool_version = str(payload.get("tool_version") or "").strip() or None
    plugin_dir = str(payload.get("pluginDir") or "").strip() or None
    if plugin_dir and base_dir is not None and not plugin_dir.startswith("/"):
        candidate = Path(plugin_dir).expanduser()
        if not candidate.is_absolute():
            plugin_dir = str((base_dir / candidate).resolve(strict=False))
    target_names: list[str] = []
    packages = payload.get("packages")
    if isinstance(packages, list):
        for package in packages:
            if isinstance(package, dict) and package.get("name"):
                target_names.append(str(package["name"]))
    policy_pass = payload.get("policy_pass") is True
    verify_pass = payload.get("verify_pass") is True
    status = "reviewed" if policy_pass and verify_pass else "blocked"
    report_evidence: list[EvidenceRecord] = []
    subjects = sorted_unique(target_names) or ("unknown-plugin",)
    for subject in subjects:
        report_evidence.append(
            EvidenceRecord.create(
                "hol-report",
                source,
                subject,
                (
                    f"HOL report schema={schema}; policy_pass={str(policy_pass).lower()}; "
                    f"verify_pass={str(verify_pass).lower()}; target={plugin_dir or 'unknown'}."
                ),
                status=status,
                source_version=tool_version,
                target_root=plugin_dir,
            )
        )

    findings: list[FindingRecord] = []
    raw_findings = payload.get("findings")
    if isinstance(raw_findings, list):
        for raw in raw_findings:
            if not isinstance(raw, dict):
                continue
            check_id = str(raw.get("ruleId") or raw.get("check_id") or "unknown-check")
            severity = str(raw.get("severity") or "unknown")
            title = str(raw.get("title") or "").strip()
            description = str(raw.get("description") or "").strip()
            message = ": ".join(item for item in (title, description) if item)
            path_value = raw.get("filePath")
            remediation = raw.get("remediation")
            findings.append(
                FindingRecord.create(
                    source_tool="hol-plugin-scanner",
                    source_version=tool_version,
                    check_id=check_id,
                    severity=severity,
                    message=message,
                    paths=(str(path_value),) if path_value else (),
                    fix_commands=(str(remediation),) if remediation else (),
                    target_plugin_ids=subjects,
                    target_root=plugin_dir,
                    evidence_refs=(item.evidence_id for item in report_evidence),
                )
            )
    return (
        tuple(sorted({item.finding_id: item for item in findings}.values(), key=lambda item: item.finding_id)),
        tuple(sorted(report_evidence, key=lambda item: item.evidence_id)),
    )


def load_report(
    path: Path,
) -> tuple[tuple[FindingRecord, ...], tuple[EvidenceRecord, ...]]:
    resolved = path.expanduser().resolve(strict=False)
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HolEvidenceError(f"Unable to parse HOL report: {resolved}") from exc
    if not isinstance(payload, dict):
        raise HolEvidenceError(f"HOL report root must be an object: {resolved}")
    return parse_report(payload, source=str(resolved), base_dir=resolved.parent)
