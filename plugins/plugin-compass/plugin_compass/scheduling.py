"""Advisory per-agent effort policy; never dispatches agents or changes models."""

from __future__ import annotations

from typing import Iterable

from .models import SchedulingGuidance


SCHEDULING_TERMS = frozenset({
    "agent", "agents", "delegate", "delegation", "effort", "latency", "model",
    "parallel", "reasoning", "schedule", "scheduler", "scheduling", "speed",
    "subagent", "subagents", "usage", "workload",
})


def build_scheduling_guidance(task_tokens: Iterable[str]) -> SchedulingGuidance | None:
    if not (set(task_tokens) & SCHEDULING_TERMS):
        return None
    return SchedulingGuidance(
        objective="fastest_verified_completion",
        model_policy=(
            "Preserve the user-selected model. Choose the lowest supported reasoning "
            "effort justified by each prospective agent's actual task, not by the "
            "project-wide label or token price. Check the dispatch tool's supported "
            "efforts; never substitute an unsupported value or silently change models."
        ),
        effort_bands={
            "low": (
                "Clear, narrow, low-risk work with little ambiguity and decisive checks: "
                "bounded extraction, formatting, or a mechanical single-owner edit."
            ),
            "medium": (
                "Bounded implementation, debugging, or review with several reasoning "
                "steps and a reliable validation path; also use when evidence is "
                "insufficient to justify low but risk is not high."
            ),
            "high": (
                "Cross-component design, ambiguous requirements, concurrency, security "
                "boundaries, high-impact changes, or difficult-to-verify reasoning. "
                "A high-risk factor raises the floor even when the edit is short."
            ),
            "above_high": (
                "Only for unusually difficult reasoning or a diagnosed reasoning "
                "failure at a lower effort; use a higher tier only if the chosen "
                "model and dispatch tool support it. Not an automatic default."
            ),
        },
        decision_fields=(
            "task_scope", "complexity", "ambiguity", "risk", "acceptance_checks",
            "selected_model", "supported_efforts", "chosen_effort", "rationale",
            "escalation_trigger",
        ),
        validation_gate=(
            "Before dispatch, define task-specific acceptance checks and the required "
            "evidence. Accept the result only after those checks pass; self-reported "
            "confidence and higher effort are not proof. If decisive validation is "
            "unavailable, request review or report the result as unverified."
        ),
        escalation_policy=(
            "On failure, diagnose the cause first. Missing inputs, permission failures, "
            "and tool outages require resolution, not extra reasoning effort. For an "
            "in-scope reasoning failure, increase supported effort on the same model "
            "and retry once with the failing evidence. If still unresolved, stop "
            "automatic retries and seek review or clarification. Do not weaken tests."
        ),
        delegation_policy=(
            "Keep small tasks local. Delegate only authorized, independently useful "
            "work when expected turnaround benefits justify coordination. Codex or "
            "the invoking scheduler owns dispatch and enforces this advisory policy."
        ),
        limitations=(
            "This rubric is an initial heuristic, not a guarantee of correctness or "
            "the globally fastest effort. Optimize total time to a verified result, "
            "including retries and tool time. Compare comparable tasks' measured "
            "completion time and acceptance results before claiming a speedup."
        ),
    )
