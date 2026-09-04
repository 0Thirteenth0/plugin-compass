"""Authoritative Codex plugin inventory adapter."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ..models import EvidenceRecord, PluginRecord


class CodexInventoryError(RuntimeError):
    """Raised when Codex inventory cannot be obtained or parsed."""


class CodexInventoryInconclusive(CodexInventoryError):
    """An empty authoritative response cannot establish that the user has no plugins."""

    code = "CODEX_INVENTORY_EMPTY"
    recovery = (
        "Ask Codex for approval to run only 'codex plugin list --json' outside the "
        "sandbox once. Save the approved result to a local JSON file and rerun with "
        "--inventory-file <path>. If approval is unavailable or the result is still "
        "empty, report discovery as inconclusive. Do not infer enabled state from "
        "plugin caches or run plugin management commands."
    )

    def __init__(self, source: str = "command:codex plugin list --json") -> None:
        self.source = source
        super().__init__(
            "The authoritative Codex inventory returned no entries. This may reflect an "
            "empty installation or restricted discovery; it is not proof that no "
            "plugins are installed."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "plugin-compass.diagnostic.v1",
            "status": "inconclusive",
            "code": self.code,
            "source": self.source,
            "message": str(self),
            "recovery": self.recovery,
        }


def _parse_payload(text: str, source: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CodexInventoryError(f"Codex inventory from {source} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise CodexInventoryError(f"Codex inventory from {source} must be a JSON object")
    if not any(field in payload for field in ("installed", "available")):
        raise CodexInventoryError("Codex inventory has no installed or available field")
    for field in ("installed", "available"):
        value = payload.get(field, [])
        if not isinstance(value, list):
            raise CodexInventoryError(f"Codex inventory field {field!r} must be an array")
    return payload


def _require_conclusive_inventory(payload: dict[str, Any], source: str) -> None:
    if not payload.get("installed") and not payload.get("available"):
        raise CodexInventoryInconclusive(source)


def load_inventory_file(path: Path) -> tuple[dict[str, Any], EvidenceRecord]:
    resolved = path.expanduser().resolve(strict=False)
    try:
        text = resolved.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise CodexInventoryError(f"Unable to read Codex inventory file: {resolved}") from exc
    payload = _parse_payload(text, str(resolved))
    _require_conclusive_inventory(payload, str(resolved))
    evidence = EvidenceRecord.create(
        "codex-inventory",
        str(resolved),
        "Codex plugin inventory",
        "Parsed installed and available plugin identity from a supplied JSON snapshot.",
    )
    return payload, evidence


def run_inventory(*, timeout_seconds: float = 20.0) -> tuple[dict[str, Any], EvidenceRecord]:
    try:
        completed = subprocess.run(
            ["codex", "plugin", "list", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexInventoryError("Unable to run 'codex plugin list --json'") from exc
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise CodexInventoryError(
            f"'codex plugin list --json' exited {completed.returncode}: {stderr[:300]}"
        )
    payload = _parse_payload(completed.stdout, "codex plugin list --json")
    _require_conclusive_inventory(payload, "command:codex plugin list --json")
    evidence = EvidenceRecord.create(
        "codex-inventory",
        "command:codex plugin list --json",
        "Codex plugin inventory",
        "Parsed installed and available plugin identity from the official Codex CLI.",
    )
    return payload, evidence


def _source_root(entry: dict[str, Any], *, base_dir: Path | None) -> str | None:
    source = entry.get("source")
    value: Any = None
    if isinstance(source, dict):
        value = source.get("path")
    elif isinstance(source, str):
        value = source
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute() and base_dir is not None:
        candidate = base_dir / candidate
    return str(candidate.resolve(strict=False))


def records_from_payload(
    payload: dict[str, Any],
    evidence: EvidenceRecord,
    *,
    base_dir: Path | None = None,
) -> tuple[PluginRecord, ...]:
    entries: list[dict[str, Any]] = []
    for section in ("installed", "available"):
        for raw in payload.get(section, []):
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            if section == "available":
                item.setdefault("installed", False)
                item.setdefault("enabled", False)
            entries.append(item)

    deduplicated: dict[str, dict[str, Any]] = {}
    for entry in entries:
        name = str(entry.get("name") or "unknown-plugin").strip() or "unknown-plugin"
        marketplace = (
            str(entry.get("marketplaceName") or "unknown-marketplace").strip()
            or "unknown-marketplace"
        )
        plugin_id = str(entry.get("pluginId") or f"{name}@{marketplace}")
        entry["pluginId"] = plugin_id
        existing = deduplicated.get(plugin_id)
        if existing is None or bool(entry.get("installed", False)):
            deduplicated[plugin_id] = entry

    records: list[PluginRecord] = []
    for plugin_id, entry in deduplicated.items():
        name = str(entry.get("name") or "unknown-plugin").strip() or "unknown-plugin"
        marketplace = (
            str(entry.get("marketplaceName") or "unknown-marketplace").strip()
            or "unknown-marketplace"
        )
        version_value = entry.get("version")
        version = str(version_value).strip() if version_value not in (None, "") else None
        description = str(entry.get("description") or "").strip()
        records.append(
            PluginRecord(
                plugin_id=plugin_id,
                name=name,
                marketplace=marketplace,
                version=version,
                installed=bool(entry.get("installed", False)),
                enabled=bool(entry.get("enabled", False)),
                source_root=_source_root(entry, base_dir=base_dir),
                description=description,
                evidence=(evidence,),
                metadata_status="unknown",
            )
        )
    return tuple(sorted(records, key=lambda item: item.plugin_id.casefold()))


def discover_plugins(
    *,
    inventory_file: Path | None = None,
    timeout_seconds: float = 20.0,
) -> tuple[PluginRecord, ...]:
    if inventory_file is None:
        payload, evidence = run_inventory(timeout_seconds=timeout_seconds)
        base_dir = None
    else:
        resolved = inventory_file.expanduser().resolve(strict=False)
        payload, evidence = load_inventory_file(resolved)
        base_dir = resolved.parent
    return records_from_payload(payload, evidence, base_dir=base_dir)
