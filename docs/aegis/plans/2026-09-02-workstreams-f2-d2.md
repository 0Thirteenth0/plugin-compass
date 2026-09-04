# Workstreams F2 and D2 implementation checkpoint

## Goal

Implement the second approved safe slice: bounded standalone-skill discovery in Plugin
Compass and exact, provider-bound sequential outcome-gate execution in Compass Builder.

## Architecture

Plugin Compass owns a separate, read-only standalone-skill adapter that returns the
source-neutral `SkillRecord` introduced by F1. Compass Builder owns a separate approval,
snapshot, and runner path for D1 command gates. Neither lane changes Plugin Compass into
an executor or wires gate results into Builder completion; F3 and D3 retain those later
responsibilities.

## Tech Stack

- Python 3.11+ standard library
- Existing Plugin Compass metadata, readiness, and source-neutral skill contracts
- Existing Compass Builder D1 ledger, bounded process runner, and temporary workspaces
- `unittest` fixtures and temporary directories

## Baseline/Authority Refs

- `feature_planning.md`, Workstreams D and F
- `docs/aegis/plans/2026-09-02-workstreams-f1-d1.md`
- `plugins/plugin-compass/plugin_compass/skill_models.py`
- `plugins/compass-builder/compass_builder/_gate_models.py`
- User authorization on 2026-09-02: move to the next recommended safe slice

## Compatibility Boundary

- `codex plugin list --json` remains the only authoritative plugin identity and enabled
  state path.
- Packaged-skill discovery remains unchanged.
- Standalone roots are explicit caller-supplied configuration; F2 does not infer them
  from plugin caches or arbitrary filesystem crawling.
- F2 does not connect standalone records to `inventory`, `assess`, `recommend`, or
  `prompt`; that is F3.
- D1 gate-ledger semantics remain unchanged.
- D2 does not alter controller, verifier, integrator, worker-import, or completion
  behavior; that is D3.
- Existing process-runner callers retain their behavior because post-parent process-group
  cleanup is opt-in and D2 alone enables it.

## TDD Route

- Mode: off
- Decision: strict
- Strict authority: the approved standalone-skill requirement explicitly requires
  test-driven implementation, and the frozen F1/D1 plan carries strict RED/GREEN forward
- Test posture: focused failing behavior test, minimal implementation, focused GREEN,
  independent specification review, independent quality review, then integrated audit
- Verification: focused F2/D2 tests, adjacent plugin regressions, repository harness audit

## F2 implemented boundary

- Discover only explicit `standalone-user`, `standalone-project`, and `system` roots.
- Read bounded `SKILL.md` frontmatter and skill-local readiness references without
  executing skill instructions.
- Preserve deterministic source-neutral identities and duplicate names while
  deduplicating identical logical roots.
- Enforce caps for roots, directory entries, depth, skill count, readiness references,
  file bytes, and cooperative runtime.
- Reject traversal, absolute/drive escapes, symlinks, junctions, and other reparse escapes.
- Return deterministic degraded diagnostics for missing, unreadable, malformed,
  oversized, duplicate, or time-limited inputs.

F2 intentionally requires a trusted caller to supply configured roots and stable logical
source identities. Its runtime deadline cannot preempt a single blocked operating-system
call, but expiry is observed immediately after the call returns.

## D2 implemented boundary

- Accept only an opaque approval capability issued by an explicit in-process provider;
  raw mappings and self-asserted approval strings cannot cross the runner boundary.
- Bind the gate, command, marker, canonical executable, working directory, platform,
  environment, resource limits, referenced content, artifact, and platform-specific
  executable-isolation identity.
- Require canonical absolute direct execution with `direct-no-shell-v1`; reject PATH
  lookup, relative executables, Windows batch mediation, and lexical dot aliases.
- Stage declared referenced inputs through exact argv-slot bindings and reject statically
  visible original-worktree aliases in other arguments or environment values.
- Require fresh artifact content transition before an artifact digest can pass.
- Execute gates sequentially with closed stdin, bounded output/time, deterministic result
  ordering, and truthful `executed` evidence.

D2 is not an operating-system sandbox. `referencesComplete` remains the trusted
provider's completeness assertion, and D3 must bind the capability to an actual
user/operator decision before controller enforcement. Four POSIX-only staging and
successful-parent descendant tests remain an explicit runtime evidence gap on this
Windows host.

## Review and verification

- F2 specification review: approved after traversal, unreadable-directory, resource,
  deadline, duplicate-root, and ordering repairs.
- F2 quality review: approved with no remaining findings.
- D2 specification review: approved after executable, artifact freshness, approval,
  snapshot, shell, reference-binding, and canonical-path repairs.
- D2 quality review: approved with no remaining code findings; POSIX runtime evidence is
  still required for a fully cross-platform claim.
- Controller-owned focused integration: 69 tests passed and 3 POSIX-only tests skipped.
- Repository-wide audit remains the final stop gate for this slice.

## Complexity control

F2 is isolated in a dedicated adapter. D2 is split among approval, snapshot, and runner
owners rather than adding responsibilities to the controller or integrator. The F2
adapter remains comparatively large; its traversal, diagnostic, deadline, and per-skill
responsibilities are separated by helpers, with further reduction left as advisory
follow-up rather than hidden scope growth.

## Safe stop

Stop after a green repository audit and report the POSIX validation gap. Do not start F3,
D3, E1, G1, installation, hooks, live benchmarks, commit, push, publication, or cleanup
without separate authorization.
