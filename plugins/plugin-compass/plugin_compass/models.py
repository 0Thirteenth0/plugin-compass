"""Typed, deterministic public records for Plugin Compass."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from .skill_models import (
        SkillAmbiguity,
        SkillAssessment,
        SkillRecord,
        SkillRecommendation,
        StandaloneDiscoverySummary,
    )


CLASSIFICATIONS = (
    "Use now",
    "Useful on demand",
    "Redundant",
    "Irrelevant to this project",
    "Blocked or untrusted",
    "Unknown or insufficient evidence",
)

TRIAGE_STATES = (
    "unreviewed",
    "credible",
    "suspected-false-positive",
    "resolved",
    "unknown",
)


def stable_id(prefix: str, *parts: object) -> str:
    payload = "\x1f".join(str(part) for part in parts)
    digest = sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def sorted_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(
        {value for value in values if value},
        key=lambda value: (value.casefold(), value),
    ))


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_id: str
    kind: str
    source: str
    subject: str
    detail: str
    status: str = "observed"
    source_version: str | None = None
    target_root: str | None = None

    @classmethod
    def create(
        cls,
        kind: str,
        source: str,
        subject: str,
        detail: str,
        *,
        status: str = "observed",
        source_version: str | None = None,
        target_root: str | None = None,
    ) -> "EvidenceRecord":
        return cls(
            evidence_id=stable_id(
                "ev",
                kind,
                source,
                subject,
                detail,
                status,
                source_version or "",
                target_root or "",
            ),
            kind=kind,
            source=source,
            subject=subject,
            detail=detail,
            status=status,
            source_version=source_version,
            target_root=target_root,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.evidence_id,
            "kind": self.kind,
            "source": self.source,
            "subject": self.subject,
            "detail": self.detail,
            "status": self.status,
            "source_version": self.source_version,
            "target_root": self.target_root,
        }


@dataclass(frozen=True, slots=True)
class ExecutionReadiness:
    status: str = "not_assessed"
    root: str | None = None
    references: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "root": self.root, "references": list(self.references)}


@dataclass(frozen=True, slots=True)
class CapabilityRecord:
    capability_id: str
    name: str
    description: str
    source: str
    evidence_refs: tuple[str, ...] = ()
    readiness: ExecutionReadiness = field(default_factory=ExecutionReadiness)

    @classmethod
    def create(
        cls,
        plugin_id: str,
        name: str,
        description: str,
        source: str,
        evidence_refs: Iterable[str] = (),
    ) -> "CapabilityRecord":
        clean_name = name.strip() or "unknown-capability"
        return cls(
            capability_id=stable_id("cap", plugin_id, clean_name.casefold(), source),
            name=clean_name,
            description=description.strip(),
            source=source,
            evidence_refs=sorted_unique(evidence_refs),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.capability_id,
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "evidence_refs": list(sorted_unique(self.evidence_refs)),
            "readiness": self.readiness.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class FindingRecord:
    finding_id: str
    source_tool: str
    source_version: str | None
    check_id: str
    severity: str
    message: str
    subjects: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    fix_commands: tuple[str, ...] = ()
    target_plugin_ids: tuple[str, ...] = ()
    target_root: str | None = None
    evidence_refs: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        source_tool: str,
        source_version: str | None,
        check_id: str,
        severity: str,
        message: str,
        subjects: Iterable[str] = (),
        paths: Iterable[str] = (),
        fix_commands: Iterable[str] = (),
        target_plugin_ids: Iterable[str] = (),
        target_root: str | None = None,
        evidence_refs: Iterable[str] = (),
    ) -> "FindingRecord":
        normalized_subjects = sorted_unique(subjects)
        normalized_paths = sorted_unique(paths)
        normalized_targets = sorted_unique(target_plugin_ids)
        return cls(
            finding_id=stable_id(
                "finding",
                source_tool,
                source_version or "",
                check_id,
                severity.casefold(),
                message,
                *normalized_subjects,
                *normalized_paths,
                *normalized_targets,
                target_root or "",
            ),
            source_tool=source_tool,
            source_version=source_version,
            check_id=check_id,
            severity=severity.casefold() or "unknown",
            message=message,
            subjects=normalized_subjects,
            paths=normalized_paths,
            fix_commands=sorted_unique(fix_commands),
            target_plugin_ids=normalized_targets,
            target_root=target_root,
            evidence_refs=sorted_unique(evidence_refs),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.finding_id,
            "source_tool": self.source_tool,
            "source_version": self.source_version,
            "check_id": self.check_id,
            "severity": self.severity,
            "message": self.message,
            "subjects": list(sorted_unique(self.subjects)),
            "paths": list(sorted_unique(self.paths)),
            "fix_commands": list(sorted_unique(self.fix_commands)),
            "target_plugin_ids": list(sorted_unique(self.target_plugin_ids)),
            "target_root": self.target_root,
            "evidence_refs": list(sorted_unique(self.evidence_refs)),
        }


@dataclass(frozen=True, slots=True)
class FindingTriage:
    finding_id: str
    state: str
    rationale: str

    def __post_init__(self) -> None:
        if self.state not in TRIAGE_STATES:
            raise ValueError(f"unsupported finding triage state: {self.state}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "state": self.state,
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class PluginRecord:
    plugin_id: str
    name: str
    marketplace: str
    version: str | None
    installed: bool
    enabled: bool
    source_root: str | None
    manifest_path: str | None = None
    description: str = ""
    capabilities: tuple[CapabilityRecord, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    metadata_status: str = "unknown"
    platforms: tuple[str, ...] = ()
    has_hooks: bool = False
    has_mcp: bool = False
    has_app: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "name": self.name,
            "marketplace": self.marketplace,
            "version": self.version,
            "installed": self.installed,
            "enabled": self.enabled,
            "source_root": self.source_root,
            "manifest_path": self.manifest_path,
            "description": self.description,
            "metadata_status": self.metadata_status,
            "platforms": list(sorted_unique(self.platforms)),
            "surfaces": {
                "hooks": self.has_hooks,
                "mcp": self.has_mcp,
                "app": self.has_app,
            },
            "capabilities": [
                item.to_dict()
                for item in sorted(
                    self.capabilities,
                    key=lambda value: (value.name.casefold(), value.capability_id),
                )
            ],
            "evidence": [
                item.to_dict()
                for item in sorted(self.evidence, key=lambda value: value.evidence_id)
            ],
        }


@dataclass(frozen=True, slots=True)
class RepositoryContext:
    root: str
    exists: bool
    empty: bool
    authority_files: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    prohibited_plugins: tuple[str, ...] = ()
    has_authority_system: bool = False
    evidence: tuple[EvidenceRecord, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "exists": self.exists,
            "empty": self.empty,
            "authority_files": list(sorted_unique(self.authority_files)),
            "languages": list(sorted_unique(self.languages)),
            "prohibited_plugins": list(sorted_unique(self.prohibited_plugins)),
            "has_authority_system": self.has_authority_system,
            "evidence": [
                item.to_dict()
                for item in sorted(self.evidence, key=lambda value: value.evidence_id)
            ],
        }


@dataclass(frozen=True, slots=True)
class Assessment:
    plugin_id: str
    classification: str
    dimensions: dict[str, str]
    hard_gates: tuple[str, ...]
    rationale: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    finding_ids: tuple[str, ...] = ()
    overlap_groups: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "classification": self.classification,
            "dimensions": dict(sorted(self.dimensions.items())),
            "hard_gates": list(sorted_unique(self.hard_gates)),
            "rationale": list(self.rationale),
            "evidence_refs": list(sorted_unique(self.evidence_refs)),
            "finding_ids": list(sorted_unique(self.finding_ids)),
            "overlap_groups": list(sorted_unique(self.overlap_groups)),
        }


@dataclass(frozen=True, slots=True)
class OverlapGroup:
    group_id: str
    capability: str
    members: tuple[str, ...]
    winner: str | None
    rationale: str
    finding_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "capability": self.capability,
            "members": list(sorted_unique(self.members)),
            "winner": self.winner,
            "rationale": self.rationale,
            "finding_ids": list(sorted_unique(self.finding_ids)),
            "evidence_refs": list(sorted_unique(self.evidence_refs)),
        }


@dataclass(frozen=True, slots=True)
class Recommendation:
    plugin_id: str
    capability_names: tuple[str, ...]
    rationale: str
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "capability_names": list(sorted_unique(self.capability_names)),
            "rationale": self.rationale,
            "evidence_refs": list(sorted_unique(self.evidence_refs)),
        }


@dataclass(frozen=True, slots=True)
class InvocationRoute:
    route_id: str
    plugin_id: str
    capability_name: str
    trigger: str
    invoker: str
    rationale: str
    evidence_refs: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        plugin_id: str,
        capability_name: str,
        trigger: str,
        invoker: str,
        rationale: str,
        evidence_refs: Iterable[str] = (),
    ) -> "InvocationRoute":
        return cls(
            route_id=stable_id(
                "route",
                plugin_id,
                capability_name.casefold(),
                trigger,
                invoker,
            ),
            plugin_id=plugin_id,
            capability_name=capability_name,
            trigger=trigger,
            invoker=invoker,
            rationale=rationale,
            evidence_refs=sorted_unique(evidence_refs),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "plugin_id": self.plugin_id,
            "capability_name": self.capability_name,
            "trigger": self.trigger,
            "invoker": self.invoker,
            "rationale": self.rationale,
            "evidence_refs": list(sorted_unique(self.evidence_refs)),
        }


@dataclass(frozen=True, slots=True)
class SchedulingGuidance:
    objective: str
    model_policy: str
    effort_bands: dict[str, str]
    decision_fields: tuple[str, ...]
    validation_gate: str
    escalation_policy: str
    delegation_policy: str
    limitations: str
    enforcement: str = "advisory"

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "enforcement": self.enforcement,
            "model_policy": self.model_policy,
            "effort_bands": dict(sorted(self.effort_bands.items())),
            "decision_fields": list(self.decision_fields),
            "validation_gate": self.validation_gate,
            "escalation_policy": self.escalation_policy,
            "delegation_policy": self.delegation_policy,
            "limitations": self.limitations,
        }


@dataclass(frozen=True, slots=True)
class RecommendationPlan:
    task: str
    repository: RepositoryContext
    plugins: tuple[PluginRecord, ...]
    skills: tuple["SkillRecord", ...]
    standalone_discovery: "StandaloneDiscoverySummary"
    skill_assessments: tuple["SkillAssessment", ...]
    skill_ambiguities: tuple["SkillAmbiguity", ...]
    skill_recommendations: tuple["SkillRecommendation", ...]
    findings: tuple[FindingRecord, ...]
    triage: tuple[FindingTriage, ...]
    assessments: tuple[Assessment, ...]
    overlap_groups: tuple[OverlapGroup, ...]
    recommendations: tuple[Recommendation, ...]
    invocation_routes: tuple[InvocationRoute, ...]
    generated_prompt: str
    optimization_goal: str = "speed"
    scheduling_guidance: SchedulingGuidance | None = None
    evidence: tuple[EvidenceRecord, ...] = field(default_factory=tuple)
    schema_version: str = "plugin-compass.plan.v5"

    def __post_init__(self) -> None:
        from .skill_models import StandaloneDiscoverySummary

        if not isinstance(self.standalone_discovery, StandaloneDiscoverySummary):
            raise TypeError(
                "standalone_discovery must be a StandaloneDiscoverySummary"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task": self.task,
            "optimization_goal": self.optimization_goal,
            "scheduling_guidance": (
                self.scheduling_guidance.to_dict() if self.scheduling_guidance else None
            ),
            "repository": self.repository.to_dict(),
            "plugins": [
                item.to_dict()
                for item in sorted(self.plugins, key=lambda value: value.plugin_id.casefold())
            ],
            "skills": [
                item.to_dict()
                for item in sorted(self.skills, key=lambda value: value.skill_id)
            ],
            "standalone_discovery": self.standalone_discovery.to_dict(),
            "skill_assessments": [
                item.to_dict()
                for item in sorted(
                    self.skill_assessments, key=lambda value: value.skill_id
                )
            ],
            "skill_ambiguities": [
                item.to_dict()
                for item in sorted(
                    self.skill_ambiguities,
                    key=lambda value: (value.name.casefold(), value.candidates),
                )
            ],
            "skill_recommendations": [
                item.to_dict()
                for item in sorted(
                    self.skill_recommendations, key=lambda value: value.skill_id
                )
            ],
            "findings": [
                item.to_dict()
                for item in sorted(self.findings, key=lambda value: value.finding_id)
            ],
            "triage": [
                item.to_dict()
                for item in sorted(self.triage, key=lambda value: value.finding_id)
            ],
            "assessments": [
                item.to_dict()
                for item in sorted(self.assessments, key=lambda value: value.plugin_id.casefold())
            ],
            "overlap_groups": [
                item.to_dict()
                for item in sorted(self.overlap_groups, key=lambda value: value.group_id)
            ],
            "recommendations": [
                item.to_dict()
                for item in sorted(
                    self.recommendations, key=lambda value: value.plugin_id.casefold()
                )
            ],
            "invocation_routes": [
                item.to_dict()
                for item in sorted(
                    self.invocation_routes,
                    key=lambda value: (value.capability_name.casefold(), value.route_id),
                )
            ],
            "generated_prompt": self.generated_prompt,
            "evidence": [
                item.to_dict()
                for item in sorted(self.evidence, key=lambda value: value.evidence_id)
            ],
        }
