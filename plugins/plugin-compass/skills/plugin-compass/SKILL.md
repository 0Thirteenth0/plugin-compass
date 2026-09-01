---
name: plugin-compass
description: Compare local Codex plugins using static evidence, recommend a minimal capability set, and provide speed-first per-agent effort guidance in a session prompt. Use when choosing plugins, skills, or reasoning effort for a task. Do not use it to install, enable, execute, or manage plugins or agents.
license: MIT
---

# Plugin Compass

Use the bundled CLI to inspect local plugin metadata without executing discovered plugin code.

## Workflow

1. Read applicable repository authority before assessment.
2. Run `inventory` to establish Codex plugin identity and static capability metadata.
3. Optionally supply a previously generated DrSkill JSONL report for loadout findings and overlap evidence.
4. Optionally supply one or more exact-target HOL JSON reports for trust evidence.
5. Run `assess --repo <path> --task <text>` for the full relevance, trust, and overlap view.
6. Run `recommend --repo <path> --task <text>` for the minimal capability set.
7. Run `prompt --repo <path> --task <text>` for a ready-to-paste session adaptation prompt.
8. When delegation is authorized and worthwhile, read
   [references/native-dispatch.md](references/native-dispatch.md) and run `handoff` for
   each actual subtask. The result is a proposal for Codex, not an executed agent.

Prefer Markdown for conversation and JSON for automation. Keep unknown evidence unknown, cite evidence IDs for recommendations, and let hard policy or security gates override ranking.

Never install, enable, update, remove, or execute a discovered plugin. Never execute a scanner-provided fix command. Never read credentials or `.env` files. Ask before any action outside static local inspection.

Inventory and assessment statically inspect explicit plugin-root file references in each
skill. Exclude a capability whose local reference is missing or unsafe. File presence is
not execution verification, dependency resolution, installed-cache parity, or trust; an
unresolved reference is not a security finding and must not block healthy sibling skills.

## Inconclusive discovery

Exit code `3` with `CODEX_INVENTORY_EMPTY` means live discovery is inconclusive, not that no plugins are installed. Do not produce a Plugin Compass ranking from it. Codex may request approval to run only `codex plugin list --json` outside the sandbox once, then save that result to a local JSON file and rerun with `--inventory-file <path>`. Plugin Compass never elevates itself or retries automatically. If approval is unavailable or the approved result is still empty, report the boundary. Session-exposed skills may support a clearly labeled manual choice, not a claimed inventory assessment. Do not infer enabled state from cache folders.

## Per-agent effort

Use the default `--optimization-goal speed` for workload, reasoning-effort, model, latency, or agent-scheduling decisions. Include the planned work in `--task` and apply the generated `scheduling_guidance` rubric separately to each prospective agent. Codex or the invoking scheduler owns the choice and dispatch; Plugin Compass supplies advisory policy only.

For a concrete native handoff, use an agent-task v1 file with current host-supported
efforts, the selected model, an evidence-based task assessment, authority flags, and
acceptance checks. The CLI may propose `collaboration.spawn_agent` arguments but never
calls it. Codex must inspect the live tool schema, invoke the tool, and independently
validate the result. Retry only a diagnosed reasoning failure once at a higher supported
effort on the exact same model. Tool, permission, missing-input, unknown, exhausted, or
unverifiable failures produce no dispatch arguments.

Preserve the selected model, use only its supported effort levels, and choose the lowest level justified by task complexity, ambiguity, risk, and validation strength. Define acceptance checks before dispatch. Do not accept an agent's self-reported confidence as verification or promise that an effort setting guarantees correct results. Diagnose failures before increasing effort, keep small tasks local, and optimize total time to verified completion rather than first-response speed.

Use `--optimization-goal cost` only when the user actually requests cost optimization. Only that mode may route to an installed and enabled `claude-code-skills:llm-cost-optimizer`. It is not the speed/effort controller. Plugin Compass recommends; Codex invokes. Routes never reclassify the parent plugin or authorize sibling capabilities.

For rating, hard-gate, and interpretation details, read [references/methodology.md](references/methodology.md).

## CLI

From the plugin root:

```text
python scripts/plugin_compass.py inventory --format markdown
python scripts/plugin_compass.py assess --repo <path> --task <text> --format markdown
python scripts/plugin_compass.py recommend --repo <path> --task <text> --format json
python scripts/plugin_compass.py prompt --repo <path> --task <text> --format markdown
python scripts/plugin_compass.py handoff --task-file <path> --format json
```

Add `--drskill-report <jsonl>` and repeatable `--hol-report <json>` options when those reports already exist. Use `--collect-drskill` only when the user explicitly wants a static `drskill scan --harness codex --json`; never substitute `audit`, `ack`, `review`, `--deep`, or `--mcp-connect`.
