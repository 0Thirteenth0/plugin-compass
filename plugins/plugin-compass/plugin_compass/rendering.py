"""Deterministic human and machine rendering."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Iterable, Mapping

from .models import (
    Assessment, InvocationRoute, Recommendation, RecommendationPlan, SchedulingGuidance,
)

if TYPE_CHECKING:
    from .skill_models import SkillAmbiguity, SkillRecommendation


def render_json(value: object) -> str:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def build_session_prompt(
    task: str,
    recommendations: Iterable[Recommendation],
    assessments: Iterable[Assessment],
    invocation_routes: Iterable[InvocationRoute] = (),
    scheduling_guidance: SchedulingGuidance | None = None,
    *,
    skill_recommendations: Iterable["SkillRecommendation"] = (),
    skill_ambiguities: Iterable["SkillAmbiguity"] = (),
    discovery_diagnostics: Iterable[Mapping[str, object]] = (),
    readiness_notes: Iterable[str] = (),
) -> str:
    selected = tuple(sorted(recommendations, key=lambda item: item.plugin_id.casefold()))
    routes = tuple(
        sorted(
            invocation_routes,
            key=lambda item: (item.capability_name.casefold(), item.route_id),
        )
    )
    excluded = tuple(
        sorted(
            (
                item
                for item in assessments
                if item.classification in {"Blocked or untrusted", "Redundant"}
            ),
            key=lambda item: item.plugin_id.casefold(),
        )
    )
    selected_skills = tuple(sorted(
        skill_recommendations,
        key=lambda item: (
            item.qualified_identity.casefold(), item.qualified_identity,
        ),
    ))
    ambiguities = tuple(sorted(
        skill_ambiguities, key=lambda item: (item.name.casefold(), item.candidates),
    ))
    diagnostics = tuple(sorted(
        discovery_diagnostics,
        key=lambda item: (
            str(item.get("code", "")).casefold(),
            str(item.get("source_type", "")).casefold(),
            str(item.get("source_identity", "")).casefold(),
            str(item.get("path", "")).casefold(),
        ),
    ))
    lines = [
        f"Task: {task.strip() or 'No task was supplied.'}",
        "",
        "Use only this evidence-backed capability set:",
    ]
    if selected:
        for item in selected:
            capabilities = ", ".join(item.capability_names) or "declared capability"
            lines.append(f"- {item.plugin_id}: {capabilities}. {item.rationale}")
    else:
        lines.append("- No additional plugin is selected for this task.")
    if selected_skills:
        lines.extend(["", "Use these exact qualified skills (selection only; do not auto-execute):"])
        for item in selected_skills:
            skill = item.skill
            lines.append(
                f"- {item.qualified_identity}: source={skill.source_type}/"
                f"{skill.source_identity}; trust={skill.trust_status}; "
                f"metadata={skill.metadata_status}; readiness={skill.readiness.status}. "
                f"{item.rationale}"
            )
    if ambiguities:
        lines.extend(["", "Unresolved skill-name ambiguities (select none):"])
        for item in ambiguities:
            lines.append(
                f"- {item.name}: {', '.join(item.candidates)}. {item.rationale}"
            )
    if diagnostics:
        lines.extend(["", "Standalone discovery diagnostics:"])
        for item in diagnostics:
            lines.append(
                f"- {item.get('code', 'unknown')}: {item.get('source_type', 'unknown')}/"
                f"{item.get('source_identity', 'unknown')}. {item.get('detail', '')}"
            )
    if routes:
        lines.extend(["", "Skill invocation routing:"])
        for item in routes:
            lines.append(
                f"- For {item.trigger}, Plugin Compass recommends "
                f"`{item.capability_name}`; {item.invoker} invokes it. {item.rationale}"
            )
    if scheduling_guidance:
        lines.extend(["", "Per-agent effort guidance (advisory):"])
        lines.extend(scheduling_guidance_lines(scheduling_guidance))
    notes = tuple(sorted(readiness_notes))
    if notes:
        lines.extend(["", "Unresolved execution paths (not security findings):"])
        lines.extend(f"- {note}" for note in notes)
    if excluded:
        lines.extend(["", "Do not select these excluded alternatives:"])
        for item in excluded:
            lines.append(f"- {item.plugin_id}: {item.classification}. {item.rationale[-1]}")
    lines.extend(
        [
            "",
            "Preserve repository authority, keep unknown evidence unknown, and request "
            "authorization before credentials, external mutation, or plugin management.",
        ]
    )
    return "\n".join(lines)


def scheduling_guidance_lines(guidance: SchedulingGuidance) -> list[str]:
    lines = [
        "- Objective: fastest verified completion, not minimum cost.",
        f"- {guidance.model_policy}",
        "- Before each dispatch record: " + ", ".join(guidance.decision_fields) + ".",
        "- For a concrete task-specific native Codex handoff, use the handoff command "
        "with an agent-task JSON file; follow references/native-dispatch.md. "
        "A proposed handoff is not an executed or verified agent run.",
    ]
    for band in ("low", "medium", "high", "above_high"):
        lines.append(f"- {band}: {guidance.effort_bands[band]}")
    lines.extend(
        f"- {value}" for value in (
            guidance.validation_gate, guidance.escalation_policy,
            guidance.delegation_policy, guidance.limitations,
        )
    )
    return lines


def render_markdown(plan: RecommendationPlan) -> str:
    assessment_by_id = {item.plugin_id: item for item in plan.assessments}
    triage_by_id = {item.finding_id: item for item in plan.triage}
    lines = [
        "# Plugin Compass Assessment",
        "",
        f"**Task:** {plan.task or 'Not supplied'}",
        f"**Repository:** `{plan.repository.root}`",
        "",
        "## Minimal capability set",
        "",
    ]
    if plan.recommendations:
        for item in plan.recommendations:
            capabilities = ", ".join(item.capability_names) or "declared capability"
            lines.append(f"- **{item.plugin_id}** - {capabilities}. {item.rationale}")
    else:
        lines.append("- No additional plugin is selected for this task.")

    lines.extend(["", "## Source-neutral skill selection", ""])
    if plan.skill_recommendations:
        for item in plan.skill_recommendations:
            lines.append(
                f"- **{item.qualified_identity}** - {item.rationale} "
                f"Trust={item.skill.trust_status}; metadata={item.skill.metadata_status}; "
                f"readiness={item.skill.readiness.status}."
            )
    else:
        lines.append("- No unambiguous eligible skill is selected for this task.")
    if plan.skill_ambiguities:
        lines.extend(["", "### Unresolved skill-name ambiguities", ""])
        for item in plan.skill_ambiguities:
            lines.append(
                f"- **{item.name}** - candidates: {', '.join(item.candidates)}. "
                f"{item.rationale}"
            )

    lines.extend(["", "## Source-neutral skill assessments", ""])
    if plan.skill_assessments:
        for item in plan.skill_assessments:
            skill = item.skill
            lines.append(
                f"- **{item.qualified_identity}** - {item.classification}; "
                f"source={skill.source_type}/{skill.source_identity}; "
                f"trust={skill.trust_status}; metadata={skill.metadata_status}; "
                f"readiness={skill.readiness.status}."
            )
    else:
        lines.append("- No skills were available for assessment.")

    lines.extend(["", "## Standalone discovery", ""])
    lines.append(f"- Status: {plan.standalone_discovery.status}.")
    for diagnostic in plan.standalone_discovery.diagnostics:
        lines.append(
            f"- `{diagnostic.code}` - {diagnostic.source_type}/"
            f"{diagnostic.source_identity}: {diagnostic.detail}"
        )

    lines.extend(["", "## Conditional invocation routes", ""])
    if plan.invocation_routes:
        for item in plan.invocation_routes:
            lines.append(
                f"- **{item.capability_name}** - trigger: {item.trigger}; "
                f"invoker: {item.invoker}. {item.rationale}"
            )
    else:
        lines.append("- No conditional exact-skill route applies to this task.")

    if plan.scheduling_guidance:
        lines.extend(["", "## Per-agent effort guidance (advisory)", ""])
        lines.extend(scheduling_guidance_lines(plan.scheduling_guidance))

    lines.extend(["", "## Project relevance", ""])
    for plugin in plan.plugins:
        assessment = assessment_by_id[plugin.plugin_id]
        lines.append(
            f"- **{plugin.plugin_id}** - {assessment.classification}; "
            f"relevance={assessment.dimensions['repository_and_task_relevance']}; "
            f"trust={assessment.dimensions['trust_and_security']}."
        )

    lines.extend(["", "## Local execution-readiness evidence", ""])
    lines.append("File existence is not execution verification or trust. Checks cover explicit plugin-root references in the declared local source only; alternate launchers and installed-cache parity remain unverified.")
    for plugin in plan.plugins:
        for capability in plugin.capabilities:
            if capability.readiness.status in {"missing_files", "unknown"}:
                lines.append(f"- **{plugin.plugin_id}:{capability.name}** - {capability.readiness.status}; excluded pending runtime-path review. Evidence: {', '.join(capability.evidence_refs)}.")

    lines.extend(["", "## Overlap groups", ""])
    if plan.overlap_groups:
        for group in plan.overlap_groups:
            members = ", ".join(group.members)
            lines.append(
                f"- **{group.capability}** - members: {members}; "
                f"winner: {group.winner or 'none'}. {group.rationale}"
            )
    else:
        lines.append("- No cross-plugin overlap finding was supplied by DrSkill.")

    lines.extend(["", "## Trust and finding triage", ""])
    if plan.findings:
        for finding in plan.findings:
            triage = triage_by_id[finding.finding_id]
            lines.append(
                f"- `{finding.source_tool}:{finding.check_id}` "
                f"({finding.severity}, {triage.state}) - {finding.message}"
            )
    else:
        lines.append("- No external tool findings were supplied; trust remains unknown where applicable.")

    lines.extend(["", "## Session adaptation prompt", "", "```text", plan.generated_prompt, "```", ""])
    return "\n".join(lines)
