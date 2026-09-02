"""Build, validate, and load repository-bound execution bundles."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Mapping

from .durable_artifacts import accepts
from .errors import StateError
from .git_environment import GitEnvironment
from .models import canonical_json, validate_run_bindings
from .repository import resolve_repository
from .secure_files import read_no_follow, require_contained
from ._validation import run_id as validate_run_id


def build_execution_bundle(run_spec, wave_plan, host_capabilities, planning_timestamp, repository, git_environment=None):
    identity = resolve_repository(repository, git_environment)
    try:
        spec, plan, _ = validate_run_bindings(
            run_spec, wave_plan, host_capabilities=host_capabilities,
            planning_timestamp=planning_timestamp,
        )
    except ValueError as exc:
        raise StateError(f"execution bundle bindings are invalid: {exc}") from exc
    return validate_execution_bundle({
        "schemaVersion": "compass-builder.plan-bundle.v1", "runSpec": spec,
        "wavePlan": plan, "hostCapabilities": copy.deepcopy(dict(host_capabilities)),
        "planningTimestamp": planning_timestamp,
        "repositoryIdentity": {
            "repositoryRoot": str(identity.root), "commonGitDir": str(identity.common_git_dir),
            "gitDir": str(identity.git_dir),
        },
    }, identity.root, git_environment)


def validate_execution_bundle(value: Mapping[str, object], repository: Path | None = None, git_environment: GitEnvironment | None = None):
    required = {"schemaVersion", "runSpec", "wavePlan", "hostCapabilities", "planningTimestamp", "repositoryIdentity"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise StateError("execution bundle must match the closed compass-builder.plan-bundle.v1 field set")
    bundle = copy.deepcopy(dict(value))
    if bundle["schemaVersion"] != "compass-builder.plan-bundle.v1":
        raise StateError("execution bundle has an unsupported schemaVersion")
    if not all(isinstance(bundle[field], Mapping) for field in ("runSpec", "wavePlan", "hostCapabilities")):
        raise StateError("execution bundle contracts must be JSON objects")
    if not isinstance(bundle["planningTimestamp"], str):
        raise StateError("execution bundle planningTimestamp must be text")
    try:
        spec, plan, _ = validate_run_bindings(
            bundle["runSpec"], bundle["wavePlan"], host_capabilities=bundle["hostCapabilities"],
            planning_timestamp=bundle["planningTimestamp"],
        )
    except ValueError as exc:
        raise StateError(f"execution bundle bindings are invalid: {exc}") from exc
    identity = bundle["repositoryIdentity"]
    fields = {"repositoryRoot", "commonGitDir", "gitDir"}
    if not isinstance(identity, dict) or set(identity) != fields:
        raise StateError("execution bundle repositoryIdentity has an unsupported field set")
    if any(not isinstance(identity[field], str) or not identity[field] or identity[field] != identity[field].strip() or len(identity[field]) > 1024 or any(ord(c) < 32 or ord(c) == 127 for c in identity[field]) for field in fields):
        raise StateError("execution bundle repositoryIdentity fields must be bounded clean text")
    if repository is not None:
        actual = resolve_repository(repository, git_environment)
        expected = {"repositoryRoot": str(actual.root), "commonGitDir": str(actual.common_git_dir), "gitDir": str(actual.git_dir)}
        if identity != expected:
            raise StateError("execution bundle repository identity does not match this checkout")
    bundle["runSpec"], bundle["wavePlan"] = spec, plan
    canonical_json(bundle)
    return bundle


def load_run_bundle(repository: Path, run_id: str, git_environment: GitEnvironment | None = None):
    validate_run_id(run_id, "runId")
    identity = resolve_repository(repository, git_environment)
    controller = identity.root / ".compass-builder"
    root = require_contained(controller / "runs" / run_id, controller, label="resume run root")
    try:
        names = {item.name for item in root.iterdir()}
    except OSError as exc:
        raise StateError(f"durable run artifact set is unavailable: {exc}") from exc
    if not accepts(names, require_bundle=True):
        raise StateError("durable run artifact set is partial or contains unknown files")
    try:
        value = json.loads(read_no_follow(
            root / "plan-bundle.json", controller, label="durable execution bundle",
            max_bytes=16_777_216,
        ).decode("utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise StateError(f"durable execution bundle is unavailable or malformed: {exc}") from exc
    if not isinstance(value, dict):
        raise StateError("durable execution bundle must be a JSON object")
    return validate_execution_bundle(value, identity.root, git_environment)


def load_run_inputs(repository: Path, run_id: str, git_environment: GitEnvironment | None = None):
    bundle = load_run_bundle(repository, run_id, git_environment)
    return bundle["runSpec"], bundle["wavePlan"]


__all__ = ["build_execution_bundle", "load_run_bundle", "load_run_inputs", "validate_execution_bundle"]
