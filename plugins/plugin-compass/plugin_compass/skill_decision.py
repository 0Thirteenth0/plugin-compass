"""Source-neutral standalone and packaged skill assessment and selection."""

from __future__ import annotations

import re
from collections import defaultdict
from itertools import combinations
from typing import Iterable

from .skill_models import (
    SkillAmbiguity,
    SkillAssessment,
    SkillDecisionResult,
    SkillRecord,
    SkillRecommendation,
)
from .models import sorted_unique


TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    "a", "an", "and", "app", "assistant", "before", "by", "choose", "codex",
    "for", "from", "in", "new", "of", "on", "or", "plugin", "plugins", "skill",
    "skills", "the", "to", "tool", "tools", "use", "using", "with",
}
USABLE_READINESS = {"not_declared", "files_present"}
USABLE_METADATA = {"complete"}
USABLE_TRUST = {"not_assessed", "trusted"}
EXACT_COVER_MAX_CANDIDATES = 18
EXACT_COVER_MAX_SEARCH_STATES = 50_000


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in TOKEN_PATTERN.findall(text.casefold())
        if len(token) > 1 and token not in STOPWORDS
    }


def _ordered_unique(skills: Iterable[SkillRecord]) -> tuple[SkillRecord, ...]:
    by_id: dict[str, SkillRecord] = {}
    for skill in skills:
        previous = by_id.get(skill.skill_id)
        if previous is not None and previous != skill:
            raise ValueError(
                f"conflicting skill identity: {skill.qualified_identity}"
            )
        by_id[skill.skill_id] = skill
    return tuple(sorted(
        by_id.values(),
        key=lambda item: (
            item.qualified_identity.casefold(), item.qualified_identity,
        ),
    ))


def _total_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values), key=lambda value: (value.casefold(), value)))


def _automatic_quality_key(
    skill: SkillRecord,
    rankings: dict[str, dict[str, object]],
) -> tuple[object, ...]:
    ranking = rankings[skill.skill_id]
    return (
        -int(bool(ranking["exact"])),
        -int(ranking["trust"]),
        -int(ranking["metadata"]),
        -int(ranking["readiness"]),
        int(ranking["size"]),
        skill.qualified_identity.casefold(),
        skill.qualified_identity,
    )


def _exact_automatic_cover(
    candidates: Iterable[SkillRecord],
    target: set[str],
    rankings: dict[str, dict[str, object]],
) -> tuple[SkillRecord, ...]:
    """Return a bounded exact minimum-cardinality cover or fail explicitly."""
    by_coverage: dict[frozenset[str], SkillRecord] = {}
    for skill in candidates:
        coverage = frozenset(set(rankings[skill.skill_id]["coverage"]) & target)
        if not coverage:
            continue
        previous = by_coverage.get(coverage)
        if previous is None or _automatic_quality_key(
            skill, rankings,
        ) < _automatic_quality_key(previous, rankings):
            by_coverage[coverage] = skill
    reduced = tuple(sorted(
        by_coverage.values(),
        key=lambda item: _automatic_quality_key(item, rankings),
    ))
    instruction = (
        "automatic exact skill-cover search exceeds its safety bound; "
        "choose exact qualified identities with --select-skill"
    )
    if len(reduced) > EXACT_COVER_MAX_CANDIDATES:
        raise ValueError(instruction)

    states = 0
    for size in range(1, len(reduced) + 1):
        best: tuple[SkillRecord, ...] | None = None
        best_key: tuple[tuple[object, ...], ...] | None = None
        for choice in combinations(reduced, size):
            states += 1
            if states > EXACT_COVER_MAX_SEARCH_STATES:
                raise ValueError(instruction)
            covered: set[str] = set()
            for skill in choice:
                covered.update(rankings[skill.skill_id]["coverage"])
            if not target <= covered:
                continue
            choice_key = tuple(
                _automatic_quality_key(skill, rankings) for skill in choice
            )
            if best_key is None or choice_key < best_key:
                best = choice
                best_key = choice_key
        if best is not None:
            return best
    if target:
        raise ValueError(
            "automatic exact skill-cover search found no complete cover; "
            "choose exact qualified identities with --select-skill"
        )
    return ()


def _assessment(
    skill: SkillRecord,
    task_tokens: set[str],
    ineligible_skill_ids: set[str],
) -> tuple[SkillAssessment, dict[str, object]]:
    name_tokens = _tokens(skill.name)
    skill_tokens = _tokens(f"{skill.name} {skill.description}")
    matched = task_tokens & skill_tokens
    exact = bool(name_tokens) and name_tokens <= task_tokens
    relevance = (
        "high" if exact or len(matched) >= 2
        else "medium" if matched
        else "low" if task_tokens
        else "unknown"
    )
    gates: list[str] = []
    if skill.trust_status not in USABLE_TRUST:
        gates.append(f"skill trust status is {skill.trust_status}")
    if skill.metadata_status not in USABLE_METADATA:
        gates.append(f"skill metadata is {skill.metadata_status}")
    if skill.readiness.status not in USABLE_READINESS:
        gates.append(f"skill readiness is {skill.readiness.status}")
    if skill.skill_id in ineligible_skill_ids:
        gates.append("parent plugin is unavailable, disabled, or gated")
    if gates:
        classification = "Blocked or untrusted"
    elif relevance == "high":
        classification = "Use now"
    elif relevance == "medium":
        classification = "Useful on demand"
    elif relevance == "low":
        classification = "Irrelevant to this project"
    else:
        classification = "Unknown or insufficient evidence"
    rationale = (
        f"Relevance is {relevance}: matched task terms={len(matched)}; "
        f"exact skill-name match={str(exact).lower()}.",
        (
            f"Source is {skill.source_type}/{skill.source_identity}; trust is "
            f"{skill.trust_status}; metadata is {skill.metadata_status}; readiness is "
            f"{skill.readiness.status}."
        ),
    )
    if gates:
        rationale += ("Hard gates: " + "; ".join(sorted(gates)) + ".",)
    assessment = SkillAssessment(
        skill=skill,
        classification=classification,
        dimensions={
            "repository_and_task_relevance": relevance,
            "trust_and_security": skill.trust_status,
            "metadata_completeness": skill.metadata_status,
            "execution_readiness": skill.readiness.status,
        },
        hard_gates=sorted_unique(gates),
        rationale=rationale,
    )
    return assessment, {
        "eligible": not gates,
        "coverage": matched,
        "exact": exact,
        "trust": 2 if skill.trust_status.casefold() == "trusted" else 1,
        "metadata": 2 if skill.metadata_status == "complete" else 1,
        "readiness": 2 if skill.readiness.status == "files_present" else 1,
        "size": len(skill_tokens),
    }


def build_skill_decision(
    skills: Iterable[SkillRecord],
    task: str,
    *,
    requested_skills: Iterable[str] = (),
    ineligible_skill_ids: Iterable[str] = (),
) -> SkillDecisionResult:
    """Assess and choose a deterministic minimal set without invoking any skill."""
    ordered = _ordered_unique(skills)
    task_tokens = _tokens(task)
    ineligible = set(ineligible_skill_ids)
    assessments: list[SkillAssessment] = []
    rankings: dict[str, dict[str, object]] = {}
    for skill in ordered:
        assessment, ranking = _assessment(skill, task_tokens, ineligible)
        assessments.append(assessment)
        rankings[skill.skill_id] = ranking

    by_name: dict[str, list[SkillRecord]] = defaultdict(list)
    by_qualified = {item.qualified_identity: item for item in ordered}
    for skill in ordered:
        by_name[skill.name.casefold()].append(skill)

    explicit: set[str] = set()
    ambiguous_names: set[str] = set()
    resolved_names: set[str] = set()
    for requested in _total_unique(requested_skills):
        if requested.startswith("skill://"):
            match = by_qualified.get(requested)
            if match is None:
                raise ValueError(f"unknown skill selection: {requested}")
            explicit.add(match.skill_id)
            resolved_names.add(match.name.casefold())
            continue
        matches = by_name.get(requested.casefold(), [])
        if not matches:
            raise ValueError(f"unknown skill selection: {requested}")
        if len(matches) > 1:
            ambiguous_names.add(requested.casefold())
        else:
            explicit.add(matches[0].skill_id)
            resolved_names.add(matches[0].name.casefold())

    for name, matches in by_name.items():
        if name in resolved_names or len(matches) < 2:
            continue
        name_tokens = _tokens(matches[0].name)
        relevant_collision = any(
            bool(rankings[item.skill_id]["coverage"])
            and next(
                assessment.classification
                for assessment in assessments
                if assessment.skill_id == item.skill_id
            ) == "Use now"
            for item in matches
        )
        if (
            name in ambiguous_names
            or (name_tokens and name_tokens <= task_tokens)
            or relevant_collision
        ):
            ambiguous_names.add(name)

    ambiguities = tuple(
        SkillAmbiguity(
            name=min((item.name for item in by_name[name]), key=lambda value: (value.casefold(), value)),
            candidates=tuple(sorted(
                (item.qualified_identity for item in by_name[name]),
                key=lambda value: (value.casefold(), value),
            )),
            rationale=(
                "Bare skill name is ambiguous; select one exact qualified identity. "
                "No candidate was selected."
            ),
        )
        for name in sorted(ambiguous_names, key=lambda value: (value.casefold(), value))
    )

    selected: list[tuple[SkillRecord, set[str], bool]] = []
    selected_ids: set[str] = set()
    for skill in ordered:
        if skill.skill_id in explicit and bool(rankings[skill.skill_id]["eligible"]):
            selected.append((skill, set(rankings[skill.skill_id]["coverage"]), True))
            selected_ids.add(skill.skill_id)

    candidates = [
        skill for skill in ordered
        if skill.skill_id not in selected_ids
        and skill.name.casefold() not in resolved_names
        and skill.name.casefold() not in ambiguous_names
        and bool(rankings[skill.skill_id]["eligible"])
        and next(
            item.classification for item in assessments
            if item.skill_id == skill.skill_id
        ) == "Use now"
    ]
    coverable: set[str] = set()
    for skill in candidates:
        coverable.update(rankings[skill.skill_id]["coverage"])
    uncovered = coverable - set().union(
        *(coverage for _skill, coverage, _explicit in selected),
    ) if selected else coverable
    for winner in _exact_automatic_cover(candidates, uncovered, rankings):
        coverage = set(rankings[winner.skill_id]["coverage"]) & uncovered
        selected.append((winner, coverage, False))
        selected_ids.add(winner.skill_id)
        uncovered -= coverage

    recommendations = tuple(sorted(
        (
            SkillRecommendation(
                skill=skill,
                rationale=(
                    "Selected by exact qualified or unique-name request."
                    if requested
                    else "Selected by deterministic minimal task-term coverage."
                ),
                covered_terms=sorted_unique(coverage),
            )
            for skill, coverage, requested in selected
        ),
        key=lambda item: (
            item.qualified_identity.casefold(), item.qualified_identity,
        ),
    ))
    return SkillDecisionResult(
        assessments=tuple(sorted(assessments, key=lambda item: item.skill_id)),
        ambiguities=ambiguities,
        recommendations=recommendations,
    )
