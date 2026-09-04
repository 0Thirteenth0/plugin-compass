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

Standalone skills are opt-in and read-only. Add repeatable
`--user-skill-root <identity> <path>`, `--project-skill-root <identity> <path>`, or
`--system-skill-root <identity> <path>` pairs to `inventory`, `assess`, `recommend`, or
`prompt`. Assessment commands also accept repeatable `--select-skill <value>` arguments;
use an exact `skill://` qualified identity when a bare name is ambiguous. Plugin Compass
never infers these roots or executes a discovered skill. Discovery opens `SKILL.md` as
bounded read-only bytes, requires valid UTF-8, rejects duplicate frontmatter keys, and
reports missing, unreadable, incomplete, malformed, oversized, escaped, or limit-exhausted
input as degraded diagnostics. Incomplete records remain visible but are never eligible
for automatic or exact recommendation. Plugin Compass never installs, copies, edits,
synchronizes, or invokes a skill.

The dependency-free frontmatter reader supports only a flat mapping of simple keys to
string scalars, with blank lines and whole-line comments. An unquoted nonempty string must
start with a Unicode letter, digit, or underscore; contain none of `: # [ ] { }`; and not
match a null, boolean, numeric, or ISO-like date/time token. Empty values remain empty
strings. Otherwise a value must use matching single or double outer quotes, with no
matching quote inside. Complex/quoted keys, duplicate canonical keys, indentation,
block/collection/tag/alias values, inline comments, and stray syntax are malformed.
Balanced quotes preserve numeric- and date-looking text as strings. Source-neutral trust
is one of `not_assessed`, `trusted`, `unknown`, `untrusted`, `blocked`, or `rejected`; only
`not_assessed` and `trusted` are recommendation-eligible. Metadata and readiness files
are opened read-only with no-follow/identity checks before use and immediately before the
first byte read, then revalidated after use. They are rejected if the named object or
containment changes.

## Empty inventory recovery

An empty live CLI inventory returns exit `3` and `CODEX_INVENTORY_EMPTY`. It may be a
visibility problem, so Plugin Compass does not interpret it as an empty installation
or produce a plan. Codex can request approval for one read-only
`codex plugin list --json` outside the sandbox. Save that output to a local JSON file,
then pass `--inventory-file <path>` to the same command inside the sandbox. The plugin
never elevates itself, retries automatically, or substitutes cache folders for inventory.
If approval is unavailable or the approved listing remains empty, discovery stays inconclusive.

Plan JSON uses `plugin-compass.plan.v5`; prompt JSON uses
`plugin-compass.prompt.v3`. Inventory JSON is v3. Source-neutral skill results preserve
qualified provenance, trust, metadata, and readiness, while nested plugin capability
records remain for compatibility. Capability records include static local
execution-readiness evidence. A missing explicit plugin-root file reference excludes that
capability, but is neither a security verdict nor proof that no alternate launcher exists.
Adding standalone roots cannot add or alter plugin records: plugin identity and installed
or enabled state continue to come only from `codex plugin list --json` or its explicit
saved snapshot.

Run the standalone release lane without live skill roots:

```text
python -m unittest tests.test_f4_release_closure tests.test_source_neutral_skill_models tests.test_standalone_skill_discovery tests.test_standalone_skill_ordering tests.test_skill_decision tests.test_f3_cli tests.test_cli tests.test_adapters -v
```

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
