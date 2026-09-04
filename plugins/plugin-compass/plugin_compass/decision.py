"""Task-specific triage, relevance, overlap, and recommendation decisions."""

from __future__ import annotations

import platform
import re
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from .adapters.standalone import StandaloneDiscoveryResult
from .scheduling import SCHEDULING_TERMS, build_scheduling_guidance

from .models import (
    Assessment,
    CapabilityRecord,
    EvidenceRecord,
    FindingRecord,
    FindingTriage,
    InvocationRoute,
    OverlapGroup,
    PluginRecord,
    Recommendation,
    RecommendationPlan,
    RepositoryContext,
    sorted_unique,
    stable_id,
)
from .skill_decision import build_skill_decision
from .skill_models import (
    SkillRecord,
    StandaloneDiscoverySummary,
    plugin_packaged_skills,
)


TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    "a",
    "an",
    "and",
    "app",
    "assistant",
    "before",
    "by",
    "choose",
    "codex",
    "for",
    "from",
    "in",
    "new",
    "of",
    "on",
    "or",
    "plugin",
    "plugins",
    "skill",
    "skills",
    "the",
    "to",
    "tool",
    "tools",
    "use",
    "using",
    "with",
}

OVERLAP_CHECKS = {
    "description-overlap",
    "double-load",
    "exact-duplicate",
    "name-shadow",
    "near-duplicate",
}

AUTHORITY_RISK_PHRASES = {
    "governance system",
    "handoff system",
    "orchestrator",
    "persistent memory",
    "task ledger",
}

PLATFORM_ALIASES = {
    "darwin": {"darwin", "mac", "macos", "osx"},
    "linux": {"linux", "posix", "wsl"},
    "windows": {"nt", "win", "win32", "windows"},
}

LLM_COST_OPTIMIZER_PLUGIN = "claude-code-skills"
LLM_COST_OPTIMIZER_CAPABILITY = "llm-cost-optimizer"
LLM_COST_ROUTE_TERMS = {
    "cost", "costs", "spend", "spending", "budget", "pricing", "cheaper",
    "cheapest", "token", "tokens",
}


def _usable_capability(capability: CapabilityRecord) -> bool:
    return capability.readiness.status in {"not_declared", "files_present"}


def tokenize(text: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in TOKEN_PATTERN.findall(text.casefold())
        if len(token) > 1 and token not in STOPWORDS
    )


def triage_findings(findings: Iterable[FindingRecord]) -> tuple[FindingTriage, ...]:
    results: list[FindingTriage] = []
    for finding in findings:
        message = finding.message.casefold()
        subjects = " ".join(finding.subjects).casefold()
        if finding.source_tool == "hol-plugin-scanner" and finding.severity in {
            "critical",
            "high",
        }:
            state = "credible"
            rationale = "Target-specific HOL high/critical findings are hard-gate evidence."
        elif (
            finding.source_tool == "drskill"
            and finding.check_id == "injection-credential-read"
            and any(
                marker in f"{subjects} {message}"
                for marker in ("auditor", "scanner", "secret_scanner", "regex")
            )
        ):
            state = "suspected-false-positive"
            rationale = (
                "The static finding appears to quote scanner or auditor detection material; "
                "the source evidence remains visible and requires human review."
            )
        else:
            state = "unreviewed"
            rationale = "No deterministic review rule establishes or dismisses this finding."
        results.append(FindingTriage(finding.finding_id, state, rationale))
    return tuple(sorted(results, key=lambda item: item.finding_id))


def _normalize_path(value: str) -> str:
    normalized = value.replace("\\", "/").rstrip("/")
    wsl_match = re.match(r"^/mnt/([a-zA-Z])(?:/(.*))?$", normalized)
    if wsl_match:
        suffix = wsl_match.group(2) or ""
        normalized = f"{wsl_match.group(1)}:/{suffix}".rstrip("/")
    return normalized.casefold()


def _finding_applies(finding: FindingRecord, plugin: PluginRecord) -> bool:
    identities = {plugin.plugin_id.casefold(), plugin.name.casefold()}
    identity_match = bool(
        identities & {item.casefold() for item in finding.target_plugin_ids}
    )
    if finding.source_tool == "hol-plugin-scanner":
        if finding.target_root and plugin.source_root:
            return identity_match and (
                _normalize_path(plugin.source_root) == _normalize_path(finding.target_root)
            )
        return identity_match
    if identity_match:
        return True
    if plugin.source_root and finding.target_root:
        if _normalize_path(plugin.source_root) == _normalize_path(finding.target_root):
            return True
    if plugin.source_root:
        root = _normalize_path(plugin.source_root) + "/"
        if any(_normalize_path(path).startswith(root) for path in finding.paths):
            return True
    capability_names = {item.name.casefold() for item in plugin.capabilities}
    return bool(capability_names & {item.casefold() for item in finding.subjects})


def _hol_status(plugin: PluginRecord, evidence: Iterable[EvidenceRecord]) -> str:
    identities = {plugin.plugin_id.casefold(), plugin.name.casefold()}
    matches = [
        item
        for item in evidence
        if item.kind == "hol-report"
        and item.subject.casefold() in identities
        and (
            not item.target_root
            or not plugin.source_root
            or _normalize_path(item.target_root) == _normalize_path(plugin.source_root)
        )
    ]
    if any(item.status == "blocked" for item in matches):
        return "blocked"
    if any(item.status == "reviewed" for item in matches):
        return "reviewed"
    return "unknown"


def _host_platform_names(host_platform: str) -> set[str]:
    normalized = host_platform.casefold()
    names = {normalized}
    for canonical, aliases in PLATFORM_ALIASES.items():
        if normalized == canonical or normalized in aliases:
            names.add(canonical)
            names.update(aliases)
    return names


def _platform_fit(plugin: PluginRecord, host_platform: str) -> str:
    if not plugin.platforms:
        return "unknown"
    declared = {item.casefold() for item in plugin.platforms}
    if declared & {"all", "any", "cross-platform", "crossplatform"}:
        return "high"
    return "high" if declared & _host_platform_names(host_platform) else "low"


def _authority_risk(plugin: PluginRecord) -> bool:
    text = " ".join(
        [plugin.name, plugin.description]
        + [f"{item.name} {item.description}" for item in plugin.capabilities]
    ).casefold()
    return any(phrase in text for phrase in AUTHORITY_RISK_PHRASES)


def _relevance(
    plugin: PluginRecord,
    task: str,
    repository: RepositoryContext,
) -> tuple[str, set[str], bool]:
    task_tokens = set(tokenize(task))
    repository_tokens = set(tokenize(" ".join(repository.languages)))
    matched: set[str] = set()
    exact = False
    for capability in plugin.capabilities:
        name_tokens = set(tokenize(capability.name))
        capability_tokens = set(tokenize(f"{capability.name} {capability.description}"))
        matched.update(task_tokens & capability_tokens)
        exact = exact or bool(name_tokens) and name_tokens <= task_tokens
    plugin_tokens = set(tokenize(f"{plugin.name} {plugin.description}"))
    matched.update(task_tokens & plugin_tokens)
    repository_match = len(repository_tokens & set().union(
        plugin_tokens,
        *(set(tokenize(f"{item.name} {item.description}")) for item in plugin.capabilities),
    ))
    if exact or len(matched) >= 2:
        return "high", matched, exact
    if len(matched) == 1 or repository_match:
        return "medium", matched, exact
    if task.strip():
        return "low", matched, exact
    return "unknown", matched, exact


def _evidence_completeness(plugin: PluginRecord, hol_status: str) -> str:
    if plugin.metadata_status == "complete" and plugin.capabilities and hol_status == "reviewed":
        return "high"
    if plugin.metadata_status in {"complete", "partial"} and plugin.capabilities:
        return "medium"
    if plugin.evidence:
        return "low"
    return "unknown"


def _runtime_cost(plugin: PluginRecord) -> str:
    if plugin.has_hooks or plugin.has_mcp or plugin.has_app:
        return "high"
    if len(plugin.capabilities) > 15:
        return "medium"
    return "low"


def _preliminary_assessment(
    plugin: PluginRecord,
    repository: RepositoryContext,
    task: str,
    findings: tuple[FindingRecord, ...],
    evidence: tuple[EvidenceRecord, ...],
    host_platform: str,
) -> tuple[Assessment, dict[str, object]]:
    relevant_findings = tuple(item for item in findings if _finding_applies(item, plugin))
    relevance, matched_tokens, exact = _relevance(plugin, task, repository)
    hol_status = _hol_status(plugin, evidence)
    platform_fit = _platform_fit(plugin, host_platform)
    completeness = _evidence_completeness(plugin, hol_status)
    runtime_cost = _runtime_cost(plugin)
    matching_capabilities = [
        item for item in plugin.capabilities
        if set(tokenize(task)) & set(tokenize(f"{item.name} {item.description}"))
    ]
    unavailable = [item for item in matching_capabilities if not _usable_capability(item)]
    task_token_set = set(tokenize(task))
    exact_unavailable = [
        item for item in unavailable
        if set(tokenize(item.name)) and set(tokenize(item.name)) <= task_token_set
    ]
    readiness_blocked = bool(exact_unavailable) or (
        bool(matching_capabilities) and len(unavailable) == len(matching_capabilities)
    )
    gates: list[str] = []
    prohibited = {item.casefold() for item in repository.prohibited_plugins}
    if plugin.name.casefold() in prohibited or plugin.plugin_id.casefold() in prohibited:
        gates.append("repository policy prohibits this plugin")
    hol_elevated = [
        item
        for item in relevant_findings
        if item.source_tool == "hol-plugin-scanner" and item.severity in {"critical", "high"}
    ]
    if hol_elevated:
        gates.append("target-specific HOL report has unresolved high or critical findings")
    if platform_fit == "low":
        gates.append(f"declared platforms are incompatible with host {host_platform}")
    if repository.has_authority_system and _authority_risk(plugin):
        gates.append("plugin declares competing authority, orchestration, or persistent state")

    dimensions = {
        "repository_and_task_relevance": relevance,
        "functional_overlap": "unknown",
        "trust_and_security": hol_status,
        "platform_fit": platform_fit,
        "runtime_and_context_cost": runtime_cost,
        "evidence_completeness": completeness,
        "execution_readiness": "unresolved" if readiness_blocked else "partial" if unavailable else "static_only",
    }
    rationale = [
        (
            f"Relevance is {relevance}: matched task terms={len(matched_tokens)}; "
            f"exact capability match={str(exact).lower()}."
        ),
        (
            f"Availability is {'enabled' if plugin.enabled else 'disabled'}; "
            f"HOL trust evidence is {hol_status}; metadata is {plugin.metadata_status}."
        ),
    ]
    if unavailable:
        rationale.append(
            "Relevant capabilities with unresolved local-file readiness: "
            + ", ".join(sorted(item.name for item in unavailable))
            + ". They are excluded; this is not a security verdict."
        )
    if gates:
        classification = "Blocked or untrusted"
        rationale.append("Hard gates override relevance: " + "; ".join(sorted(gates)) + ".")
    elif plugin.metadata_status in {"malformed", "missing"}:
        classification = "Unknown or insufficient evidence"
    elif readiness_blocked:
        classification = "Unknown or insufficient evidence"
    elif hol_status == "unknown" and (plugin.has_hooks or plugin.has_mcp or plugin.has_app):
        classification = "Unknown or insufficient evidence"
        rationale.append("Executable plugin surfaces lack target-specific HOL evidence.")
    elif relevance == "high" and plugin.installed and plugin.enabled:
        classification = "Use now"
    elif relevance in {"high", "medium"}:
        classification = "Useful on demand"
    elif relevance == "low":
        classification = "Irrelevant to this project"
    else:
        classification = "Unknown or insufficient evidence"

    evidence_refs = [item.evidence_id for item in plugin.evidence]
    evidence_refs.extend(item.evidence_id for item in repository.evidence if gates)
    evidence_refs.extend(ref for finding in relevant_findings for ref in finding.evidence_refs)
    assessment = Assessment(
        plugin_id=plugin.plugin_id,
        classification=classification,
        dimensions=dimensions,
        hard_gates=sorted_unique(gates),
        rationale=tuple(rationale),
        evidence_refs=sorted_unique(evidence_refs),
        finding_ids=sorted_unique(item.finding_id for item in relevant_findings),
    )
    ranking: dict[str, object] = {
        "eligible": not gates and not readiness_blocked and plugin.metadata_status not in {"malformed", "missing"},
        "enabled": plugin.enabled,
        "relevance": {"unknown": 0, "low": 1, "medium": 2, "high": 3}[relevance],
        "exact": exact,
        "matched_tokens": matched_tokens,
        "capability_count": len(plugin.capabilities),
        "evidence": {"unknown": 0, "low": 1, "medium": 2, "high": 3}[completeness],
        "cost": {"high": 0, "medium": 1, "low": 2}[runtime_cost],
    }
    return assessment, ranking


def _overlap_groups(
    plugins: tuple[PluginRecord, ...],
    findings: tuple[FindingRecord, ...],
    rankings: dict[str, dict[str, object]],
) -> tuple[OverlapGroup, ...]:
    by_capability: dict[str, set[str]] = defaultdict(set)
    plugin_by_id = {item.plugin_id: item for item in plugins}
    for plugin in plugins:
        by_capability[plugin.name.casefold()].add(plugin.plugin_id)
        for capability in plugin.capabilities:
            by_capability[capability.name.casefold()].add(plugin.plugin_id)

    groups: list[OverlapGroup] = []
    for finding in findings:
        if finding.source_tool != "drskill" or finding.check_id not in OVERLAP_CHECKS:
            continue
        members: set[str] = set(finding.target_plugin_ids)
        for subject in finding.subjects:
            members.update(by_capability.get(subject.casefold(), set()))
        members = {item for item in members if item in plugin_by_id}
        if len(members) < 2:
            continue
        eligible = [item for item in members if bool(rankings[item]["eligible"])]
        winner = None
        if eligible:
            winner = sorted(
                eligible,
                key=lambda plugin_id: (
                    -int(bool(rankings[plugin_id]["enabled"])),
                    -int(rankings[plugin_id]["relevance"]),
                    -int(bool(rankings[plugin_id]["exact"])),
                    int(rankings[plugin_id]["capability_count"]),
                    -int(rankings[plugin_id]["evidence"]),
                    -int(rankings[plugin_id]["cost"]),
                    plugin_id.casefold(),
                ),
            )[0]
        label = min(finding.subjects, key=lambda value: (len(value), value.casefold())) if finding.subjects else finding.check_id
        rationale = (
            "No member is eligible after hard gates."
            if winner is None
            else f"{winner} wins by availability, relevance, specialization, evidence, cost, and stable ID."
        )
        groups.append(
            OverlapGroup(
                group_id=stable_id("overlap", finding.check_id, *sorted(members)),
                capability=label,
                members=sorted_unique(members),
                winner=winner,
                rationale=rationale,
                finding_ids=(finding.finding_id,),
                evidence_refs=finding.evidence_refs,
            )
        )
    deduplicated = {item.group_id: item for item in groups}
    return tuple(sorted(deduplicated.values(), key=lambda item: item.group_id))


def _apply_overlap(
    assessments: tuple[Assessment, ...],
    groups: tuple[OverlapGroup, ...],
) -> tuple[Assessment, ...]:
    by_plugin: dict[str, list[OverlapGroup]] = defaultdict(list)
    for group in groups:
        for member in group.members:
            by_plugin[member].append(group)
    updated: list[Assessment] = []
    for assessment in assessments:
        membership = by_plugin.get(assessment.plugin_id, [])
        dimensions = dict(assessment.dimensions)
        dimensions["functional_overlap"] = "high" if membership else "low"
        classification = assessment.classification
        rationale = list(assessment.rationale)
        losses = [
            group for group in membership if group.winner and group.winner != assessment.plugin_id
        ]
        if losses and classification in {"Use now", "Useful on demand"}:
            classification = "Redundant"
            rationale.append(
                "Task-specific overlap winner(s): "
                + ", ".join(sorted_unique(group.winner or "" for group in losses))
                + "."
            )
        updated.append(
            replace(
                assessment,
                classification=classification,
                dimensions=dimensions,
                rationale=tuple(rationale),
                overlap_groups=sorted_unique(group.group_id for group in membership),
            )
        )
    return tuple(sorted(updated, key=lambda item: item.plugin_id.casefold()))


def _minimal_recommendations(
    plugins: tuple[PluginRecord, ...],
    assessments: tuple[Assessment, ...],
    rankings: dict[str, dict[str, object]],
    *,
    excluded_tokens: Iterable[str] = (),
    optimization_goal: str = "speed",
) -> tuple[Recommendation, ...]:
    exclusions = set(excluded_tokens)
    assessment_by_id = {item.plugin_id: item for item in assessments}
    eligible_capabilities = {
        plugin.plugin_id: tuple(
            item for item in plugin.capabilities
            if _usable_capability(item) and not (
                optimization_goal == "speed"
                and plugin.name.casefold() == LLM_COST_OPTIMIZER_PLUGIN
                and item.name.casefold() == LLM_COST_OPTIMIZER_CAPABILITY
            )
        )
        for plugin in plugins
    }
    coverage_by_plugin = {
        plugin.plugin_id: (
            set(rankings[plugin.plugin_id]["matched_tokens"])
            & set().union(*(
                set(tokenize(f"{item.name} {item.description}"))
                for item in eligible_capabilities[plugin.plugin_id]
            ))
        ) - exclusions
        for plugin in plugins
    }
    candidates = [
        plugin
        for plugin in plugins
        if assessment_by_id[plugin.plugin_id].classification == "Use now"
        and coverage_by_plugin[plugin.plugin_id]
    ]
    coverable: set[str] = set()
    for plugin in candidates:
        coverable.update(coverage_by_plugin[plugin.plugin_id])
    uncovered = set(coverable)
    selected: list[PluginRecord] = []
    remaining = list(candidates)
    while uncovered and remaining:
        remaining.sort(
            key=lambda plugin: (
                -len(coverage_by_plugin[plugin.plugin_id] & uncovered),
                len(plugin.capabilities),
                plugin.plugin_id.casefold(),
            )
        )
        winner = remaining.pop(0)
        coverage = coverage_by_plugin[winner.plugin_id] & uncovered
        if not coverage:
            break
        selected.append(winner)
        uncovered -= coverage

    recommendations: list[Recommendation] = []
    for plugin in selected:
        matched = coverage_by_plugin[plugin.plugin_id]
        capabilities = [
            item
            for item in eligible_capabilities[plugin.plugin_id]
            if matched & set(tokenize(f"{item.name} {item.description}"))
        ]
        assessment = assessment_by_id[plugin.plugin_id]
        evidence_refs = list(assessment.evidence_refs)
        evidence_refs.extend(ref for item in capabilities for ref in item.evidence_refs)
        recommendations.append(
            Recommendation(
                plugin_id=plugin.plugin_id,
                capability_names=sorted_unique(item.name for item in capabilities),
                rationale=assessment.rationale[0],
                evidence_refs=sorted_unique(evidence_refs),
            )
        )
    return tuple(sorted(recommendations, key=lambda item: item.plugin_id.casefold()))


def _invocation_routes(
    plugins: tuple[PluginRecord, ...],
    optimization_goal: str,
) -> tuple[InvocationRoute, ...]:
    if optimization_goal != "cost":
        return ()

    matches: list[tuple[PluginRecord, CapabilityRecord]] = []
    for plugin in plugins:
        if (
            plugin.name.casefold() != LLM_COST_OPTIMIZER_PLUGIN
            or not plugin.installed
            or not plugin.enabled
        ):
            continue
        for capability in plugin.capabilities:
            if capability.name.casefold() == LLM_COST_OPTIMIZER_CAPABILITY and _usable_capability(capability):
                matches.append((plugin, capability))
    if not matches:
        return ()

    plugin, capability = sorted(
        matches,
        key=lambda item: (item[0].plugin_id.casefold(), item[1].capability_id),
    )[0]
    evidence_refs = [
        item.evidence_id
        for item in plugin.evidence
        if item.kind in {"codex-inventory", "plugin-manifest"}
    ]
    evidence_refs.extend(capability.evidence_refs)
    return (
        InvocationRoute.create(
            plugin_id=plugin.plugin_id,
            capability_name=f"{plugin.name}:{capability.name}",
            trigger="an explicitly selected cost-optimization goal",
            invoker="Codex",
            rationale=(
                "Use its cost analysis only for the explicitly requested cost goal, "
                "not as the controller of speed or per-agent reasoning effort. Do not "
                "claim savings without pricing and usage evidence. This exact "
                "capability route does not reclassify its parent plugin or authorize "
                "sibling capabilities."
            ),
            evidence_refs=evidence_refs,
        ),
    )


def build_recommendation_plan(
    plugins: Iterable[PluginRecord],
    repository: RepositoryContext,
    task: str,
    *,
    findings: Iterable[FindingRecord] = (),
    external_evidence: Iterable[EvidenceRecord] = (),
    host_platform: str | None = None,
    optimization_goal: str = "speed",
    standalone_skills: Iterable[SkillRecord] = (),
    requested_skills: Iterable[str] = (),
    standalone_discovery: (
        StandaloneDiscoverySummary | StandaloneDiscoveryResult | None
    ) = None,
) -> RecommendationPlan:
    if optimization_goal not in {"speed", "cost"}:
        raise ValueError(f"unsupported optimization goal: {optimization_goal}")
    ordered_plugins = tuple(sorted(plugins, key=lambda item: item.plugin_id.casefold()))
    ordered_findings = tuple(sorted(findings, key=lambda item: item.finding_id))
    all_evidence: dict[str, EvidenceRecord] = {}
    for plugin in ordered_plugins:
        for item in plugin.evidence:
            all_evidence[item.evidence_id] = item
    for item in repository.evidence:
        all_evidence[item.evidence_id] = item
    for item in external_evidence:
        all_evidence[item.evidence_id] = item

    host = (host_platform or platform.system() or "unknown").casefold()
    preliminary: list[Assessment] = []
    rankings: dict[str, dict[str, object]] = {}
    evidence_values = tuple(all_evidence.values())
    for plugin in ordered_plugins:
        assessment, ranking = _preliminary_assessment(
            plugin,
            repository,
            task,
            ordered_findings,
            evidence_values,
            host,
        )
        preliminary.append(assessment)
        rankings[plugin.plugin_id] = ranking
    groups = _overlap_groups(ordered_plugins, ordered_findings, rankings)
    assessments = _apply_overlap(tuple(preliminary), groups)
    invocation_routes = _invocation_routes(ordered_plugins, optimization_goal)
    scheduling_guidance = (
        build_scheduling_guidance(tokenize(task)) if optimization_goal == "speed" else None
    )
    covered_terms: set[str] = set()
    if invocation_routes:
        covered_terms.update(LLM_COST_ROUTE_TERMS)
    if scheduling_guidance:
        covered_terms.update(SCHEDULING_TERMS)
    recommendations = _minimal_recommendations(
        ordered_plugins,
        assessments,
        rankings,
        excluded_tokens=covered_terms,
        optimization_goal=optimization_goal,
    )

    packaged_skills = plugin_packaged_skills(ordered_plugins)
    standalone_values = tuple(standalone_skills)
    requested_values = tuple(requested_skills)
    assessment_by_plugin = {item.plugin_id: item for item in assessments}
    eligible_plugin_ids = {
        plugin.plugin_id
        for plugin in ordered_plugins
        if plugin.installed
        and plugin.enabled
        and assessment_by_plugin[plugin.plugin_id].classification
        in {"Use now", "Useful on demand"}
    }
    ineligible_skill_ids = {
        skill.skill_id
        for skill in packaged_skills
        if skill.source_identity not in eligible_plugin_ids
    }
    skill_decision = build_skill_decision(
        (*packaged_skills, *standalone_values),
        task,
        requested_skills=requested_values,
        ineligible_skill_ids=ineligible_skill_ids,
    )
    ordered_skills = tuple(item.skill for item in skill_decision.assessments)

    if standalone_values or requested_values:
        suppressed_names = {
            item.name.casefold()
            for item in skill_decision.recommendations
            if item.skill.source_type != "plugin"
        }
        skills_by_qualified_identity = {
            item.qualified_identity: item for item in ordered_skills
        }
        for requested in requested_values:
            if requested.startswith("skill://"):
                requested_skill = skills_by_qualified_identity.get(requested)
                if requested_skill is not None:
                    suppressed_names.add(requested_skill.name.casefold())
            else:
                suppressed_names.add(requested.casefold())
        standalone_coverage: set[str] = set()
        for item in skill_decision.recommendations:
            if item.skill.source_type != "plugin":
                standalone_coverage.update(item.covered_terms)
        task_tokens = set(tokenize(task))
        for skill in packaged_skills:
            coverage = task_tokens & set(tokenize(f"{skill.name} {skill.description}"))
            if coverage and coverage <= standalone_coverage:
                suppressed_names.add(skill.name.casefold())
        suppressed_names.update(
            item.name.casefold() for item in skill_decision.ambiguities
        )
        filtered: list[Recommendation] = []
        for recommendation in recommendations:
            names = tuple(
                name for name in recommendation.capability_names
                if name.casefold() not in suppressed_names
            )
            if names:
                filtered.append(replace(recommendation, capability_names=names))
        recommendations = tuple(filtered)

        by_plugin = {item.plugin_id: item for item in recommendations}
        for skill_recommendation in skill_decision.recommendations:
            skill = skill_recommendation.skill
            if skill.source_type != "plugin" or skill.source_identity not in eligible_plugin_ids:
                continue
            existing = by_plugin.get(skill.source_identity)
            if existing is None:
                by_plugin[skill.source_identity] = Recommendation(
                    plugin_id=skill.source_identity,
                    capability_names=(skill.name,),
                    rationale=skill_recommendation.rationale,
                    evidence_refs=skill.evidence_refs,
                )
            else:
                by_plugin[skill.source_identity] = replace(
                    existing,
                    capability_names=sorted_unique(
                        (*existing.capability_names, skill.name)
                    ),
                    evidence_refs=sorted_unique(
                        (*existing.evidence_refs, *skill.evidence_refs)
                    ),
                )
        recommendations = tuple(sorted(
            by_plugin.values(), key=lambda item: item.plugin_id.casefold(),
        ))

    if standalone_discovery is None:
        standalone_summary = StandaloneDiscoverySummary.create(
            "complete" if standalone_values else "not_configured"
        )
    elif isinstance(standalone_discovery, StandaloneDiscoverySummary):
        standalone_summary = standalone_discovery
    elif isinstance(standalone_discovery, StandaloneDiscoveryResult):
        standalone_summary = standalone_discovery.to_summary()
    else:
        raise TypeError(
            "standalone_discovery must be a StandaloneDiscoverySummary or "
            "StandaloneDiscoveryResult"
        )

    from .rendering import build_session_prompt

    prompt = build_session_prompt(
        task,
        recommendations,
        assessments,
        invocation_routes,
        scheduling_guidance,
        skill_recommendations=skill_decision.recommendations,
        skill_ambiguities=skill_decision.ambiguities,
        discovery_diagnostics=tuple(
            item.to_dict() for item in standalone_summary.diagnostics
        ),
        readiness_notes=tuple(
            f"{plugin.plugin_id}:{cap.name}: {cap.readiness.status} in {cap.readiness.root}; "
            "do not invoke this capability until its runtime path is resolved."
            for plugin in ordered_plugins for cap in plugin.capabilities
            if cap.readiness.status in {"missing_files", "unknown"}
            and set(tokenize(task)) & set(tokenize(f"{cap.name} {cap.description}"))
        ),
    )
    return RecommendationPlan(
        task=task,
        repository=repository,
        plugins=ordered_plugins,
        skills=ordered_skills,
        standalone_discovery=standalone_summary,
        skill_assessments=skill_decision.assessments,
        skill_ambiguities=skill_decision.ambiguities,
        skill_recommendations=skill_decision.recommendations,
        findings=ordered_findings,
        triage=triage_findings(ordered_findings),
        assessments=assessments,
        overlap_groups=groups,
        recommendations=recommendations,
        invocation_routes=invocation_routes,
        generated_prompt=prompt,
        optimization_goal=optimization_goal,
        scheduling_guidance=scheduling_guidance,
        evidence=tuple(all_evidence.values()),
    )
