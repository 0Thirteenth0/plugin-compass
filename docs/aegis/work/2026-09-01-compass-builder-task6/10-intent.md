# Compass Builder Task 6 intent

## Requested outcome

Continue the approved Compass Builder MVP by enforcing Git-derived worker scope and
receipt evidence, serial fail-closed integration, and cleanup limited to registered,
verified, safely contained worktrees.

## Slice Card

- Goal: make worker verification, integration, and cleanup independently enforceable
  before a live model-backed run is authorized.
- Parent plan/spec: `docs/aegis/plans/2026-09-01-compass-builder-mvp.md`, Task 6, and
  `docs/COMPASS_BUILDER_CONTRACT.md`.
- Files: new `verifier.py`, `integrator.py`, `cleanup.py`, a deterministic temporary Git
  repository factory, focused tests, minimal package exports, and CLI wiring for
  `verify-worker` and `cleanup`.
- Boundary: no live Codex worker, benchmark, installed-plugin mutation, automatic conflict
  repair, production repository cleanup, or change to Plugin Compass's read-only role.
- Verification: focused verifier/integrator/cleanup tests, full suite, Plugin Creator
  validation, compile and diff gates, then independent specification and quality review.
- Stop: forged or ambiguous Git evidence, unsafe path/removal semantics, stale CAS,
  inherited ambient Git behavior, state-contract mismatch, or scope expansion.

## BaselineReadSetHint and usage

- Required and acknowledged: `docs/PRODUCT_CONTRACT.md`, `docs/TECHNICAL_DESIGN.md`,
  `docs/COMPASS_BUILDER_CONTRACT.md`, `SECURITY.md`, the active MVP plan, and Task 5's
  durable-state evidence.
- Cited by the parent plan: all required sources above.
- Missing: none.

## ImpactStatementDraft

The slice adds controller-side evidence and lifecycle owners beneath the separate
`compass-builder` plugin. Plugin Compass remains read-only. Git objects and controller-run
checks, not worker claims, determine merge eligibility; cleanup remains explicit and
registry-bound.

## Execution Readiness View

- Intent lock: fastest verified completion, never maximum parallelism at the expense of
  acceptance or repository safety.
- Scope fence: Task 6 owners and focused CLI composition only.
- Baseline lock: clean synchronized `main` at `e6d859f` plus the active product/security
  contracts.
- Owner constraints: verifier owns receipt/Git reconciliation; integrator owns lease/CAS,
  ordered merge, and integrated checks; cleanup owns removal eligibility; controller state
  remains owned by `state.py`.
- Compatibility boundary: Windows paths with spaces, Python 3.11+, Git worktrees,
  standard library, and sanitized local-only Git configuration.
- Retirement boundary: helper owners retire only when native Codex/Git surfaces provide
  equivalent deterministic receipt, integration, and cleanup contracts.
- Test obligations: forged/stale/wrong-SHA, ancestry, dirty state, all scope aliases and
  rename/delete modes, symlink/submodule, multi/merge commit, hostile ambient config,
  conflict/stale CAS/failed checks, and unsafe cleanup.
- Review gates: one implementer, independent specification approval, independent
  quality/security approval, then coordinator verification.
- Drift/rewind: return to plan review if live dispatch, automated conflict resolution,
  unregistered deletion, or a new shared writer becomes necessary.

## TDD Route

- Mode: off
- Decision: skipped
- Strict authority: not applicable
- Test posture: focused post-change regression plus full-suite verification
- Reason: the approved parent plan explicitly records off-mode; the user did not request
  strict RED/GREEN sequencing.

## Change Necessity and complexity

Change Necessity:

- User-visible need: parallel builders are useful only if the controller can prove scope,
  serialize green integration, and remove no evidence-bearing worktree.
- No-change option: worker receipts and process instructions cannot prove Git ancestry,
  changed modes, clean integrated checks, or safe filesystem removal.
- Minimum boundary: the three single-purpose owners and deterministic test factory named
  by Task 6, plus wiring-only exports and CLI composition.
- Decision: code-change.

Complexity Budget:

- Artifact class: source, maintained tests, and process evidence.
- Target files: new verifier, integrator, cleanup, repository factory, and focused tests.
- Current pressure: `state.py` is 791 lines and must not gain a new responsibility;
  `cli.py` remains wiring-only.
- Projected pressure: within budget while Git inspection, merge/check orchestration, and
  cleanup eligibility remain separate and each owner stays below the 800-line soft signal.
- Planned governance: extract shared Git test/environment primitives to the planned
  factory; do not create a generic runtime owner or duplicate state validation.

## Worktree necessity

No task-owned implementation worktree is created: there is one clean checkout and one
writer. Disposable worktrees are allowed only inside isolated temporary test repositories
with test-owned cleanup.
