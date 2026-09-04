"""Plugin Compass handoff-only transport for Compass Builder planning."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Callable, Mapping, Sequence

from ._validation import EFFORT_ORDER
from .models import canonical_json


class HandoffError(ValueError):
    """Plugin Compass could not provide a matching proposal."""


POLICY_VERSION = "plugin-compass.effort-policy.v1"
HANDOFF_FIELDS = {
    "schema_version", "decision_id", "status", "objective", "enforcement",
    "evidence_basis", "task_id", "selected_model", "supported_efforts",
    "support_evidence", "assessment", "acceptance_checks", "recommended_effort",
    "rationale", "dispatch_tool", "dispatch_arguments", "max_reasoning_retries",
    "previous_attempt", "validation_owner", "limitations",
}
ProcessRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _digest(value: Mapping[str, object]) -> str:
    return "sha256:" + sha256(canonical_json(value)).hexdigest()


def build_agent_task(
    story: Mapping[str, object], host: Mapping[str, object], *, index: int
) -> dict[str, object]:
    safe_id = re.sub(r"[^a-z0-9_]", "_", str(story["id"]).casefold()).strip("_")
    task_id = f"builder_{index}_{safe_id}"[:64]
    raw_checks = list(story["acceptanceChecks"])
    checks_digest = _digest({"acceptanceChecks": raw_checks})
    commands_digest = _digest({"validationCommands": list(story["validationCommands"])})
    needs_summary = len(raw_checks) > 32 or any(len(check) > 2000 for check in raw_checks)
    if needs_summary:
        checks = []
        for check in raw_checks[:31]:
            if len(check) <= 1900:
                checks.append(check)
            else:
                item_digest = _digest({"acceptanceCheck": check})
                checks.append(check[:1800] + f" … [full check bound by {item_digest}]")
        checks.append(
            f"Complete acceptance-check set is controller-bound by {checks_digest}; "
            "use the canonical run spec for execution."
        )
    else:
        checks = raw_checks
    if not checks and story["independentReviewPath"] is not None:
        checks.append(f"Independent review: {story['independentReviewPath']}")
    rationale = (
        f"Declared complexity={story['complexity']}, ambiguity={story['ambiguity']}, "
        f"risk={story['risk']}, validation_strength={story['validationStrength']}."
    )
    return {
        "schema_version": "plugin-compass.agent-task.v1",
        "task_id": task_id,
        "task": str(story["description"]),
        "context": (
            f"Story {story['id']}. Write-scope count={len(story['writeScopes'])}; "
            f"bounded preview: {', '.join(story['writeScopes'][:8])}. "
            f"Shared-state mode: {story['sharedState']['mode']}. "
            f"Canonical acceptance digest={checks_digest}; validation-command "
            f"digest={commands_digest}. "
            "The controller owns Git integration and run state. Do not launch other agents."
        ),
        "selected_model": host["selectedModel"],
        "supported_efforts": [
            effort for effort in EFFORT_ORDER if effort in host["supportedEfforts"]
        ],
        "support_evidence": (
            f"Current native capability snapshot {host['captureSource']} captured "
            f"{host['capturedAt']} for Codex {host['codexVersion']}. Canonical story "
            f"digest={_digest(story)}; acceptance={checks_digest}; validation={commands_digest}."
        ),
        "assessment": {
            "complexity": story["complexity"],
            "ambiguity": story["ambiguity"],
            "risk": story["risk"],
            "validation_strength": story["validationStrength"],
            "rationale": rationale,
        },
        "acceptance_checks": checks,
        "delegation_authorized": True,
        "delegation_worthwhile": True,
    }


def resolve_plugin_compass(
    *, explicit_root: Path | None = None,
    inventory: Mapping[str, object] | None = None,
    inventory_base: Path | None = None,
) -> Path:
    """Resolve only an explicit root or an authoritative inventory entry."""
    if explicit_root is not None:
        root = explicit_root.expanduser().resolve(strict=True)
    else:
        if inventory is None:
            raise HandoffError("Plugin Compass requires authoritative inventory or an explicit root")
        matches: list[Mapping[str, object]] = []
        for raw in inventory.get("installed", []):
            if isinstance(raw, Mapping) and raw.get("name") == "plugin-compass":
                if raw.get("installed") is True and raw.get("enabled") is True:
                    matches.append(raw)
        if len(matches) != 1:
            raise HandoffError("authoritative inventory must contain one enabled installed Plugin Compass")
        source = matches[0].get("source")
        path = source.get("path") if isinstance(source, Mapping) else None
        if not isinstance(path, str) or not path.strip():
            raise HandoffError("Plugin Compass inventory entry lacks an authoritative source path")
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            if inventory_base is None:
                raise HandoffError("relative Plugin Compass inventory path requires an inventory base")
            candidate = inventory_base / candidate
        root = candidate.resolve(strict=True)
    manifest = root / ".codex-plugin" / "plugin.json"
    script = root / "scripts" / "plugin_compass.py"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HandoffError("Plugin Compass root lacks a valid plugin manifest") from exc
    if payload.get("name") != "plugin-compass" or not script.is_file():
        raise HandoffError("resolved root is not the Plugin Compass command package")
    return root


def _default_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), check=False, capture_output=True, text=True, encoding="utf-8",
        errors="strict", shell=False, timeout=20,
    )


def invoke_handoff(
    plugin_root: Path,
    agent_task: Mapping[str, object],
    *,
    runner: ProcessRunner = _default_runner,
) -> dict[str, object]:
    """Invoke only Plugin Compass's public ``handoff`` command."""
    with tempfile.TemporaryDirectory(prefix="compass-builder-handoff-") as directory:
        task_file = Path(directory) / "agent-task.json"
        task_file.write_bytes(canonical_json(agent_task))
        argv = (
            sys.executable,
            str(plugin_root / "scripts" / "plugin_compass.py"),
            "handoff", "--task-file", str(task_file), "--format", "json",
        )
        try:
            result = runner(argv)
        except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
            raise HandoffError(f"Plugin Compass handoff command failed: {exc}") from exc
    try:
        output = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise HandoffError("Plugin Compass handoff command did not return JSON") from exc
    if result.returncode != 0:
        status = output.get("status") if isinstance(output, dict) else "unknown"
        raise HandoffError(f"Plugin Compass handoff was gated ({status})")
    if not isinstance(output, dict) or output.get("status") != "proposed":
        raise HandoffError("Plugin Compass handoff was not a successful proposal")
    return output


def bind_handoff(
    agent_task: Mapping[str, object], output: Mapping[str, object], host: Mapping[str, object]
) -> dict[str, str]:
    """Validate a proposal and bind both target task and response in one digest."""
    if not isinstance(output, Mapping) or set(output) != HANDOFF_FIELDS:
        raise HandoffError("Plugin Compass handoff does not match the closed v1 field set")
    for field in (
        "decision_id", "task_id", "selected_model", "support_evidence", "rationale",
        "validation_owner", "limitations",
    ):
        if not isinstance(output[field], str) or not output[field].strip():
            raise HandoffError(f"Plugin Compass handoff field {field} must be non-empty text")
    if output["evidence_basis"] != "caller_supplied_task_and_host_capabilities":
        raise HandoffError("Plugin Compass handoff evidence_basis is mismatched")
    if (
        type(output["max_reasoning_retries"]) is not int
        or output["max_reasoning_retries"] != 1
        or output["previous_attempt"] is not None
    ):
        raise HandoffError("Plugin Compass handoff retry contract is mismatched")
    if output["support_evidence"] != agent_task["support_evidence"]:
        raise HandoffError("Plugin Compass handoff support evidence is mismatched")
    arguments = output["dispatch_arguments"]
    if not isinstance(arguments, Mapping) or set(arguments) != {
        "task_name", "message", "fork_turns", "reasoning_effort", "model"
    }:
        raise HandoffError("Plugin Compass proposed dispatch arguments are malformed")
    if (
        arguments["task_name"] != agent_task["task_id"]
        or arguments["fork_turns"] != "none"
        or arguments["model"] != host["selectedModel"]
        or not isinstance(arguments["message"], str)
        or not arguments["message"].strip()
    ):
        raise HandoffError("Plugin Compass proposed dispatch arguments are mismatched")
    expected = {
        "schema_version": "plugin-compass.handoff.v1",
        "status": "proposed",
        "objective": "fastest_verified_completion",
        "enforcement": "proposal_only",
        "task_id": agent_task["task_id"],
        "selected_model": host["selectedModel"],
        "dispatch_tool": "collaboration.spawn_agent",
    }
    for field, value in expected.items():
        if output.get(field) != value:
            raise HandoffError(f"Plugin Compass handoff field {field} is mismatched")
    if output.get("supported_efforts") != agent_task["supported_efforts"]:
        raise HandoffError("Plugin Compass handoff supported efforts are mismatched")
    if output.get("assessment") != agent_task["assessment"]:
        raise HandoffError("Plugin Compass handoff assessment is mismatched")
    if output.get("acceptance_checks") != agent_task["acceptance_checks"]:
        raise HandoffError("Plugin Compass handoff acceptance checks are mismatched")
    effort = output.get("recommended_effort")
    if effort not in host["supportedEfforts"]:
        raise HandoffError("Plugin Compass recommended an unsupported effort")
    if arguments["reasoning_effort"] != effort:
        raise HandoffError("Plugin Compass dispatch effort does not match its recommendation")
    target_digest = _digest(agent_task)
    envelope = {
        "effortPolicyVersion": POLICY_VERSION,
        "targetTaskDigest": target_digest,
        "handoff": dict(output),
    }
    return {
        "recommendedEffort": str(effort),
        "targetTaskDigest": target_digest,
        "handoffDigest": _digest(envelope),
    }
