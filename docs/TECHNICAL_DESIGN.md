# Plugin Compass Technical Design

## Architecture Decision

Plugin Compass remains a repo-local Codex marketplace containing one plugin and one
focused skill. A small Python 3.11+ standard-library CLI remains justified by the
requirements for deterministic JSON, offline fixtures, and stable prompt generation.

The CLI is an adapter and decision engine, not a scanner. It consumes supported tool
outputs and bounded local metadata without importing or executing third-party plugin code.

## Component Ownership

- `models.py` — typed public records and deterministic serialization.
- `adapters/codex.py` — invoke `codex plugin list --json` without a shell or parse a fixture.
- `adapters/drskill.py` — parse DrSkill JSONL or explicitly invoke the static Codex scan.
- `adapters/hol.py` — parse target-specific HOL JSON reports.
- `metadata.py` — bounded `.codex-plugin/plugin.json` and `SKILL.md` frontmatter reads.
- `readiness.py` — bounded existence checks for explicit plugin-root file references;
  no imports, execution, dependency resolution, cache inference, or trust verdict.
- `repository.py` — applicable authority files and bounded repository context only.
- `decision.py` — finding triage, relevance, overlap winner, minimal-set planning, and conditional exact-skill routing.
- `scheduling.py` — speed-first advisory effort rubric; no dispatch, model mutation, or telemetry.
- `handoff.py` — pure agent-task validation and native dispatch-argument proposals; no
  authority, tool calls, result acceptance, or persistent scheduler state.
- `rendering.py` — deterministic Markdown, JSON, and session prompt rendering.
- `cli.py` and `scripts/plugin_compass.py` — argument parsing and command composition.

The earlier `discovery.py` and `assessment.py` prototypes have been removed. Their useful
Codex inventory and task-decision behavior now lives in the owners above; generalized
unsafe-command and fuzzy-duplicate scanning was retired.

## External Tool Contracts

### Codex

`codex plugin list --json` is the canonical source for plugin identity, installation,
enabled state, version, marketplace, and declared local source. Live invocation uses an
argument array, no shell, a timeout, captured text, and explicit JSON validation.

When a live response has both arrays empty, the adapter raises
`CodexInventoryInconclusive`. Every CLI command stops with exit `3`; JSON mode emits
diagnostic v1, while Markdown mode explains the boundary on stderr. This does not
claim the CLI's internal cause. Codex may request one approved read-only listing outside
the sandbox and provide its output through the existing `--inventory-file` path. The
adapter never escalates, retries, or scans caches. Explicit snapshots, including empty
ones, retain their supplied-data semantics. Other errors remain exit `2`.

### DrSkill 0.7.x

Supported MVP inputs are JSON objects emitted by:

```text
drskill scan --harness codex --json
```

The adapter accepts a JSONL file or captured process output. Each finding is normalized
without changing its original `check_id`, `severity`, message, contributors, harnesses,
or fix commands. Fix commands are evidence strings and are never executed.

The MVP must not:

- parse the human-rendered `drskill list` table;
- import `drskill` Python internals;
- invoke `audit`, `ack`, `review`, `--deep`, or `--mcp-connect`;
- assume DrSkill validates Codex `.codex-plugin` packaging.

### HOL plugin-scanner

The HOL adapter accepts saved JSON for an exact plugin target. High/critical unresolved
findings are hard gates. Missing, malformed, mismatched, or stale reports remain unknown.
HOL output is not replaced by DrSkill severity.

## Data Flow

1. Load Codex inventory JSON or run the official listing command.
2. Resolve only declared local roots and shallow plugin/skill metadata.
3. Load optional DrSkill JSONL and HOL JSON evidence.
4. Inspect applicable repository authority and bounded context.
5. Normalize records and preserve original provenance.
6. Apply hard gates, then finding triage, relevance, and overlap-winner rules.
7. Select the minimal eligible capability set for the supplied task.
8. Independently select any applicable exact-skill invocation route without changing the parent plugin's assessment.
9. Exclude capabilities with unresolved explicit local runtime references while keeping
   healthy sibling capabilities eligible.
10. Optionally convert one authorized, caller-assessed subtask into proposal-only native
    dispatch arguments with validation and retry gates.
11. Sort every public collection before rendering JSON, Markdown, or the prompt.

## Models

`FindingRecord` stores the source tool, source version when known, check ID, original
severity, message, contributors, and evidence references. `FindingTriage` stores a
separate state and rationale; it never overwrites the source finding.

`PluginRecord` stores identity, availability, local source, capability metadata,
execution surfaces, and evidence references. It does not carry scanner-specific booleans.

`Assessment` keeps descriptive dimensions and hard gates. `RecommendationPlan` is the
single canonical object rendered into all output formats.

`InvocationRoute` records a qualified exact capability, its parent plugin, trigger,
Codex as the invoker, a bounded routing rationale, and evidence references. It is not a
plugin recommendation. The initial conditional route targets the exact installed and
enabled `claude-code-skills:llm-cost-optimizer` capability only for explicit cost mode.
Its parent plugin may remain hard-gated and excluded from the minimal set.

Plan v3 and prompt v2 expose typed `optimization_goal` (default `speed`) and nullable
`SchedulingGuidance`. Speed mode does not use text keyword matches to infer a cost goal.
For relevant tasks, the rubric supplies per-agent decision fields, model-preservation
rules, effort bands, acceptance gates, and bounded escalation. It does not itself
classify subagent tasks or enforce dispatch settings. That requires the invoking
scheduler, which is outside this repository. The rubric is a heuristic, not measured
latency evidence or a guarantee of correct results.

`ExecutionReadiness` records `not_assessed`, `not_declared`, `files_present`,
`missing_files`, or `unknown` for a capability's explicit plugin-root references in the
declared local source. `files_present` is not a claim about behavior, dependencies,
installed-cache parity, alternate launchers, or safety. A missing reference excludes that
capability from selection but does not become a plugin trust gate.

Agent-task v1 inputs and handoff v1 outputs are bounded, strict JSON contracts. The
selector preserves an explicit model (or inherits only for a first attempt), accepts only
caller-supplied currently supported efforts with cited host evidence, and chooses the
lowest supported level satisfying complexity, ambiguity, risk, validation, and retry
floors. A retry must name the exact prior model and concrete failure evidence. Gate
statuses emit no dispatch arguments; a proposal remains unexecuted and unverified.

## Finding Triage

Deterministic rules may label a finding `suspected-false-positive` only when the evidence
matches a narrow documented condition, such as a scanner implementation quoting its own
credential-path detection patterns. Otherwise tool findings remain `unreviewed` unless a
fixture or explicit local review record marks them `credible`, `resolved`, or `unknown`.

No heuristic triage may clear a HOL high/critical finding for the exact target.

## Decision Ordering

Ordering is transparent and uses, in order:

1. hard-gate eligibility;
2. availability for the current session;
3. task and repository relevance;
4. capability-specific match;
5. specialist advantage for a narrow request;
6. credible evidence completeness;
7. lower context/runtime cost when supported by evidence;
8. plugin ID as the deterministic tie-breaker.

Disabled plugins are unavailable now, not untrusted. They may be recommended on demand
but Plugin Compass never enables them.

## Determinism

- No wall-clock timestamp participates in decision output.
- Paths are normalized for comparison and retained verbatim for display.
- JSONL input order does not affect output ordering.
- Records, findings, evidence, overlap members, recommendations, and keys use stable sorts.
- Invocation routes use stable IDs and capability-name ordering.
- Stable IDs derive only from normalized source fields.
- Tool versions and report timestamps may be reported but do not influence ranking unless
  the contract explicitly marks evidence stale.

## Repository Inspection Boundary

Inspect only named authority documents and bounded structural signals required for task
context. Skip credential filenames, VCS internals, dependency/vendor trees, symlinks, and
oversized files. Do not perform a general source-code content crawl.

## Packaging and Validation

The plugin ships no hooks, MCP server, app, background service, or credentials.

Validation responsibilities are deliberately separate:

1. Plugin Creator validates `.codex-plugin/plugin.json` and marketplace structure.
2. Skill Creator validates the focused skill.
3. DrSkill lints the skill directory and supplies loadout findings; it does not validate
   the Codex plugin manifest in version 0.7.2.
4. Unit/CLI tests validate behavior and determinism.
5. HOL `plugin-scanner` is the final target-specific security gate.

No installation, marketplace registration, or enablement occurs without separate user
authorization.
