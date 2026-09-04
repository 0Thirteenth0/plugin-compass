# Workstreams F1 and D1 implementation plan

## Authorization and boundary

The user authorized Slice 0 followed by F1 and D1 on 2026-09-02. This plan does not
authorize plugin installation, hooks, cache mutation, commits, pushes, publishing, or
cleanup.

Plugin Compass remains read-only decision support. Compass Builder remains the sole
executor for approved run specifications. This slice adds contracts only; it does not add
standalone-skill invocation or gate-command execution.

## Slice 0: frozen contracts

### F1 — source-neutral skill model

- Add a top-level `SkillRecord` that is not owned by `PluginRecord`.
- Preserve plugin-packaged capabilities and the authoritative
  `codex plugin list --json` plugin identity path.
- Represent provenance with one of `plugin`, `standalone-user`,
  `standalone-project`, `system`, or `session-only`.
- Derive stable skill identity from logical source identity plus normalized relative
  path, never filesystem traversal order or a machine-specific absolute root.
- Preserve source, trust, metadata, and readiness status in serialized records.
- Add plugin-packaged skills to the new top-level collection while retaining the nested
  capability surface for compatibility during migration.
- Version the recommendation-plan JSON shape when the new collection becomes public.

F1 does not enumerate standalone roots or rank standalone skills; those are F2 and F3.

### D1 — outcome-gate ledger contract

- Add a closed, versioned controller-owned outcome-gate ledger.
- Support story and root gates, requirement and acceptance coverage, command and
  manual-review verification, explicit execution identity, risk and validation strength,
  evidence identity, and non-success handoff states.
- Reject duplicate gate IDs, missing required coverage, weak runnable gates, incoherent
  verification fields, and invalid state/evidence combinations.
- Keep definitions as data. D1 does not approve or execute commands and does not change
  controller completion behavior; execution and integration enforcement are later D
  slices.

## Test route

Both slices use strict RED / GREEN / REFACTOR because they introduce public data
contracts. Each lane first adds a focused failing contract test, observes the expected
failure, implements only the minimum owner, and reruns its focused and related suites.

## Isolation and ownership

| Lane | Owned implementation surface | Excluded shared surface |
| --- | --- | --- |
| F1 | `plugins/plugin-compass/`, focused Plugin Compass tests and fixtures | shared docs, Builder files |
| D1 | `plugins/compass-builder/`, focused Builder tests and fixtures | shared docs, Plugin Compass files |
| Controller | this plan, `feature_planning.md`, final integrated validation | lane-owned source while workers run |

The lanes may run concurrently because their write scopes are disjoint. Integration and
full-suite validation remain serial and controller-owned.

## Safe stop

Stop after F1 and D1 are independently green and the integrated repository suite passes.
Do not begin F2, D2, installation, commit, push, publication, or cleanup without a new
authorization.
