# Plugin Compass

![Plugin Compass icon](./assets/plugin-compass-icon.png)

Plugin Compass is a read-only Codex decision-support plugin. It combines local Codex
inventory and capability metadata with optional DrSkill and HOL scanner evidence to
recommend the smallest relevant plugin and skill set for a repository or task.

For workload and delegation decisions, the default objective is fastest verified
completion. The generated per-agent effort rubric preserves the selected model and
requires acceptance checks and bounded escalation. It is advisory: Codex or the invoking
scheduler must apply it to actual agent tasks and enforce validation. No effort level
guarantees correctness or optimal speed. The cost optimizer is reserved for explicit
`--optimization-goal cost` requests.

The `handoff` command turns one caller-assessed task into proposed native Codex dispatch
arguments. It is proposal-only: it never launches an agent, grants authority, verifies
the host's supported efforts, or accepts the worker's result. See
`skills/plugin-compass/references/native-dispatch.md`.

## Status

The MVP implementation, fixture suite, and deterministic Markdown and JSON renderers
are complete. The plugin is installed locally from the `plugin-compass-local`
marketplace for acceptance testing.

## Safety boundary

Plugin Compass does not execute discovered plugin code, connect to discovered MCP
servers, run scanner fix commands, inspect session history, or manage installed plugins.
External scanner output is treated as evidence and retains its original severity and
provenance.

## Commands

```text
python scripts/plugin_compass.py inventory --format markdown
python scripts/plugin_compass.py assess --repo <path> --task <text> --format markdown
python scripts/plugin_compass.py recommend --repo <path> --task <text> --format json
python scripts/plugin_compass.py prompt --repo <path> --task <text> --format markdown
python scripts/plugin_compass.py handoff --task-file <agent-task.json> --format json
```

Assessment commands default to `--optimization-goal speed`. Use `--optimization-goal cost`
only for an explicit cost request; speed/model/effort wording does not enable the cost skill.

## Empty inventory recovery

An empty live CLI inventory returns exit `3` and `CODEX_INVENTORY_EMPTY`. It may be a
visibility problem, so Plugin Compass does not interpret it as an empty installation
or produce a plan. Codex can request approval for one read-only
`codex plugin list --json` outside the sandbox. Save that output to a local JSON file,
then pass `--inventory-file <path>` to the same command inside the sandbox. The plugin
never elevates itself, retries automatically, or substitutes cache folders for inventory.
If approval is unavailable or the approved listing remains empty, discovery stays inconclusive.

Plan JSON now uses `plugin-compass.plan.v3`; prompt JSON remains
`plugin-compass.prompt.v2`. Inventory JSON is v2. Capability records include static local
execution-readiness evidence. A missing explicit plugin-root file reference excludes that
capability, but is neither a security verdict nor proof that no alternate launcher exists.

Agent-task input and handoff output use `plugin-compass.agent-task.v1` and
`plugin-compass.handoff.v1`. A proposal preserves the selected model, selects the lowest
supplied supported effort satisfying the task assessment, requires acceptance checks, and
permits at most one diagnosed higher-effort retry. Gate statuses withhold dispatch
arguments. Handoff exits `4` for gates, `2` for invalid input, and `0` for a proposal or
keep-local decision. The inconclusive inventory response keeps its diagnostic schema.

Use `--drskill-report`, `--hol-report`, or the explicit `--collect-drskill` option to
add scanner evidence. Ordinary inventory does not run DrSkill implicitly.

`--drskill-report` accepts DrSkill 0.7.x JSONL output. `--hol-report` is repeatable and
accepts exact-target HOL `scan-result.v1` JSON. Plugin Compass preserves scanner fix
text as data and never executes it.

See the repository-level product contract and DrSkill gap analysis for the authoritative
MVP scope.
