"""Source-neutral skill records and plugin compatibility projection."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Iterable
from urllib.parse import quote

from .models import ExecutionReadiness, PluginRecord, sorted_unique, stable_id


SKILL_SOURCE_TYPES = (
    "plugin",
    "standalone-user",
    "standalone-project",
    "system",
    "session-only",
)
SKILL_TRUST_STATUSES = (
    "not_assessed",
    "trusted",
    "unknown",
    "untrusted",
    "blocked",
    "rejected",
)
STANDALONE_DISCOVERY_STATUSES = (
    "not_configured",
    "complete",
    "degraded",
)
STANDALONE_DIAGNOSTIC_FIELDS = frozenset({
    "code",
    "source_type",
    "source_identity",
    "path",
    "detail",
})
SKILL_ASSESSMENT_DIMENSIONS = frozenset({
    "repository_and_task_relevance",
    "trust_and_security",
    "metadata_completeness",
    "execution_readiness",
})


@dataclass(frozen=True, slots=True)
class StandaloneDiscoveryDiagnostic:
    """Closed, immutable diagnostic emitted by standalone discovery."""

    code: str
    source_type: str
    source_identity: str
    path: str
    detail: str

    @classmethod
    def create(
        cls,
        value: "StandaloneDiscoveryDiagnostic | Mapping[str, object]",
    ) -> "StandaloneDiscoveryDiagnostic":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("standalone discovery diagnostic must be a mapping")
        if frozenset(value) != STANDALONE_DIAGNOSTIC_FIELDS:
            raise ValueError(
                "standalone discovery diagnostic fields must be exactly: "
                + ", ".join(sorted(STANDALONE_DIAGNOSTIC_FIELDS))
            )
        fields: dict[str, str] = {}
        for name in STANDALONE_DIAGNOSTIC_FIELDS:
            field_value = value[name]
            if not isinstance(field_value, str):
                raise ValueError(
                    f"standalone discovery diagnostic field {name} must be a string"
                )
            fields[name] = field_value
        return cls(**fields)

    def sort_key(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (value.casefold(), value)
            for value in (
                self.code,
                self.source_type,
                self.source_identity,
                self.path,
                self.detail,
            )
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "source_type": self.source_type,
            "source_identity": self.source_identity,
            "path": self.path,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class StandaloneDiscoverySummary:
    """Canonical public summary of explicitly configured root discovery."""

    status: str
    diagnostics: tuple[StandaloneDiscoveryDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in STANDALONE_DISCOVERY_STATUSES:
            raise ValueError(f"unsupported standalone discovery status: {self.status}")
        if any(
            not isinstance(item, StandaloneDiscoveryDiagnostic)
            for item in self.diagnostics
        ):
            raise TypeError(
                "standalone discovery diagnostics must be typed diagnostics"
            )
        canonical = tuple(sorted(set(self.diagnostics), key=lambda item: item.sort_key()))
        object.__setattr__(self, "diagnostics", canonical)
        if self.status == "degraded" and not canonical:
            raise ValueError("degraded standalone discovery status requires diagnostics")
        if self.status != "degraded" and canonical:
            raise ValueError(
                f"{self.status} standalone discovery status cannot include diagnostics"
            )

    @classmethod
    def create(
        cls,
        status: str,
        diagnostics: Iterable[
            StandaloneDiscoveryDiagnostic | Mapping[str, object]
        ] = (),
    ) -> "StandaloneDiscoverySummary":
        return cls(
            status=status,
            diagnostics=tuple(
                StandaloneDiscoveryDiagnostic.create(item) for item in diagnostics
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


def normalize_relative_skill_path(value: str) -> str:
    """Return a portable logical path, rejecting absolute or traversing inputs."""
    candidate = value.strip().replace("\\", "/")
    if not candidate or candidate.startswith("/") or re.match(r"^[A-Za-z]:", candidate):
        raise ValueError("skill relative path must be non-empty and relative")
    parts = []
    for part in PurePosixPath(candidate).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            raise ValueError("skill relative path must not contain parent traversal")
        parts.append(part)
    if not parts:
        raise ValueError("skill relative path must be non-empty and relative")
    return PurePosixPath(*parts).as_posix()


def qualified_skill_identity(
    source_type: str, source_identity: str, relative_path: str,
) -> str:
    """Build a readable identity solely from normalized logical provenance."""
    return (
        f"skill://{source_type}/"
        f"{quote(source_identity, safe='')}/"
        f"{quote(relative_path, safe='/')}"
    )


@dataclass(frozen=True, slots=True)
class SkillRecord:
    skill_id: str
    qualified_identity: str
    name: str
    description: str
    path: str
    relative_path: str
    source_type: str
    source_identity: str
    trust_status: str = "not_assessed"
    metadata_status: str = "unknown"
    readiness: ExecutionReadiness = ExecutionReadiness()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.source_type not in SKILL_SOURCE_TYPES:
            raise ValueError(f"unsupported skill source type: {self.source_type}")
        if self.trust_status not in SKILL_TRUST_STATUSES:
            raise ValueError(f"unsupported skill trust status: {self.trust_status!r}")
        if not self.source_identity.strip():
            raise ValueError("skill source identity must be non-empty")
        normalized_path = normalize_relative_skill_path(self.relative_path)
        if normalized_path != self.relative_path:
            raise ValueError("skill relative path must already be normalized")
        expected_id = stable_id(
            "skill",
            qualified_skill_identity(
                self.source_type,
                self.source_identity.strip(),
                normalized_path,
            ),
        )
        expected_qualified_identity = qualified_skill_identity(
            self.source_type,
            self.source_identity.strip(),
            normalized_path,
        )
        if self.qualified_identity != expected_qualified_identity:
            raise ValueError("qualified skill identity must match logical provenance")
        if self.skill_id != expected_id:
            raise ValueError("skill id must match its logical source identity and relative path")

    @classmethod
    def create(
        cls,
        *,
        name: str,
        description: str,
        path: str,
        relative_path: str,
        source_type: str,
        source_identity: str,
        trust_status: str = "not_assessed",
        metadata_status: str = "unknown",
        readiness_status: str = "not_assessed",
        readiness_root: str | None = None,
        readiness_references: Iterable[str] = (),
        evidence_refs: Iterable[str] = (),
    ) -> "SkillRecord":
        normalized_path = normalize_relative_skill_path(relative_path)
        logical_source = source_identity.strip()
        if source_type not in SKILL_SOURCE_TYPES:
            raise ValueError(f"unsupported skill source type: {source_type}")
        if not logical_source:
            raise ValueError("skill source identity must be non-empty")
        qualified_identity = qualified_skill_identity(
            source_type,
            logical_source,
            normalized_path,
        )
        return cls(
            skill_id=stable_id("skill", qualified_identity),
            qualified_identity=qualified_identity,
            name=name.strip() or "unknown-skill",
            description=description.strip(),
            path=path,
            relative_path=normalized_path,
            source_type=source_type,
            source_identity=logical_source,
            trust_status=trust_status,
            metadata_status=metadata_status,
            readiness=ExecutionReadiness(
                readiness_status,
                readiness_root,
                sorted_unique(readiness_references),
            ),
            evidence_refs=sorted_unique(evidence_refs),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.skill_id,
            "qualified_identity": self.qualified_identity,
            "name": self.name,
            "description": self.description,
            "path": self.path,
            "relative_path": self.relative_path,
            "source": {"type": self.source_type, "identity": self.source_identity},
            "trust_status": self.trust_status,
            "metadata_status": self.metadata_status,
            "readiness": self.readiness.to_dict(),
            "evidence_refs": list(sorted_unique(self.evidence_refs)),
        }


@dataclass(frozen=True, slots=True)
class SkillAssessment:
    skill: SkillRecord
    classification: str
    dimensions: Mapping[str, str]
    hard_gates: tuple[str, ...]
    rationale: tuple[str, ...]

    def __post_init__(self) -> None:
        if frozenset(self.dimensions) != SKILL_ASSESSMENT_DIMENSIONS:
            raise ValueError(
                "skill assessment dimension fields must be exactly: "
                + ", ".join(sorted(SKILL_ASSESSMENT_DIMENSIONS))
            )
        if any(not isinstance(value, str) for value in self.dimensions.values()):
            raise ValueError("skill assessment dimension values must be strings")
        object.__setattr__(self, "dimensions", MappingProxyType(dict(self.dimensions)))

    @property
    def skill_id(self) -> str:
        return self.skill.skill_id

    @property
    def qualified_identity(self) -> str:
        return self.skill.qualified_identity

    @property
    def name(self) -> str:
        return self.skill.name

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.skill.to_dict(),
            "classification": self.classification,
            "dimensions": dict(sorted(self.dimensions.items())),
            "hard_gates": list(sorted_unique(self.hard_gates)),
            "rationale": list(self.rationale),
        }


@dataclass(frozen=True, slots=True)
class SkillAmbiguity:
    name: str
    candidates: tuple[str, ...]
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "candidates": sorted(
                set(self.candidates), key=lambda value: (value.casefold(), value),
            ),
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class SkillRecommendation:
    skill: SkillRecord
    rationale: str
    covered_terms: tuple[str, ...]

    @property
    def skill_id(self) -> str:
        return self.skill.skill_id

    @property
    def qualified_identity(self) -> str:
        return self.skill.qualified_identity

    @property
    def name(self) -> str:
        return self.skill.name

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.skill.to_dict(),
            "rationale": self.rationale,
            "covered_terms": list(sorted_unique(self.covered_terms)),
        }


@dataclass(frozen=True, slots=True)
class SkillDecisionResult:
    assessments: tuple[SkillAssessment, ...]
    ambiguities: tuple[SkillAmbiguity, ...]
    recommendations: tuple[SkillRecommendation, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "assessments": [
                item.to_dict()
                for item in sorted(self.assessments, key=lambda value: value.skill_id)
            ],
            "ambiguities": [
                item.to_dict()
                for item in sorted(
                    self.ambiguities,
                    key=lambda value: (value.name.casefold(), value.candidates),
                )
            ],
            "recommendations": [
                item.to_dict()
                for item in sorted(self.recommendations, key=lambda value: value.skill_id)
            ],
        }


def plugin_packaged_skills(plugins: Iterable[PluginRecord]) -> tuple[SkillRecord, ...]:
    """Project already-discovered plugin SKILL.md capabilities into SkillRecord values."""
    records: list[SkillRecord] = []
    for plugin in plugins:
        if not plugin.source_root:
            continue
        source_root = Path(plugin.source_root).resolve(strict=False)
        for capability in plugin.capabilities:
            skill_path = Path(capability.source).resolve(strict=False)
            if skill_path.name.casefold() != "skill.md":
                continue
            try:
                relative_path = skill_path.relative_to(source_root).as_posix()
            except ValueError:
                continue
            records.append(
                SkillRecord.create(
                    name=capability.name,
                    description=capability.description,
                    path=capability.source,
                    relative_path=relative_path,
                    source_type="plugin",
                    source_identity=plugin.plugin_id,
                    trust_status="not_assessed",
                    metadata_status=(
                        "complete" if capability.name and capability.description else "partial"
                    ),
                    readiness_status=capability.readiness.status,
                    readiness_root=capability.readiness.root,
                    readiness_references=capability.readiness.references,
                    evidence_refs=capability.evidence_refs,
                )
            )
    return tuple(sorted(records, key=lambda item: item.skill_id))
