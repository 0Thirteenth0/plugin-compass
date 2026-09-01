"""Deterministic sequential/parallel mode and dependency-wave planner."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping

from .doctor import DoctorError
from .handoff import (
    HandoffError, POLICY_VERSION, bind_handoff, build_agent_task, invoke_handoff,
)
from .models import canonical_json, validate_run_bindings, validate_run_spec


class PlanningError(ValueError):
    """The requested plan cannot be produced without weakening a gate."""


EFFORT_UNITS = {"low": 1, "medium": 2, "high": 3, "xhigh": 4, "max": 4, "ultra": 4}
HandoffProvider = Callable[[Mapping[str, object]], Mapping[str, object]]


def _digest(value: Mapping[str, object]) -> str:
    from hashlib import sha256

    return "sha256:" + sha256(canonical_json(value)).hexdigest()


def _ready(stories: list[dict[str, object]], completed: set[str]) -> list[dict[str, object]]:
    """Return the dependency-ready contiguous prefix required by the v1 wire order."""
    ready: list[dict[str, object]] = []
    for story in stories:
        if story["id"] in completed:
            continue
        if not set(story["dependsOn"]) <= completed:
            break
        ready.append(story)
    return ready


def _overlap(stories: list[dict[str, object]]) -> bool:
    seen: list[tuple[str, ...]] = []
    for story in stories:
        for raw in story["writeScopes"]:
            parts = tuple(part.casefold() for part in str(raw).replace("\\", "/").split("/"))
            if any(parts[:len(old)] == old or old[:len(parts)] == parts for old in seen):
                return True
            seen.append(parts)
    return False


def _parallel_gates(
    spec: dict[str, object], doctor: Mapping[str, object],
    ready: list[dict[str, object]], bindings: Mapping[str, Mapping[str, str]],
    *, prior_wave_failed: bool,
) -> list[str]:
    host = doctor["hostCapabilities"]
    gates: list[str] = []
    if len(ready) < 2:
        gates.append("fewer than two dependency-ready stories")
    if not doctor["workingTreeClean"]:
        gates.append("working tree is dirty")
    missing_support = [name for name, value in host["supports"].items() if not value]
    if missing_support:
        gates.append("required host support is unavailable: " + ", ".join(sorted(missing_support)))
    if prior_wave_failed:
        gates.append("a prior wave failed")
    ceiling = min(spec["hostConcurrencyCeiling"], spec["userConcurrencyCeiling"])
    if ceiling < 2:
        gates.append("host/user concurrency ceiling is below two")
    effective_width = min(2, ceiling, max(1, len(ready)))
    prospective = _waves(spec["stories"], effective_width)
    by_id = {story["id"]: story for story in spec["stories"]}
    for wave in prospective:
        wave_stories = [by_id[story_id] for story_id in wave["storyIds"]]
        if any(story["sharedState"]["mode"] == "mutates" for story in wave_stories):
            gates.append("a planned story mutates shared state")
        if any(
            story["validationStrength"] != "decisive"
            or not (
                (story["acceptanceChecks"] and story["validationCommands"])
                or story["independentReviewPath"] is not None
            )
            for story in wave_stories
        ):
            gates.append("a planned story lacks decisive actionable validation")
        if _overlap(wave_stories):
            gates.append("planned wave write scopes overlap under Windows normalization")
        if len(wave_stories) >= 2:
            units = [
                EFFORT_UNITS[bindings[story["id"]]["recommendedEffort"]]
                for story in wave_stories
            ]
            if sum(units) - max(units) < 2:
                gates.append("coordination benefit is below coordination-policy.v1 threshold 2")
    return list(dict.fromkeys(gates))


def _waves(stories: list[dict[str, object]], width: int) -> list[dict[str, object]]:
    completed: set[str] = set()
    waves: list[dict[str, object]] = []
    while len(completed) < len(stories):
        ready = _ready(stories, completed)
        if not ready:
            raise PlanningError("dependency graph has no ready story")
        selected = ready[:width]
        waves.append({"waveIndex": len(waves), "storyIds": [story["id"] for story in selected]})
        completed.update(story["id"] for story in selected)
    return waves


def build_plan(
    run_spec: Mapping[str, object],
    doctor: Mapping[str, object],
    *,
    requested_mode: str,
    plugin_compass_root: Path,
    handoff_provider: HandoffProvider | None = None,
    prior_wave_failed: bool = False,
) -> dict[str, object]:
    """Create a byte-deterministic plan after every advisory and safety gate."""
    try:
        spec = validate_run_spec(run_spec)
    except ValueError as exc:
        raise PlanningError(str(exc)) from exc
    if requested_mode not in {"auto", "sequential", "parallel"}:
        raise PlanningError("requested mode must be auto, sequential, or parallel")
    if spec["mode"] != "auto" and requested_mode != spec["mode"]:
        raise PlanningError("requested mode does not match the immutable explicit run-spec mode")
    if spec["effortPolicyVersion"] != POLICY_VERSION:
        raise PlanningError("run spec names an unsupported Plugin Compass effort policy version")
    host = doctor.get("hostCapabilities")
    if not isinstance(host, Mapping):
        raise PlanningError("doctor report lacks validated host capabilities")
    if doctor.get("resolvedBaseSha") != spec["baseSha"]:
        raise PlanningError("doctor report resolves a different immutable base SHA")
    provider = handoff_provider or (lambda task: invoke_handoff(plugin_compass_root, task))
    bindings: dict[str, dict[str, str]] = {}
    for index, story in enumerate(spec["stories"]):
        task = build_agent_task(story, host, index=index)
        try:
            proposal = provider(task)
            bindings[story["id"]] = bind_handoff(task, proposal, host)
        except HandoffError as exc:
            raise PlanningError(f"story {story['id']} handoff failed closed: {exc}") from exc
    ready = _ready(spec["stories"], set())
    gates = _parallel_gates(
        spec, doctor, ready, bindings, prior_wave_failed=prior_wave_failed
    )
    if requested_mode == "parallel" and gates:
        raise PlanningError("explicit parallel is unavailable: " + "; ".join(gates))
    if requested_mode == "sequential":
        mode = "sequential"
        reasons = ["Sequential mode was explicitly requested."]
    elif requested_mode == "parallel":
        mode = "parallel"
        reasons = ["Parallel mode passed every coordination-policy.v1 safety gate."]
    elif gates:
        mode = "sequential"
        reasons = [f"Auto selected sequential: {reason}." for reason in gates]
    else:
        mode = "parallel"
        reasons = ["Auto selected parallel: every safety gate passed and coordination benefit is at least 2."]
    ceiling = min(2, spec["hostConcurrencyCeiling"], spec["userConcurrencyCeiling"])
    concurrency = 1 if mode == "sequential" else min(ceiling, len(ready))
    stories = [
        {
            "storyId": story["id"],
            "branch": f"cb/{spec['runId']}/{story['id']}",
            "recommendedEffort": bindings[story["id"]]["recommendedEffort"],
            "handoffDigest": bindings[story["id"]]["handoffDigest"],
        }
        for story in spec["stories"]
    ]
    plan = {
        "schemaVersion": "compass-builder.wave-plan.v1",
        "runId": spec["runId"],
        "baseSha": spec["baseSha"],
        "integrationBranch": spec["integrationBranch"],
        "integrationExpectedSha": spec["integrationExpectedSha"],
        "normalizedInputDigest": _digest(spec),
        "hostEvidenceDigest": doctor["hostEvidenceDigest"],
        "effortPolicyVersion": POLICY_VERSION,
        "mode": mode,
        "reasons": reasons,
        "concurrency": concurrency,
        "stories": stories,
        "waves": _waves(spec["stories"], concurrency),
    }
    try:
        _spec, normalized, _state = validate_run_bindings(
            spec, plan, host_capabilities=host,
            planning_timestamp=str(doctor["planningTimestamp"]),
        )
    except ValueError as exc:
        raise PlanningError(str(exc)) from exc
    return normalized
