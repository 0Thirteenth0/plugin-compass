# Plugin Compass Revised Implementation Plan

## Goal

Build the smallest production-quality Plugin Compass that adds task-specific selection,
finding triage, minimal-set planning, and prompt generation on top of Codex, DrSkill, and
HOL evidence.

## Current State

- The Codex plugin, focused skill, CLI, schema, fixtures, and regression suite exist.
- Product, design, and DrSkill gap decisions are documented.
- `drskill-core==0.7.2` is installed and statically baselined.
- Prototype scanner code has been replaced by narrow Codex, DrSkill, and HOL adapters.
- The behavior is covered by fixture-backed unit and CLI tests; final external validation
  evidence is recorded in the project handoff.

## Scope Fence

The MVP contains one focused skill and a small standard-library CLI. It does not contain
generalized scanners, DrSkill internals, hooks, MCP servers, apps, session mining,
networking, orchestration, persistent state, or package-management actions.

## Compatibility Boundary

- Python 3.11+ standard library.
- Windows paths, paths containing spaces, and WSL-mounted paths.
- Codex plugin-list JSON fixtures and live output.
- DrSkill 0.7.x finding JSONL, treated as an external versioned format.
- HOL scanner JSON fixtures and exact-target reports.
- No production credentials or third-party plugin execution.

## Implementation Tasks

### 1. Freeze contracts and fixtures

**Status:** complete.

- Add fixtures for Codex inventory, local plugin/skill metadata, DrSkill JSONL, HOL JSON,
  repository authority, and expected plans.
- Include credible findings, known false-positive shapes, unknown trust, disabled plugins,
  Windows paths with spaces, and malformed inputs.
- Define the JSON schema before rewriting decision code.

**Verification:** fixtures parse independently and expected outputs are reviewable.

### 2. Replace prototype discovery with evidence adapters

**Status:** complete.

- Extract the useful Codex subprocess and fixture parsing into `adapters/codex.py`.
- Add a DrSkill JSONL adapter that preserves original fields and never executes fixes.
- Add a HOL JSON adapter with exact-target matching and high/critical hard gates.
- Retain only shallow, bounded manifest and skill-frontmatter extraction.
- Remove generalized injection, unsafe-command, and duplicate scanning from Plugin Compass.

**Verification:** adapter unit tests, malformed-input tests, timeout tests, and no-shell assertions.

### 3. Simplify models and repository context

**Status:** complete.

- Add `FindingRecord` and `FindingTriage`.
- Remove scanner-specific trust booleans from plugin identity records.
- Reduce repository inspection to applicable authority and bounded structural evidence.
- Preserve stable IDs, sorted serialization, and explicit unknown states.

**Verification:** schema validation, stable IDs, Windows path cases, and repeated equality.

### 4. Rebuild task-specific decisions

**Status:** complete.

- Implement descriptive repository/task relevance.
- Triage findings without overwriting source severity.
- Consume DrSkill overlap findings instead of running a custom fuzzy scanner.
- Select an eligible task-specific winner and the minimal capability set.
- Keep disabled availability separate from trust.
- Generate evidence-linked rationale and the ready-to-paste prompt.

**Verification:** focused tests for every classification, hard gate, overlap winner,
specialist preference, false positive, and unknown evidence path.

### 5. Complete CLI and skill

**Status:** complete.

- Implement `inventory`, `assess`, `recommend`, and `prompt`.
- Support Markdown and deterministic JSON.
- Add explicit `--drskill-report`, `--hol-report`, and `--collect-drskill` inputs.
- Keep ordinary inventory fast and free of implicit DrSkill scans.
- Rewrite `SKILL.md` and methodology around the final commands and authorization boundary.

**Verification:** CLI fixture smoke tests, help output, deterministic output snapshots, and
Skill Creator validation.

### 6. Validate and review

**Status:** complete.

- Run the Plugin Creator validator against the plugin package.
- Run Skill Creator validation and DrSkill lint against the focused skill directory.
- Run the full fixture/unit/CLI suite twice and compare outputs.
- Run HOL lint, verify, and scan against the exact plugin target.
- Review the final implementation for duplicate scanner logic and unsupported claims.

**Verification:** exact commands, versions, exit codes, scope, and residual unknowns are
recorded in the final handoff.

### 7. Add execution-readiness and native handoff contracts

**Status:** complete.

- Statically record explicit plugin-root file presence per capability without executing it.
- Exclude unresolved capability paths while preserving healthy sibling skills and trust state.
- Add strict agent-task and proposal-only native handoff contracts.
- Preserve the selected model, choose only supplied supported efforts, require validation,
  and cap diagnosed higher-effort reasoning retries at one.

**Verification:** readiness safety tests, handoff gate tests, schema validation, native
tool forward test when authorized by applicable repository policy, and package validators.

### 8. Add standalone-skill discovery and source-neutral recommendations

**Status:** complete in Workstreams F1-F3; F4 release/adversarial closure remains later.

- Keep `codex plugin list --json` authoritative for plugin identity and discover
  standalone skills only from explicit bounded user, project, and system roots.
- Preserve standalone skills as source-neutral records with qualified identity,
  provenance, trust, metadata, readiness, and degraded diagnostics.
- Integrate mixed-source skill assessment and exact minimum-cardinality recommendations
  into `inventory`, `assess`, `recommend`, and `prompt` without invoking any skill.
- Fail safely on ambiguous bare names, logical-root collisions, malformed/oversized
  metadata, reparse/path escapes, resource limits, and inconclusive plugin inventory.

**Verification:** fixture-backed F3 CLI/model/decision/schema/rendering/determinism tests,
independent specification and quality/security review, and repository audit.

### 9. Add opt-in outcome-gate enforcement to Compass Builder

**Status:** complete in Workstreams D1-D3; controlled comparison remains later.

- Preserve `plan-bundle.v1` behavior and add a closed opt-in v2 bundle with a pristine
  outcome-gate ledger.
- Require a trusted in-process provider for just-in-time exact decisions, receipt seals,
  pre-publication authentication, explicit genesis initialization, a monotonic evidence
  checkpoint, and durable per-attempt command reservations.
- Verify workers and required story gates before branch import; run root gates after
  existing post-merge checks but before verified-state or completion advancement.
- Fail closed on forged, truncated, replayed, wrong-phase, wrong-target, or incomplete
  evidence, missing checkpoints, unresolved execution attempts, or v1 gate artifacts and
  preserve phase-specific blockers.

**Verification:** focused gate/evidence/controller/integrator tests in temporary Git
repositories, independent specification and quality/security review, and repository audit.

## Retirement Checklist

Before completion, remove or replace:

- prototype unsafe-command regular expressions;
- prototype credential and external-mutation inference from arbitrary strings;
- prototype fuzzy duplicate graph;
- repository-wide source inventory that is unnecessary for authority or task context;
- trust decisions derived solely from DrSkill severity;
- any claim that DrSkill validates `.codex-plugin/plugin.json`.

## Test Obligations

- Two plugins with the same DrSkill overlap finding.
- Specialist versus broad bundle for a narrow task.
- Missing and malformed Codex/plugin metadata.
- Installed but disabled plugin remains on-demand, not untrusted.
- HOL high/critical finding blocks the exact target.
- DrSkill credential-path false positive remains visible but non-blocking.
- Unknown security status with and without executable surfaces.
- Windows paths and paths containing spaces.
- Empty repository and repository with explicit authority.
- Stable Markdown, JSON, overlap winners, minimal plans, and prompts.
- DrSkill JSONL in a different order produces identical output.
- Fix commands and discovered instructions are never executed.

## Stop Conditions

Stop if implementation requires unsupported DrSkill internals, human-table parsing,
session-history access, plugin execution, external mutation, credentials, networking,
persistent state, or a second governance system.

## Execution Route

Implement only an explicitly approved slice. Disjoint Plugin Compass and Compass Builder
lanes may use isolated reviewers when authorized, while the controller owns shared
documents and repository-wide validation. Preserve the valid plugin scaffold and all
unrelated user changes.
