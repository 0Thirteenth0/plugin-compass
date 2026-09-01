"""Immutable, worktree-bound Codex launch preparation.

This module constructs and validates launch material. It deliberately contains no
subprocess call: the durable controller owns the later decision to start a worker.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence

from .git_environment import GitEnvironment, GitEnvironmentError, validate_git_environment
from ._validation import branch, identifier, run_id
from .models import (
    canonical_json, validate_host_capabilities_at, validate_run_bindings,
)


class LaunchError(ValueError):
    """Launch material violates a capability, policy, or safety binding."""


@dataclass(frozen=True)
class FailureEvidence:
    """Controller-owned classification of a completed worker attempt."""

    kind: str
    evidence_digest: str
    source: str = "controller"


@dataclass(frozen=True)
class FailureDisposition:
    """Whether a failure is blocked or eligible for the sole reasoning retry."""

    status: str
    retry_effort: str | None
    reason: str


@dataclass(frozen=True)
class PreparedLaunch:
    """Validated argv/stdin/environment and the immutable launch record."""

    argv: tuple[str, ...]
    stdin: str
    environment: Mapping[str, str]
    record: Mapping[str, object]


SCHEMA_VERSION = "compass-builder.launch-record.v1"
WORKER_OUTPUT_VERSION = "compass-builder.worker-output.v1"
REASONING_CONFIG_KEY = "model_reasoning_effort"
EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max", "ultra")
FAILURE_KINDS = {
    "reasoning", "startup", "model", "config", "tool", "permission",
    "missing-input", "validation", "other",
}
NON_REASONING_BLOCKERS = FAILURE_KINDS - {"reasoning"}
MAX_PROMPT_BYTES = 65_536
BUNDLED_WORKER_SCHEMA = (
    Path(__file__).resolve().parents[1] / "schemas" / "worker-output.schema.json"
)
EXPECTED_WORKER_SCHEMA_DIGEST = (
    "sha256:92e2f2e58ccd8790247722e889644ce02845ca410f01c7bc56cc41d4fe751080"
)
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
_RECORD_FIELDS = {
    "schemaVersion", "runId", "storyId", "branch", "attempt", "worktree",
    "exactModel", "effort", "initialRecommendedEffort", "reasoningConfigKey",
    "reasoningConfigEvidenceDigest", "handoffDigest", "hostEvidenceDigest",
    "workerOutputSchemaPath", "workerOutputSchemaDigest", "promptDigest",
    "gitEnvironmentDigest", "argv", "previousLaunchDigest", "retryEvidenceDigest",
}


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _digest_json(value: Mapping[str, object]) -> str:
    return _digest_bytes(canonical_json(value))


def _require_digest(value: object, field: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise LaunchError(f"{field} must be a sha256: digest with 64 lowercase hex digits")
    return value


def _require_absolute_path(value: object, field: str, *, existing: bool) -> Path:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 1024
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise LaunchError(
            f"{field} must be bounded trimmed absolute-path text without controls"
        )
    path = Path(value)
    if not path.is_absolute():
        raise LaunchError(f"{field} must be absolute")
    try:
        resolved = path.resolve(strict=existing)
    except OSError as exc:
        raise LaunchError(f"{field} cannot be resolved: {exc}") from exc
    resolved_text = str(resolved)
    if (
        len(resolved_text) > 1024
        or any(ord(character) < 32 or ord(character) == 127 for character in resolved_text)
    ):
        raise LaunchError(f"{field} canonical path exceeds its local syntax bounds")
    return resolved


def _expected_argv(record: Mapping[str, object]) -> tuple[str, ...]:
    return (
        "codex", "exec", "-C", str(record["worktree"]),
        "-m", str(record["exactModel"]),
        "-c", f'{record["reasoningConfigKey"]}="{record["effort"]}"',
        "--disable", "multi_agent", "--ephemeral",
        "-s", "workspace-write", "--approve-for-me", "--json",
        "--output-schema", str(record["workerOutputSchemaPath"]), "-",
    )


def validate_launch_record(value: Mapping[str, object]) -> dict[str, object]:
    """Validate a closed launch record and its exact no-shell argv contract."""

    if not isinstance(value, Mapping):
        raise LaunchError("launch record must be an object mapping")
    record = copy.deepcopy(dict(value))
    if set(record) != _RECORD_FIELDS:
        missing = sorted(_RECORD_FIELDS - set(record))
        extra = sorted(set(record) - _RECORD_FIELDS)
        raise LaunchError(f"launch record field set is not closed (missing={missing}, extra={extra})")
    if record["schemaVersion"] != SCHEMA_VERSION:
        raise LaunchError(f"schemaVersion must be {SCHEMA_VERSION!r}")
    try:
        run_id(record["runId"], "$.runId")
        identifier(record["storyId"], "$.storyId")
        branch(record["branch"], "$.branch")
    except ValueError as exc:
        raise LaunchError(str(exc)) from exc
    if not isinstance(record["exactModel"], str) or not _MODEL_RE.fullmatch(record["exactModel"]):
        raise LaunchError("exactModel must be one exact, interpolation-free model identifier")
    if record["reasoningConfigKey"] != REASONING_CONFIG_KEY:
        raise LaunchError("reasoningConfigKey is not the locally verified Codex key")
    if record["effort"] not in EFFORT_ORDER or record["initialRecommendedEffort"] not in EFFORT_ORDER:
        raise LaunchError("effort values must be supported Codex reasoning efforts")
    if type(record["attempt"]) is not int or record["attempt"] not in (1, 2):
        raise LaunchError("attempt must be 1 or 2")
    worktree = _require_absolute_path(record["worktree"], "worktree", existing=False)
    schema_path = _require_absolute_path(
        record["workerOutputSchemaPath"], "workerOutputSchemaPath", existing=False
    )
    record["worktree"] = str(worktree)
    record["workerOutputSchemaPath"] = str(schema_path)
    for field in (
        "reasoningConfigEvidenceDigest", "handoffDigest", "hostEvidenceDigest",
        "workerOutputSchemaDigest", "promptDigest", "gitEnvironmentDigest",
    ):
        _require_digest(record[field], field)
    # Replay validation must inspect current schema bytes, not merely trust the path
    # and digest recorded when launch material was first prepared.
    current_schema_path, current_schema_digest = _load_worker_schema(schema_path)
    if schema_path != current_schema_path:
        raise LaunchError("workerOutputSchemaPath is not the bundled Compass Builder schema")
    if record["workerOutputSchemaDigest"] != current_schema_digest:
        raise LaunchError("workerOutputSchemaDigest does not bind the current bundled schema")
    nullable = ("previousLaunchDigest", "retryEvidenceDigest")
    for field in nullable:
        if record[field] is not None:
            _require_digest(record[field], field)
    if record["attempt"] == 1:
        if record["effort"] != record["initialRecommendedEffort"]:
            raise LaunchError("first attempt must use the planner-bound recommended effort")
        if any(record[field] is not None for field in nullable):
            raise LaunchError("first attempt cannot claim retry evidence")
    else:
        if any(record[field] is None for field in nullable):
            raise LaunchError("second attempt requires previous launch and retry evidence digests")
        if EFFORT_ORDER.index(record["effort"]) <= EFFORT_ORDER.index(record["initialRecommendedEffort"]):
            raise LaunchError("second attempt must use a higher reasoning effort")
    argv = record["argv"]
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise LaunchError("argv must be an array of text arguments")
    expected = _expected_argv(record)
    if tuple(argv) != expected:
        raise LaunchError("argv differs from the exact bounded no-shell Codex contract")
    forbidden = {
        "--dangerously-bypass-approvals-and-sandbox", "--bypass-sandbox",
        "--bypass-approval", "--add-dir", "--writable-root", "--full-auto",
    }
    if forbidden.intersection(argv):
        raise LaunchError("argv contains a forbidden authorization or write-scope bypass")
    return record


def validate_worker_output(value: Mapping[str, object]) -> dict[str, object]:
    """Validate structured worker output without treating it as Git evidence."""

    fields = {"schemaVersion", "status", "summary", "acceptanceChecks", "blocker"}
    if not isinstance(value, Mapping) or set(value) != fields:
        raise LaunchError("worker output must match the closed v1 field set")
    output = copy.deepcopy(dict(value))
    if output["schemaVersion"] != WORKER_OUTPUT_VERSION:
        raise LaunchError(f"worker output schemaVersion must be {WORKER_OUTPUT_VERSION!r}")
    if output["status"] not in {"succeeded", "failed", "blocked"}:
        raise LaunchError("worker output status is unsupported")
    summary = output["summary"]
    if not isinstance(summary, str) or summary != summary.strip() or not summary or len(summary) > 4000:
        raise LaunchError("worker output summary must be bounded trimmed text")
    checks = output["acceptanceChecks"]
    if not isinstance(checks, list) or len(checks) > 128:
        raise LaunchError("worker output acceptanceChecks must be a bounded array")
    for index, check in enumerate(checks):
        expected = {"check", "status", "evidence"}
        if not isinstance(check, dict) or set(check) != expected:
            raise LaunchError(f"acceptanceChecks[{index}] does not match the closed result shape")
        if check["status"] not in {"passed", "failed", "not-run"}:
            raise LaunchError(f"acceptanceChecks[{index}].status is unsupported")
        for field, maximum in (("check", 2000), ("evidence", 4000)):
            text = check[field]
            if not isinstance(text, str) or text != text.strip() or not text or len(text) > maximum:
                raise LaunchError(f"acceptanceChecks[{index}].{field} must be bounded trimmed text")
    blocker = output["blocker"]
    if output["status"] == "succeeded":
        if blocker is not None or any(check["status"] != "passed" for check in checks):
            raise LaunchError("succeeded output requires no blocker and only passed checks")
    elif not isinstance(blocker, str) or blocker != blocker.strip() or not blocker or len(blocker) > 4000:
        raise LaunchError("failed or blocked output requires a bounded blocker")
    return output


def build_worker_prompt(
    run_spec: Mapping[str, object], *, story_id: str
) -> str:
    """Build a deterministic prompt solely from the immutable run story."""

    stories = run_spec.get("stories")
    if not isinstance(stories, list):
        raise LaunchError("run spec lacks ordered stories")
    matches = [story for story in stories if isinstance(story, Mapping) and story.get("id") == story_id]
    if len(matches) != 1:
        raise LaunchError(f"story {story_id!r} is not uniquely present in the run spec")
    story = matches[0]
    scopes = "\n".join(f"- {scope}" for scope in story["writeScopes"])
    acceptance = "\n".join(f"- {check}" for check in story["acceptanceChecks"])
    commands = "\n".join(f"- {command}" for command in story["validationCommands"])
    prompt = (
        f"Implement Compass Builder run {run_spec['runId']} story {story_id}.\n\n"
        f"Task:\n{story['description']}\n\n"
        f"Write only within these repository-relative scopes:\n{scopes}\n\n"
        f"Acceptance checks:\n{acceptance or '- Use the declared independent review path.'}\n\n"
        f"Validation commands:\n{commands or '- No worker command; preserve evidence for controller review.'}\n\n"
        "The controller owns run state, worktree lifecycle, and integration. "
        "Do not launch child workers or agents. Do not edit controller state. "
        "Return only output matching the supplied worker JSON schema.\n"
    )
    if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise LaunchError(f"bounded worker prompt exceeds {MAX_PROMPT_BYTES} UTF-8 bytes")
    return prompt


def classify_failure(
    previous_launch: Mapping[str, object],
    evidence: FailureEvidence,
    supported_efforts: Sequence[str],
) -> FailureDisposition:
    """Return the only allowed retry decision from controller-owned evidence."""

    previous = validate_launch_record(previous_launch)
    if evidence.source != "controller":
        return FailureDisposition("blocked", None, "retry evidence is not controller-owned")
    if evidence.kind not in FAILURE_KINDS:
        return FailureDisposition("blocked", None, "failure kind is unsupported")
    try:
        _require_digest(evidence.evidence_digest, "failure evidence")
    except LaunchError as exc:
        return FailureDisposition("blocked", None, str(exc))
    if evidence.kind in NON_REASONING_BLOCKERS:
        return FailureDisposition(
            "blocked", None, f"{evidence.kind} failure cannot raise reasoning effort"
        )
    if previous["attempt"] != 1:
        return FailureDisposition("blocked", None, "the sole reasoning retry is already consumed")
    supported = [effort for effort in EFFORT_ORDER if effort in supported_efforts]
    current = str(previous["effort"])
    higher = [effort for effort in supported if EFFORT_ORDER.index(effort) > EFFORT_ORDER.index(current)]
    if not higher:
        return FailureDisposition("blocked", None, "no higher effort is proven supported")
    return FailureDisposition("retry", higher[0], "controller evidence identifies a reasoning failure")


def _load_worker_schema(path: Path) -> tuple[Path, str]:
    try:
        resolved = path.resolve(strict=True)
        bundled = BUNDLED_WORKER_SCHEMA.resolve(strict=True)
    except OSError as exc:
        raise LaunchError("bundled worker output schema is missing or unreadable") from exc
    if resolved != bundled:
        raise LaunchError("worker output schema must be the bundled Compass Builder schema")
    if not resolved.is_file():
        raise LaunchError("worker output schema must be a regular file")
    try:
        raw = resolved.read_bytes()
        schema = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LaunchError("worker output schema is not valid UTF-8 JSON") from exc
    version = (
        schema.get("properties", {}).get("schemaVersion", {}).get("const")
        if isinstance(schema, dict) else None
    )
    if version != WORKER_OUTPUT_VERSION:
        raise LaunchError("worker output schema does not bind the supported v1 output")
    if (
        set(schema) != {
            "$schema", "$id", "title", "type", "additionalProperties",
            "required", "properties", "$defs", "allOf",
        }
        or schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or set(schema.get("required", [])) != set(schema.get("properties", {}))
        or schema.get("$defs", {}).get("check", {}).get("additionalProperties") is not False
    ):
        raise LaunchError("bundled worker output schema is not the expected closed shape")
    canonical = (
        json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    schema_digest = _digest_bytes(canonical)
    if schema_digest != EXPECTED_WORKER_SCHEMA_DIGEST:
        raise LaunchError("bundled worker output schema canonical digest is unexpected")
    return resolved, schema_digest


def _prepare(
    run_spec: Mapping[str, object],
    wave_plan: Mapping[str, object],
    host_capabilities: Mapping[str, object],
    *,
    planning_timestamp: str,
    story_id: str,
    worktree: Path,
    worker_schema: Path,
    reasoning_config_key: str,
    reasoning_config_evidence_digest: str,
    git_environment: GitEnvironment,
    effort: str,
    attempt: int,
    previous_launch_digest: str | None,
    retry_evidence_digest: str | None,
) -> PreparedLaunch:
    try:
        host = validate_host_capabilities_at(host_capabilities, planning_timestamp)
        spec, plan, _state = validate_run_bindings(
            run_spec, wave_plan, host_capabilities=host,
            planning_timestamp=planning_timestamp,
        )
    except ValueError as exc:
        raise LaunchError(f"execution bindings are not valid: {exc}") from exc
    native_reasoning = host["reasoningConfig"]
    if reasoning_config_key != native_reasoning["key"]:
        raise LaunchError("reasoning config key does not match native host proof")
    if reasoning_config_evidence_digest != native_reasoning["evidenceDigest"]:
        raise LaunchError("reasoning config evidence does not match native host proof")
    try:
        validate_git_environment(git_environment)
    except GitEnvironmentError as exc:
        raise LaunchError(f"Git environment is not controller-owned: {exc}") from exc
    planned = [story for story in plan["stories"] if story["storyId"] == story_id]
    if len(planned) != 1:
        raise LaunchError(f"story {story_id!r} is not uniquely planner-bound")
    planned_story = planned[0]
    if effort not in host["supportedEfforts"]:
        raise LaunchError("launch effort is not proven by native host capabilities")
    exact_model = spec["exactModel"]
    if exact_model != host["selectedModel"]:
        raise LaunchError("exact launch model differs from native selected model")
    if not isinstance(exact_model, str) or not _MODEL_RE.fullmatch(exact_model):
        raise LaunchError("exact launch model is not an interpolation-free identifier")
    try:
        resolved_worktree = worktree.resolve(strict=True)
    except (AttributeError, OSError) as exc:
        raise LaunchError("worktree path is missing, unreadable, or invalid") from exc
    if not resolved_worktree.is_dir():
        raise LaunchError("worktree path must be an existing directory")
    # Task 4 binds this path into argv/record only. Controller registration and
    # lifecycle verification are intentionally owned by Task 5.
    resolved_schema, schema_digest = _load_worker_schema(worker_schema)
    prompt = build_worker_prompt(spec, story_id=story_id)
    record: dict[str, object] = {
        "schemaVersion": SCHEMA_VERSION,
        "runId": spec["runId"],
        "storyId": story_id,
        "branch": planned_story["branch"],
        "attempt": attempt,
        "worktree": str(resolved_worktree),
        "exactModel": exact_model,
        "effort": effort,
        "initialRecommendedEffort": planned_story["recommendedEffort"],
        "reasoningConfigKey": native_reasoning["key"],
        "reasoningConfigEvidenceDigest": native_reasoning["evidenceDigest"],
        "handoffDigest": planned_story["handoffDigest"],
        "hostEvidenceDigest": plan["hostEvidenceDigest"],
        "workerOutputSchemaPath": str(resolved_schema),
        "workerOutputSchemaDigest": schema_digest,
        "promptDigest": _digest_bytes(prompt.encode("utf-8")),
        "gitEnvironmentDigest": git_environment.digest,
        "argv": [],
        "previousLaunchDigest": previous_launch_digest,
        "retryEvidenceDigest": retry_evidence_digest,
    }
    record["argv"] = list(_expected_argv(record))
    normalized = validate_launch_record(record)
    return PreparedLaunch(
        argv=tuple(normalized["argv"]),
        stdin=prompt,
        environment=git_environment.environment,
        record=MappingProxyType(normalized),
    )


def prepare_launch(
    run_spec: Mapping[str, object],
    wave_plan: Mapping[str, object],
    host_capabilities: Mapping[str, object],
    *,
    planning_timestamp: str,
    story_id: str,
    worktree: Path,
    worker_schema: Path,
    reasoning_config_key: str,
    reasoning_config_evidence_digest: str,
    git_environment: GitEnvironment,
) -> PreparedLaunch:
    """Prepare the first exact-model launch from planner-bound effort advice."""

    plan_story = next(
        (
            story for story in wave_plan.get("stories", [])
            if isinstance(story, Mapping) and story.get("storyId") == story_id
        ),
        None,
    )
    if not isinstance(plan_story, Mapping):
        raise LaunchError(f"story {story_id!r} is absent from the wave plan")
    return _prepare(
        run_spec, wave_plan, host_capabilities,
        planning_timestamp=planning_timestamp, story_id=story_id,
        worktree=worktree, worker_schema=worker_schema,
        reasoning_config_key=reasoning_config_key,
        reasoning_config_evidence_digest=reasoning_config_evidence_digest,
        git_environment=git_environment,
        effort=str(plan_story.get("recommendedEffort")), attempt=1,
        previous_launch_digest=None, retry_evidence_digest=None,
    )


def prepare_retry_launch(
    run_spec: Mapping[str, object],
    wave_plan: Mapping[str, object],
    host_capabilities: Mapping[str, object],
    *,
    planning_timestamp: str,
    story_id: str,
    worktree: Path,
    worker_schema: Path,
    reasoning_config_key: str,
    reasoning_config_evidence_digest: str,
    git_environment: GitEnvironment,
    previous_launch: Mapping[str, object],
    failure_evidence: FailureEvidence,
) -> PreparedLaunch:
    """Prepare the sole same-model higher-effort retry when evidence permits it."""

    previous = validate_launch_record(previous_launch)
    if previous["storyId"] != story_id:
        raise LaunchError("retry story differs from the previous launch")
    disposition = classify_failure(
        previous, failure_evidence, host_capabilities.get("supportedEfforts", [])
    )
    if disposition.status != "retry" or disposition.retry_effort is None:
        raise LaunchError(f"worker failure is blocked: {disposition.reason}")
    prepared = _prepare(
        run_spec, wave_plan, host_capabilities,
        planning_timestamp=planning_timestamp, story_id=story_id,
        worktree=worktree, worker_schema=worker_schema,
        reasoning_config_key=reasoning_config_key,
        reasoning_config_evidence_digest=reasoning_config_evidence_digest,
        git_environment=git_environment,
        effort=disposition.retry_effort, attempt=2,
        previous_launch_digest=_digest_json(previous),
        retry_evidence_digest=failure_evidence.evidence_digest,
    )
    if prepared.record["exactModel"] != previous["exactModel"]:
        raise LaunchError("retry must preserve the exact first-attempt model")
    immutable = (
        "runId", "storyId", "branch", "worktree", "exactModel",
        "initialRecommendedEffort", "reasoningConfigKey",
        "reasoningConfigEvidenceDigest", "handoffDigest", "hostEvidenceDigest",
        "workerOutputSchemaPath", "workerOutputSchemaDigest", "promptDigest",
        "gitEnvironmentDigest",
    )
    drifted = [field for field in immutable if prepared.record[field] != previous[field]]
    if drifted:
        raise LaunchError(
            "retry changed immutable first-attempt binding(s): " + ", ".join(drifted)
        )
    return prepared


__all__ = [
    "BUNDLED_WORKER_SCHEMA", "EXPECTED_WORKER_SCHEMA_DIGEST",
    "FailureDisposition", "FailureEvidence", "LaunchError", "PreparedLaunch",
    "REASONING_CONFIG_KEY", "build_worker_prompt", "classify_failure",
    "prepare_launch", "prepare_retry_launch", "validate_launch_record",
    "validate_worker_output",
]
