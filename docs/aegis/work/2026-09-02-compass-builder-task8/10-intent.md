# Compass Builder Task 8 intent

## Requested outcome

Continue the approved Compass Builder MVP with an executable controller run owner, a
fail-closed paired comparator, and a deterministic synthetic benchmark runner.

## Slice Card

- Goal: prove the complete public run, paired-attempt, receipt, ledger, aggregate, and
  comparison route without live model usage, then expose `compare` and `benchmark`
  through the public CLI.
- Parent plan/spec: `docs/aegis/plans/2026-09-01-compass-builder-mvp.md`, Task 8,
  and the validation/benchmark section of `docs/COMPASS_BUILDER_CONTRACT.md`.
- Files: planned Task 8 benchmark, runner, integration, fixture, test, and documentation
  paths; one user-authorized `compass_builder/controller.py` owner; minimal `cli.py`,
  package-export, repository-harness, CI, and validation-guide wiring; these lifecycle
  records.
- Boundary: source implementation and fake deterministic transport only. No live Codex
  worker, real benchmark, model consumption, installation, plugin cache mutation,
  concurrency-cap increase, external service, or automatic conflict repair.
- Verification: focused controller/comparator/runner tests, direct worktree integration,
  public fixture comparator, unit/audit profiles, full discovery, static dependency and
  workflow checks, independent specification and quality review.
- Stop: a second controller owner, fabricated green receipt, excluded failed attempt,
  unbounded subprocess, interactive repair path, unregistered repository mutation,
  incomparable controls treated as performance evidence, or any live-model action.

## BaselineReadSetHint and usage

- Required and acknowledged: `docs/PRODUCT_CONTRACT.md`, `docs/TECHNICAL_DESIGN.md`,
  `docs/COMPASS_BUILDER_CONTRACT.md`, `SECURITY.md`, the active MVP plan, the initial
  Builder baseline, and Task 7 intent/checkpoint/evidence/reflection.
- Cited by the parent plan: all required sources above.
- Active `AGENTS.md` or `CONTEXT.md`: none present in the repository.
- Task 7 external readback: corrective commit `1b24ef9` passed the complete hosted
  Windows Python 3.11 workflow before this slice began.
- Missing: none.

## ImpactStatementDraft

This slice closes the missing production owner between the already implemented launch,
verification, integration, state, and cleanup components, then makes benchmark evidence
derive from that same public run route. Plugin Compass remains read-only and the
two-builder calibration ceiling remains unchanged.

## Execution Readiness View

- Intent lock: fastest verified completion and comparable evidence, not maximum
  concurrency, minimum cost, or a favorable performance claim.
- Scope fence: Compass Builder controller execution plus Task 8 comparator/benchmark
  mechanics and their repository-local validation wiring only.
- Baseline lock: clean synchronized `main` at
  `1b24ef9fe8af55f31491cb64a6ea44de8d9da3f9` and the accepted product, security,
  Builder, and parent-plan contracts.
- Owner constraints: `controller.py` composes existing state/launch/verify/integrate
  owners; `benchmark.py` compares validated receipts; `benchmark_runner.py` owns paired
  scheduling, disposable fixture copies, ledger, and atomic benchmark publication;
  `cli.py` only parses and routes.
- Compatibility boundary: Windows, Python 3.11+, Git worktrees and paths with spaces,
  standard library only, no shell interpolation, deterministic canonical JSON.
- Retirement boundary: `run --dry-run` remains an explicit projection mode, not a
  hidden fallback. Native Codex scheduling/receipts may retire equivalent controller
  logic only after contract-equivalent evidence exists.
- Task batches: controller/public-run owner; comparator; paired runner/ledger/fixtures;
  documentation/validation/review/closeout.
- Test obligations: public route, exact controls, paired ordering, fresh repository per
  arm, all-attempt accounting, ledger hash chain, safety precedence, exact 20% boundary,
  atomic output, timeout/failure retention, and no live transport in tests.
- Review gates: implementation inspection, independent specification review,
  independent quality review, then fresh coordinator verification.
- Drift/rewind: return to plan review if implementation needs a daemon, another policy
  engine, interactive repair, external package/service, unbounded worker, or a change to
  Plugin Compass.
- Advisory boundary: source evidence cannot authorize or substitute for Task 9's live
  benchmark and package/install gates.

## Authorization and TDD route

- Scope expansion authorization: the user explicitly authorized adding the missing
  dedicated controller executor and minimal public-run wiring after the plan gap was
  reported.
- TDD Mode: off
- Decision: skipped
- Strict authority: not applicable
- Test posture: minimum cohesive implementation followed by focused, integration, and
  full post-change verification, inherited from the approved parent plan.

## Change Necessity and complexity

Change Necessity:

- User-visible need: Task 8 cannot truthfully benchmark the public run path while the
  current CLI permits only a dry-run projection.
- No-change / non-code option: comparator-only documentation would leave the public
  benchmark command unable to generate valid receipts and make Task 9 impossible.
- Why code change is necessary: the existing launch, verification, integration, state,
  and cleanup owners require one lifecycle composer; paired execution, ledger evidence,
  and metric comparison require deterministic executable owners.
- Minimum change boundary: one controller composer, one comparator, one benchmark
  runner, minimal CLI/validation wiring, focused tests/fixtures, and documentation.
- Decision: code-change.

Complexity Budget:

- Artifact class: controller source, benchmark source, maintained tests, fixtures,
  documentation, and validation wiring.
- Target files: new single-purpose `controller.py`, `benchmark.py`, and
  `benchmark_runner.py`; planned tests/integration/fixtures/docs; small routing edits.
- Current pressure: state is 726 lines, launcher 541, integrator 488, verifier 441, and
  model tests 806; none receive new responsibilities.
- Projected post-change pressure: within budget when lifecycle composition, comparison,
  and paired orchestration remain separate and new maintained files stay below the
  800-line pressure signal.
- Budget result: within-budget.
- Planned governance: no controller logic in `cli.py`, no benchmark policy in models,
  no generic task-runner framework, and no duplicate Git/process/security primitive.

Pre-Edit Owner-Fit Decision:

- Edit intent: new-responsibility.
- Owner fit: the user-authorized controller file closes an intended but missing owner;
  comparator and runner files are assigned by Task 8.
- Safer edit boundary: compose existing public component APIs and extend them only when a
  direct missing seam is proven; do not enlarge `state.py` or Plugin Compass.
- Decision: add owner files with wiring-only edits elsewhere.

## Worktree necessity

No task-owned worktree is created. The checkout is clean, implementation has one writer,
reviews are read-only, and the coordinator alone owns Git lifecycle mutation.
