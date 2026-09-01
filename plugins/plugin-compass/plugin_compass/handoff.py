"""Pure task-to-native-dispatch proposals. Never calls an agent or a shell."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import stable_id


EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
LEVELS = ("low", "medium", "high")
MAX_TASK_BYTES = 64 * 1024
REQUIRED = {
    "schema_version", "task_id", "task", "context", "selected_model", "supported_efforts",
    "support_evidence", "assessment", "acceptance_checks", "delegation_authorized",
    "delegation_worthwhile",
}


def _object(value: Any, required: set[str], optional: set[str] | None = None) -> dict:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - (optional or set()):
        raise ValueError("task object has missing or unsupported fields")
    return value


def _text(value: Any, field: str, limit: int = 16000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} must be a nonempty bounded string")
    return value.strip()


def _strings(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise ValueError(f"{field} must contain 1..32 nonempty strings")
    return [_text(item, field, 2000) for item in value]


def load_task(path: Path) -> dict:
    try:
        if path.stat().st_size > MAX_TASK_BYTES:
            raise ValueError("agent task exceeds 64 KiB read limit")
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ValueError("Unable to read agent-task JSON") from exc


def build_handoff(value: Any) -> dict[str, Any]:
    spec = _object(value, REQUIRED, {"previous_attempt"})
    if spec["schema_version"] != "plugin-compass.agent-task.v1":
        raise ValueError("unsupported agent-task schema")
    task_id = _text(spec["task_id"], "task_id", 64)
    if not re.fullmatch(r"[a-z0-9_]+", task_id):
        raise ValueError("task_id must contain only lowercase letters, digits and underscores")
    task = _text(spec["task"], "task")
    context = _text(spec["context"], "context")
    model = _text(spec["selected_model"], "selected_model", 200)
    support_evidence = _text(spec["support_evidence"], "support_evidence", 2000)
    supported = _strings(spec["supported_efforts"], "supported_efforts")
    if len(set(supported)) != len(supported):
        raise ValueError("supported_efforts must not contain duplicates")
    if set(supported) - set(EFFORTS):
        raise ValueError("unsupported reasoning effort name; refresh the host capability contract")
    supported = [effort for effort in EFFORTS if effort in supported]
    assessment = _object(spec["assessment"], {"complexity", "ambiguity", "risk", "validation_strength", "rationale"})
    for field in ("complexity", "ambiguity", "risk"):
        if assessment[field] not in LEVELS:
            raise ValueError(f"assessment.{field} must be low, medium or high")
    if assessment["validation_strength"] not in ("decisive", "partial", "none"):
        raise ValueError("validation_strength must be decisive, partial or none")
    explanation = _text(assessment["rationale"], "assessment.rationale", 4000)
    checks = _strings(spec["acceptance_checks"], "acceptance_checks")
    for field in ("delegation_authorized", "delegation_worthwhile"):
        if not isinstance(spec[field], bool):
            raise ValueError(f"{field} must be boolean")

    minimum = max(LEVELS.index(assessment[field]) for field in ("complexity", "ambiguity", "risk"))
    if assessment["validation_strength"] != "decisive":
        minimum = 2
    floor = EFFORTS.index(LEVELS[minimum])
    if "previous_attempt" in spec and spec["previous_attempt"] is None:
        raise ValueError("previous_attempt must be omitted rather than null")
    previous = spec.get("previous_attempt")
    if previous is not None:
        previous = _object(previous, {"selected_model", "reasoning_effort", "reasoning_retries", "failure_kind", "failed_evidence"})
        if model == "inherit":
            raise ValueError("retry requires the exact model ID from the previous dispatch receipt, not inherit")
        if previous["selected_model"] != model:
            raise ValueError("retry must preserve the selected model")
        if previous["reasoning_effort"] not in EFFORTS:
            raise ValueError("previous reasoning effort is unsupported")
        retries = previous["reasoning_retries"]
        if type(retries) is not int or retries not in (0, 1):
            raise ValueError("reasoning_retries must be 0 or 1")
        if previous["failure_kind"] not in ("reasoning", "tool", "permission", "missing_input", "unknown"):
            raise ValueError("unsupported failure_kind")
        _strings(previous["failed_evidence"], "failed_evidence")
        floor = max(floor, EFFORTS.index(previous["reasoning_effort"]) + 1)

    normalized = dict(spec, supported_efforts=supported)
    decision_id = stable_id("handoff", json.dumps(normalized, sort_keys=True, ensure_ascii=False))
    output: dict[str, Any] = {
        "schema_version": "plugin-compass.handoff.v1",
        "decision_id": decision_id,
        "status": "proposed",
        "objective": "fastest_verified_completion",
        "enforcement": "proposal_only",
        "evidence_basis": "caller_supplied_task_and_host_capabilities",
        "task_id": task_id,
        "selected_model": model,
        "supported_efforts": supported,
        "support_evidence": support_evidence,
        "assessment": dict(assessment),
        "acceptance_checks": checks,
        "recommended_effort": None,
        "rationale": explanation,
        "dispatch_tool": "collaboration.spawn_agent",
        "dispatch_arguments": None,
        "max_reasoning_retries": 1,
        "previous_attempt": dict(previous) if previous else None,
        "validation_owner": "Codex controller: run the acceptance checks and inspect actual evidence before accepting.",
        "limitations": "Caller assessments and host support are not independently verified. This proposal grants no authority, executes nothing, and guarantees neither correctness nor fastest latency.",
    }
    gate = None
    if not spec["delegation_authorized"]:
        gate = ("needs_authorization", "Obtain delegation authority; the task file cannot grant it.")
    elif not spec["delegation_worthwhile"]:
        gate = ("keep_local", "Keep this task local; coordination is not justified.")
    elif assessment["validation_strength"] == "none":
        gate = ("needs_validation", "Define an actionable verification or review path before dispatch.")
    elif previous and previous["failure_kind"] != "reasoning":
        gate = ("needs_diagnosis", "Resolve the recorded non-reasoning failure; do not spend more effort on it.")
    elif previous and previous["reasoning_retries"] >= 1:
        gate = ("needs_review", "The one higher-effort reasoning retry is exhausted; seek review or clarification.")
    candidates = [effort for effort in supported if EFFORTS.index(effort) >= floor]
    if not gate and not candidates:
        gate = ("needs_supported_effort", "No supported effort satisfies this task or retry floor; do not silently change models.")
    if gate:
        output["status"], output["rationale"] = gate
        return output

    chosen = candidates[0]
    message = "\n".join([
        f"Task: {task}", "", "Scoped context and boundaries:", context, "",
        "Acceptance checks (report actual commands/results or review evidence):",
        *(f"- {check}" for check in checks), "",
        "Do not weaken acceptance criteria. Report blocked or unavailable checks as unverified. "
        "Return your findings and evidence to the controller; do not launch additional agents.",
    ])
    if previous:
        message += "\n\nPrior failed evidence for this bounded retry:\n" + "\n".join(f"- {item}" for item in previous["failed_evidence"])
    arguments = {"task_name": task_id, "message": message, "fork_turns": "none", "reasoning_effort": chosen}
    if model != "inherit":
        arguments["model"] = model
    output["recommended_effort"] = chosen
    output["dispatch_arguments"] = arguments
    output["rationale"] = f"Minimum justified band={LEVELS[minimum]}; lowest supported effort satisfying the task/retry floor={chosen}. {explanation}"
    return output


def render_handoff(output: dict[str, Any]) -> str:
    lines = ["# Native Codex Handoff (proposal only)", "", f"Status: {output['status']}", f"Decision: {output['decision_id']}", f"Model: {output['selected_model']}; effort: {output['recommended_effort'] or 'not selected'}", "", output["rationale"], "", "Acceptance checks:", *(f"- {check}" for check in output["acceptance_checks"])]
    if output["dispatch_arguments"]:
        lines.extend(["", "Proposed native dispatch arguments:", "", "```json", json.dumps(output["dispatch_arguments"], indent=2, ensure_ascii=False), "```"])
    lines.extend(["", output["validation_owner"], output["limitations"], ""])
    return "\n".join(lines)
