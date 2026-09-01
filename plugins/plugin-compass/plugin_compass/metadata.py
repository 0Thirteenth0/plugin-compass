"""Bounded local metadata extraction without executing plugin content."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from .models import CapabilityRecord, EvidenceRecord, PluginRecord, sorted_unique
from .readiness import inspect_readiness


MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_SKILL_BYTES = 128 * 1024
MAX_SKILLS = 2_000


def _read_json(path: Path) -> Any:
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("metadata file exceeds read limit")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _frontmatter(path: Path) -> tuple[str, str, str]:
    if path.stat().st_size > MAX_SKILL_BYTES:
        return "", "", ""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "", "", ""
    values: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line or line[:1].isspace():
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values.get("name", ""), values.get("description", ""), text


def _platforms(manifest: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    candidates: list[Any] = [manifest.get("platforms")]
    compatibility = manifest.get("compatibility")
    if isinstance(compatibility, dict):
        candidates.append(compatibility.get("platforms"))
    interface = manifest.get("interface")
    if isinstance(interface, dict):
        candidates.append(interface.get("platforms"))
    for candidate in candidates:
        if isinstance(candidate, str):
            values.append(candidate.casefold())
        elif isinstance(candidate, list):
            values.extend(str(item).casefold() for item in candidate if isinstance(item, str))
    return sorted_unique(values)


def _safe_skill_paths(root: Path, skills_root: Path) -> Iterable[Path]:
    root_resolved = root.resolve(strict=False)
    count = 0
    for path in sorted(skills_root.rglob("SKILL.md"), key=lambda item: str(item).casefold()):
        if count >= MAX_SKILLS:
            break
        try:
            path.resolve(strict=False).relative_to(root_resolved)
        except ValueError:
            continue
        if path.is_symlink():
            continue
        count += 1
        yield path


def enrich_plugin(record: PluginRecord) -> PluginRecord:
    if not record.source_root:
        return record
    root = Path(record.source_root).expanduser().resolve(strict=False)
    manifest_path = root / ".codex-plugin" / "plugin.json"
    evidence = list(record.evidence)
    if not manifest_path.is_file():
        evidence.append(
            EvidenceRecord.create(
                "manifest-missing",
                str(manifest_path),
                record.plugin_id,
                "Declared local root has no .codex-plugin/plugin.json.",
                status="unknown",
            )
        )
        return replace(
            record,
            manifest_path=str(manifest_path),
            metadata_status="missing",
            evidence=tuple(evidence),
        )
    try:
        manifest = _read_json(manifest_path)
        if not isinstance(manifest, dict):
            raise ValueError("manifest root is not an object")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        evidence.append(
            EvidenceRecord.create(
                "manifest-malformed",
                str(manifest_path),
                record.plugin_id,
                f"Manifest could not be parsed: {type(exc).__name__}.",
                status="unknown",
            )
        )
        return replace(
            record,
            manifest_path=str(manifest_path),
            metadata_status="malformed",
            evidence=tuple(evidence),
        )

    manifest_name = str(manifest.get("name") or "").strip()
    description = str(manifest.get("description") or record.description).strip()
    metadata_status = "complete" if manifest_name and description else "partial"
    manifest_evidence = EvidenceRecord.create(
        "plugin-manifest",
        str(manifest_path),
        record.plugin_id,
        f"Codex plugin manifest parsed with metadata status {metadata_status}.",
        status=metadata_status,
    )
    evidence.append(manifest_evidence)

    skills_root = root / "skills"
    declared_skills = manifest.get("skills")
    if isinstance(declared_skills, str) and declared_skills.strip():
        candidate = (root / declared_skills).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError:
            candidate = root / "skills"
        skills_root = candidate

    capabilities: list[CapabilityRecord] = []
    if skills_root.is_dir():
        for skill_path in _safe_skill_paths(root, skills_root):
            try:
                name, skill_description, skill_text = _frontmatter(skill_path)
            except OSError:
                name, skill_description, skill_text = "", "", ""
            if not name:
                evidence.append(
                    EvidenceRecord.create(
                        "skill-metadata-missing",
                        str(skill_path.resolve(strict=False)),
                        record.plugin_id,
                        "SKILL.md has no readable top-level name field.",
                        status="unknown",
                    )
                )
                continue
            skill_evidence = EvidenceRecord.create(
                "skill-frontmatter",
                str(skill_path.resolve(strict=False)),
                name,
                skill_description or "Skill description is missing.",
                status="observed" if skill_description else "unknown",
            )
            evidence.append(skill_evidence)
            readiness, readiness_evidence = inspect_readiness(root, skill_path, skill_text)
            evidence.extend(readiness_evidence)
            capabilities.append(
                replace(CapabilityRecord.create(
                    record.plugin_id,
                    name,
                    skill_description,
                    str(skill_path.resolve(strict=False)),
                    (skill_evidence.evidence_id, *(item.evidence_id for item in readiness_evidence)),
                ), readiness=readiness)
            )
    if not capabilities and description:
        capabilities.append(
            CapabilityRecord.create(
                record.plugin_id,
                manifest_name or record.name,
                description,
                str(manifest_path),
                (manifest_evidence.evidence_id,),
            )
        )

    version_value = manifest.get("version")
    version = str(version_value).strip() if version_value not in (None, "") else record.version
    return replace(
        record,
        version=version,
        manifest_path=str(manifest_path),
        description=description,
        capabilities=tuple(capabilities),
        evidence=tuple({item.evidence_id: item for item in evidence}.values()),
        metadata_status=metadata_status,
        platforms=_platforms(manifest),
        has_hooks=(root / "hooks").is_dir() or (root / "hooks.json").is_file(),
        has_mcp=(root / ".mcp.json").is_file(),
        has_app=(root / ".app.json").is_file(),
    )


def enrich_plugins(records: Iterable[PluginRecord]) -> tuple[PluginRecord, ...]:
    return tuple(
        sorted((enrich_plugin(item) for item in records), key=lambda item: item.plugin_id.casefold())
    )
