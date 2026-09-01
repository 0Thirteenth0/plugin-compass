"""Validated planning plus Task 5 dry-run controller composition."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .doctor import DoctorError, run_doctor
from .handoff import HandoffError, resolve_plugin_compass
from .models import canonical_json, run_binding_digest
from .lease import acquire_lease, release_lease
from .planner import PlanningError, build_plan
from .state import (
    StateError, StateStore, build_execution_bundle, load_run_bundle,
    validate_execution_bundle,
)


def _json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to load JSON from {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return value


def _inventory(path: Path | None) -> tuple[dict[str, object], Path | None]:
    if path is not None:
        return _json(path), path.resolve(strict=True).parent
    result = subprocess.run(
        ["codex", "plugin", "list", "--json"], check=False, capture_output=True,
        text=True, encoding="utf-8", errors="strict", shell=False, timeout=20,
    )
    if result.returncode != 0:
        raise HandoffError("authoritative Codex plugin inventory is unavailable")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise HandoffError("authoritative Codex plugin inventory is malformed")
    return value, None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="compass-builder")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "plan"):
        command = commands.add_parser(name)
        command.add_argument("--repo", type=Path, required=True)
        command.add_argument("--spec", type=Path, required=True)
        command.add_argument("--native-capabilities", type=Path, required=True)
        command.add_argument("--planning-timestamp")
        if name == "plan":
            command.add_argument("--mode", choices=("auto", "sequential", "parallel"), required=True)
            command.add_argument("--plugin-compass-root", type=Path)
            command.add_argument("--inventory-file", type=Path)
            command.add_argument("--prior-wave-failed", action="store_true")
    run = commands.add_parser("run")
    run.add_argument("--repo", type=Path, required=True)
    run.add_argument("--plan", type=Path, required=True)
    resume = commands.add_parser("resume")
    resume.add_argument("--repo", type=Path, required=True)
    resume.add_argument("--run-id", required=True)
    for command in (run, resume):
        command.add_argument(
            "--dry-run", action="store_true", required=True,
            help="validate and project controller work without starting a worker",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command in {"run", "resume"}:
            if args.command == "run":
                bundle = validate_execution_bundle(_json(args.plan), args.repo)
            else:
                bundle = load_run_bundle(args.repo, args.run_id)
            spec, plan = bundle["runSpec"], bundle["wavePlan"]
            store = StateStore(args.repo, spec, plan)
            now = datetime.now(timezone.utc)
            acquired = now.isoformat().replace("+00:00", "Z")
            expires = (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
            handle = acquire_lease(
                store.repository.common_git_dir, str(store.spec["integrationBranch"]),
                owner_id=f"dry-run-{os.getpid()}",
                evidence_digest=run_binding_digest(store.spec, store.plan),
                acquired_at=acquired, expires_at=expires,
            )
            try:
                if args.command == "run":
                    state = store.initial_state()
                    store.create(state, execution_bundle=bundle)
                else:
                    previous = store.load()
                    state = store.resume_state(previous)
                    store.write_transition(previous, state)
                output = store.dry_run_projection(state)
                output["operation"] = args.command
                output["leaseValidated"] = True
                sys.stdout.write(canonical_json(output).decode("utf-8") + "\n")
                return 0
            finally:
                release_lease(handle)
        spec = _json(args.spec)
        native = _json(args.native_capabilities)
        planning = args.planning_timestamp or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        report = run_doctor(
            args.repo, spec, native, planning_timestamp=planning
        )
        output: dict[str, object] = report
        if args.command == "plan":
            inventory, base = (None, None)
            if args.plugin_compass_root is None:
                inventory, base = _inventory(args.inventory_file)
            root = resolve_plugin_compass(
                explicit_root=args.plugin_compass_root,
                inventory=inventory,
                inventory_base=base,
            )
            wave_plan = build_plan(
                spec, report, requested_mode=args.mode, plugin_compass_root=root,
                prior_wave_failed=args.prior_wave_failed,
            )
            output = build_execution_bundle(
                spec, wave_plan, report["hostCapabilities"],
                str(report["planningTimestamp"]), args.repo,
            )
    except (
        DoctorError, HandoffError, PlanningError, StateError, ValueError, OSError,
        subprocess.TimeoutExpired, UnicodeError,
    ) as exc:
        print(f"compass-builder: {exc}", file=sys.stderr)
        return 4
    sys.stdout.write(canonical_json(output).decode("utf-8") + "\n")
    return 0
