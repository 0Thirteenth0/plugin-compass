"""Command-line interface for Plugin Compass."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .adapters.codex import CodexInventoryError, CodexInventoryInconclusive, discover_plugins
from .adapters.drskill import (
    DrSkillEvidenceError,
    collect_codex_scan,
    load_report as load_drskill_report,
)
from .adapters.hol import HolEvidenceError, load_report as load_hol_report
from .decision import build_recommendation_plan
from .metadata import enrich_plugins
from .handoff import build_handoff, load_task, render_handoff
from .models import EvidenceRecord, FindingRecord
from .rendering import render_json, render_markdown, scheduling_guidance_lines
from .repository import inspect_repository


def _add_inventory_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--inventory-file",
        type=Path,
        help="saved Codex plugin-list JSON (or test fixture); omit for live discovery",
    )
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")


def _add_assessment_options(parser: argparse.ArgumentParser) -> None:
    _add_inventory_options(parser)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument(
        "--optimization-goal",
        choices=("speed", "cost"),
        default="speed",
        help="speed prioritizes verified completion; cost explicitly enables cost advice",
    )
    parser.add_argument("--drskill-report", type=Path)
    parser.add_argument("--hol-report", type=Path, action="append", default=[])
    parser.add_argument(
        "--collect-drskill",
        action="store_true",
        help="explicitly run only 'drskill scan --harness codex --json'",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plugin-compass",
        description="Map local Codex capabilities and choose a minimal evidence-backed set.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser("inventory", help="enumerate local plugin capabilities")
    _add_inventory_options(inventory)

    handoff = subparsers.add_parser("handoff", help="propose task-specific effort and native dispatch arguments; never execute")
    handoff.add_argument("--task-file", type=Path, required=True)
    handoff.add_argument("--format", choices=("markdown", "json"), default="markdown")

    for command, help_text in (
        ("assess", "render the complete repository and task assessment"),
        ("recommend", "select the minimal capability set"),
        ("prompt", "generate a ready-to-paste session prompt"),
    ):
        subparser = subparsers.add_parser(command, help=help_text)
        _add_assessment_options(subparser)
    return parser


def _inventory_payload(plugins: tuple[object, ...]) -> dict[str, object]:
    return {
        "schema_version": "plugin-compass.inventory.v2",
        "plugins": [item.to_dict() for item in plugins],
    }


def _inventory_markdown(plugins: tuple[object, ...]) -> str:
    lines = ["# Plugin Compass Inventory", ""]
    if not plugins:
        return "\n".join(lines + ["- No plugins were reported.", ""])
    for plugin in plugins:
        capabilities = ", ".join(item.name for item in plugin.capabilities) or "unknown"
        state = "enabled" if plugin.enabled else "disabled"
        lines.append(
            f"- **{plugin.plugin_id}** - {state}; metadata={plugin.metadata_status}; "
            f"capabilities: {capabilities}."
        )
        for capability in plugin.capabilities:
            if capability.readiness.status in {"missing_files", "unknown"}:
                lines.append(f"  - {capability.name}: local-file readiness={capability.readiness.status}; execution unverified.")
    lines.append("")
    return "\n".join(lines)


def _collect_external_evidence(
    args: argparse.Namespace,
) -> tuple[tuple[FindingRecord, ...], tuple[EvidenceRecord, ...]]:
    findings: list[FindingRecord] = []
    evidence: list[EvidenceRecord] = []
    if args.drskill_report and args.collect_drskill:
        raise ValueError("--drskill-report and --collect-drskill are mutually exclusive")
    if args.drskill_report:
        report_findings, report_evidence = load_drskill_report(args.drskill_report)
        findings.extend(report_findings)
        evidence.extend(report_evidence)
    elif args.collect_drskill:
        report_findings, report_evidence = collect_codex_scan()
        findings.extend(report_findings)
        evidence.extend(report_evidence)
    for path in args.hol_report:
        report_findings, report_evidence = load_hol_report(path)
        findings.extend(report_findings)
        evidence.extend(report_evidence)
    return (
        tuple(
            sorted(
                {item.finding_id: item for item in findings}.values(),
                key=lambda item: item.finding_id,
            )
        ),
        tuple(
            sorted(
                {item.evidence_id: item for item in evidence}.values(),
                key=lambda item: item.evidence_id,
            )
        ),
    )


def run(args: argparse.Namespace) -> str:
    plugins = enrich_plugins(discover_plugins(inventory_file=args.inventory_file))
    if args.command == "inventory":
        return (
            render_json(_inventory_payload(plugins))
            if args.format == "json"
            else _inventory_markdown(plugins)
        )

    findings, external_evidence = _collect_external_evidence(args)
    repository = inspect_repository(args.repo)
    plan = build_recommendation_plan(
        plugins,
        repository,
        args.task,
        findings=findings,
        external_evidence=external_evidence,
        optimization_goal=args.optimization_goal,
    )
    if args.command == "prompt":
        if args.format == "json":
            return render_json(
                {
                    "schema_version": "plugin-compass.prompt.v2",
                    "task": plan.task,
                    "optimization_goal": plan.optimization_goal,
                    "generated_prompt": plan.generated_prompt,
                    "recommendations": [item.to_dict() for item in plan.recommendations],
                    "invocation_routes": [
                        item.to_dict() for item in plan.invocation_routes
                    ],
                    "scheduling_guidance": (
                        plan.scheduling_guidance.to_dict()
                        if plan.scheduling_guidance else None
                    ),
                }
            )
        return plan.generated_prompt + "\n"
    if args.command == "recommend" and args.format == "markdown":
        lines = ["# Plugin Compass Recommendation", ""]
        if plan.recommendations:
            for item in plan.recommendations:
                names = ", ".join(item.capability_names) or "declared capability"
                lines.append(f"- **{item.plugin_id}** - {names}. {item.rationale}")
        else:
            lines.append("- No additional plugin is selected for this task.")
        if plan.invocation_routes:
            lines.extend(["", "## Conditional invocation routes", ""])
            for item in plan.invocation_routes:
                lines.append(
                    f"- **{item.capability_name}** - {item.invoker} invokes it for "
                    f"{item.trigger}. {item.rationale}"
                )
        if plan.scheduling_guidance:
            lines.extend(["", "## Per-agent effort guidance (advisory)", ""])
            lines.extend(scheduling_guidance_lines(plan.scheduling_guidance))
        lines.append("")
        return "\n".join(lines)
    return render_json(plan) if args.format == "json" else render_markdown(plan)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="strict")
            except (OSError, ValueError):
                pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        code = 0
        if args.command == "handoff":
            proposal = build_handoff(load_task(args.task_file))
            output = render_json(proposal) if args.format == "json" else render_handoff(proposal)
            code = 0 if proposal["status"] in {"proposed", "keep_local"} else 4
        else:
            output = run(args)
    except CodexInventoryInconclusive as exc:
        if args.format == "json":
            sys.stdout.write(render_json(exc.to_dict()))
        else:
            print(f"plugin-compass: discovery inconclusive. {exc}\n{exc.recovery}", file=sys.stderr)
        return 3
    except (
        CodexInventoryError,
        DrSkillEvidenceError,
        HolEvidenceError,
        ValueError,
    ) as exc:
        print(f"plugin-compass: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
