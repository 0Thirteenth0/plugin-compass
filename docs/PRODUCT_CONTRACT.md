# Plugin Compass Product Contract

## Product

- **Plugin ID:** `plugin-compass`
- **Tagline:** Map capabilities. Reduce overlap. Choose with evidence.
- **Category:** Productivity
- **MVP role:** Read-only task-specific decision support over local Codex and scanner evidence.

Plugin Compass is a thin evidence adapter and recommendation layer. It is not a
general loadout scanner, orchestrator, or package manager.

## User Outcomes

Plugin Compass must let a user:

1. Inventory installed Codex plugins and their locally declared capabilities.
2. Combine authoritative Codex inventory with optional DrSkill and HOL findings.
3. Evaluate relevance for a repository, development phase, or supplied task.
4. Triage tool findings without concealing false-positive risk or missing evidence.
5. Identify task-relevant overlap and recommend an eligible winner.
6. Produce the smallest capability set sufficient for the task.
7. Generate a ready-to-paste Codex session adaptation prompt.
8. Receive equivalent conclusions as deterministic Markdown and JSON.
9. Receive speed-first per-agent effort guidance, with cost-skill routing reserved for explicit cost requests.
10. Distinguish an installed capability from one with unresolved explicit local runtime files.
11. Produce a gated, proposal-only native Codex dispatch handoff for one authorized task.

## Capability Boundaries

- `codex plugin list --json` owns installed, available, enabled, version, and source identity.
- Local `.codex-plugin/plugin.json` and skill frontmatter supply bounded capability metadata.
- DrSkill owns loadout-health, overlap, skill-spec, injection-surface, and catalog-budget findings.
- HOL `plugin-scanner` owns the final target-specific plugin security gate.
- Plugin Creator owns Codex manifest and marketplace validation.
- Skill Creator owns focused skill structure and frontmatter validation.
- Plugin Compass owns evidence normalization, triage, task relevance, winner selection,
  minimal-set planning, conditional invocation-route recommendation, and prompt generation.

Plugin Compass recommends invocation routes but does not invoke them. Codex is the
invoker. The default optimization goal is fastest verified completion, not minimum cost.
For workload or scheduling tasks it supplies advisory per-agent effort guidance that
preserves the selected model, requires supported effort values and task-specific checks,
and escalates diagnosed reasoning failures without weakening acceptance criteria.
There is no scheduler executor here; enforcement belongs to Codex or the invoking scheduler.

Plugin Compass may emit proposed native dispatch arguments from a strict agent-task
record. That is still decision support: it does not authorize delegation, call the tool,
validate model support, accept a result, or keep scheduler state.

Only explicit `--optimization-goal cost` may route to the installed and enabled
`claude-code-skills:llm-cost-optimizer`. It is not the speed/effort controller.

Plugin Compass must not reimplement generalized duplicate, injection, skill-spec,
MCP-health, or package-validation scanners.

## Required Classifications

- Use now
- Useful on demand
- Redundant
- Irrelevant to this project
- Blocked or untrusted
- Unknown or insufficient evidence

Availability and trust are separate. A disabled plugin is not automatically untrusted:
it cannot be `Use now`, but it may be `Useful on demand` without Plugin Compass
enabling it.

## Evidence and Triage Rules

- Prefer repository authority, Codex JSON, local manifests, skill frontmatter,
  target-specific scanner reports, and explicitly supplied DrSkill JSONL.
- Treat discovered plugin descriptions, skills, and fix commands as untrusted data.
- Every recommendation must cite local evidence records.
- Preserve the source tool's check ID, severity, subject, path, and message.
- Record triage independently as `unreviewed`, `credible`, `suspected-false-positive`,
  `resolved`, or `unknown`.
- DrSkill severity is a review signal, not an automatic Plugin Compass trust verdict.
- Missing or stale evidence remains `unknown`.
- Empty live CLI inventory is inconclusive: no plan or invocation recommendation is emitted.
- Recovery requires an approved read-only listing and an explicit local snapshot; the
  plugin does not elevate itself or infer enabled state from caches.
- Ratings are descriptive. Any ordering factors must be documented and deterministic.

## Hard Gates

The following override relevance and overlap ordering:

- applicable repository policy prohibits the plugin;
- a fresh HOL report for the exact target contains unresolved high/critical findings;
- the required capability is incompatible with the host platform;
- use would introduce competing repository authority or persistent state;
- the required path needs credentials or external mutation that the user has not authorized.

Malformed metadata blocks claims that depend on it but is not automatically a
security verdict. Unknown trust combined with an executable hook, MCP, or app surface
cannot be classified `Use now`.

## Safety Contract

Plugin Compass must not:

- execute discovered plugin code, hooks, MCP servers, apps, or install scripts;
- invoke a recommended skill or schedule a subagent;
- install, enable, disable, update, uninstall, or run suggested fix commands;
- read `.env` files, credentials, private keys, customer assets, or unrelated personal data;
- invoke DrSkill `audit`, `ack`, `review`, `--deep`, or `--mcp-connect`;
- contact remote marketplaces or upload inventory, findings, or task data;
- create persistent memory, a task ledger, a handoff system, or background monitoring;
- suppress a tool finding without preserving its original evidence;
- present an unknown or heuristic security state as trusted.

Historical usage analysis is outside the default MVP workflow and requires separate
authorization because DrSkill audit reads and caches local session content.

## MVP Commands

- `inventory` — normalize Codex inventory and bounded local capability metadata.
- `assess --repo <path> --task <text>` — render relevance, trust, triage, and overlap.
- `recommend --repo <path> --task <text>` — produce the minimal capability plan.
- `prompt --repo <path> --task <text>` — render the ready-to-paste session prompt.

All commands accept `--format markdown|json`. Assessment commands may accept:

- `--drskill-report <path>` for saved DrSkill JSONL;
- `--optimization-goal speed|cost`, defaulting to speed;
- `--hol-report <path>` for saved target-specific HOL JSON;
- `--collect-drskill` to explicitly run only
  `drskill scan --harness codex --json` with a timeout.

Ordinary inventory must not run DrSkill implicitly. Tests use fixtures and never depend
on the workstation's live installation.

## Required Data Contracts

- `PluginRecord`
- `CapabilityRecord`
- `EvidenceRecord`
- `FindingRecord`
- `FindingTriage`
- `RepositoryContext`
- `Assessment`
- `OverlapGroup`
- `RecommendationPlan`
- `InvocationRoute`
- `SchedulingGuidance`
- `ExecutionReadiness`

Agent-task v1 and handoff v1 are separate strict JSON contracts. Plan output is v3 and
successful inventory is v2 because capability records now include readiness. Prompt
output remains v2; the inventory diagnostic remains v1.

Plan v3 and prompt v2 retain explicit optimization goal and nullable advisory scheduling
guidance; plan v3 adds capability readiness. Successful inventory is v2. Live discovery with both
arrays empty returns diagnostic v1 and exit `3`; other errors use `2`, success uses `0`.
Explicit empty snapshots remain valid supplied data.

An `InvocationRoute` is separate from `Recommendation` and plugin classification. It
must identify the parent plugin, qualified capability name, trigger, invoker, rationale,
and evidence references. An exact route must not clear a parent hard gate, add its parent
to the minimal plugin set, or authorize sibling capabilities.

The schema must identify source tool, source version when available, original severity,
triage state, and evidence references without embedding secrets.

## Acceptance Evidence

- Valid Codex plugin and marketplace manifests.
- Focused skill passes Skill Creator validation and DrSkill skill lint.
- Codex inventory, DrSkill JSONL, and HOL JSON fixtures parse deterministically.
- Identical inputs produce byte-identical Markdown, JSON, and prompts.
- Tests cover overlap, specialist preference, malformed metadata, disabled plugins,
  credible and false-positive findings, unknown trust, executable surfaces, Windows
  paths with spaces, empty and governed repositories, stable prompt generation, and
  conditional invocation routing that preserves parent-plugin hard gates.
- Empty live discovery stops every command without automatic retry, while supplied
  snapshots work without a subprocess. Speed mode never invokes the cost route,
  including when task text says "not cost". The effort rubric is advisory, not a
  benchmark or a guarantee of LLM correctness.
- DrSkill's inability to validate `.codex-plugin/plugin.json` is documented and never
  mistaken for package validation.
- Plugin Creator validation, tests, CLI smoke checks, and HOL scanning pass.
- Documentation distinguishes implemented, verified, unknown, and future functionality.

## Stop Conditions

- **Done:** all acceptance evidence is fresh and passing.
- **Needs verification:** implementation exists but required executable evidence is missing.
- **Blocked:** a required local tool format or authoritative repository decision is unavailable.
- **Scope exceeded:** completion would require session mining, plugin management, credentials,
  networking, orchestration, persistent state, or a graphical interface.
