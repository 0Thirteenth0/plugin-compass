# Compass Builder Task 5 intent

## Requested outcome

Continue the approved Compass Builder MVP plan by implementing durable controller state,
exclusive leases, resumable run-state transitions, focused skill guidance, and dry-run
`run`/`resume` CLI wiring.

## Slice Card

- Goal: make controller state and recovery deterministic and fail closed before any live
  model worker is authorized.
- Parent plan/spec: `docs/aegis/plans/2026-09-01-compass-builder-mvp.md`, Task 5, and
  `docs/COMPASS_BUILDER_CONTRACT.md`.
- Files: only the Task 5 skill, references/prompts, `state.py`, `lease.py`, example,
  focused tests, and minimal CLI wiring named by the plan.
- Boundary: no live Codex worker, benchmark, installed-plugin mutation, production
  cleanup, worker-scope verification, or Git integration implementation.
- Verification: focused state/lease/skill tests, Skill Creator validation, full suite,
  plugin validation, and diff checks.
- Stop: any repository-identity ambiguity, stale SHA, unsafe path, non-exclusive lease,
  invalid state transition, or scope expansion.

## BaselineReadSetHint and usage

- Required and acknowledged: `docs/PRODUCT_CONTRACT.md`, `docs/TECHNICAL_DESIGN.md`,
  `docs/COMPASS_BUILDER_CONTRACT.md`, `SECURITY.md`, the active MVP plan, and the active
  baseline.
- Cited by the parent plan: all required sources above.
- Missing: none.

## ImpactStatementDraft

The slice adds controller-owned persistence and recovery beneath the separate
`compass-builder` plugin. Plugin Compass remains read-only and unchanged. The public
effect is a dry-run-capable orchestration contract; live worker dispatch remains gated.

## Execution Readiness View

- Intent lock: fastest verified completion with Plugin Compass as passive advice only.
- Scope fence: Task 5 paths and minimal CLI composition.
- Baseline lock: clean `main` at `cd1f737`, synchronized with `origin/main`.
- Owner constraints: `state.py` owns persistence/resume; `lease.py` owns exclusivity;
  the skill owns user-facing orchestration guidance; CLI only composes owners.
- Compatibility boundary: Windows paths with spaces, Python 3.11+, standard library,
  explicit Git/Codex capability evidence.
- Retirement boundary: controller helpers retire only when native Codex provides
  equivalent deterministic state, worktree, and receipt contracts.
- Test obligations: corrupt/stale state, invalid resume, lease contention/staleness,
  nested-worker prohibition, and complete dry-run state coverage.
- Review gates: independent specification review, then independent quality/security
  review, then coordinator verification.
- Drift/rewind: return to plan review if Task 5 needs verifier/integrator/cleanup or live
  benchmark behavior assigned to later tasks.

## TDD Route

- Mode: off
- Decision: skipped
- Strict authority: not applicable
- Test posture: focused post-change regression plus full-suite verification
- Reason: the user approved execution, not strict RED/GREEN sequencing.

## Change Necessity and complexity

- User-visible need: safe resume and exclusive controller ownership are required before
  worktree-bound builders can execute.
- No-change option: prose and process memory cannot enforce atomic persistence, leases,
  or state-transition invariants.
- Minimum boundary: the owners and files already named by Task 5.
- Decision: code-change.

Complexity Budget:

- Artifact class: source, tests, skill/process artifacts.
- Target files: new focused state/lease owners, new focused tests/references, small CLI
  wiring.
- Current pressure: `launcher.py` is substantial but not extended; `cli.py` is small.
- Projected pressure: separate cohesive modules, each below soft pressure thresholds.
- Budget result: within-budget.
- Planned governance: split persistence, lease, skill references, and CLI composition;
  no new responsibility in existing large owners.

## Worktree necessity

No task-owned implementation worktree is created: there is no concurrent checkout
conflict or unrelated dirty state. Disposable worktrees may exist only inside isolated
temporary test repositories and must have test-owned cleanup.
