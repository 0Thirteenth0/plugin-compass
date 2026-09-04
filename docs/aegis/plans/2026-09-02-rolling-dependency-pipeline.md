# Proposed rolling dependency pipeline

## Goal

Design a versioned, experimental rolling DAG pipeline for Compass Builder that can reuse
freed worker capacity and unlock safe dependents without weakening deterministic
integration, outcome verification, isolation, recovery, or v1 compatibility.

This is a proposed implementation plan. Only G0 documentation is authorized. G1 and all
runtime work require separate approval.

## Architecture

Keep the current v1 wave-barrier controller unchanged. Introduce a parallel v2 contract
family with a pure deterministic scheduler, a dedicated durable state owner, and a narrow
v2 controller. Plugin Compass may recommend an execution mode and dispatch strategy, but
Compass Builder remains the sole scheduler, clone owner, verifier, integrator, and state
authority.

The recommended v2 dependency-completion rule is `integration-verified`: a dependent
story is not ready until every prerequisite is present in the last verified integration
SHA. Dispatch priority affects ready-queue selection only. Integration uses an immutable
topological/specification ordinal so completion timing cannot reorder merges.

## Tech Stack

- Python 3.11+ standard library
- Existing bounded `codex exec --json` worker transport
- Git object inspection, remote-free full-history clones, leases, and compare-and-swap
- Closed JSON Schema 2020-12 contracts plus matching Python semantic validators
- Repository `unittest` and temporary Git-repository harnesses

## Baseline/Authority Refs

- `feature_planning.md`, Workstreams D, E, F, and G
- `docs/COMPASS_BUILDER_CONTRACT.md`
- `docs/COMPASS_BUILDER_BENCHMARK.md`
- `docs/aegis/baseline/2026-09-01-compass-builder-baseline.md`
- `docs/aegis/plans/2026-09-02-workstreams-f1-d1.md` (frozen; read-only)
- `plugins/compass-builder/compass_builder/controller.py:514-617`
- `plugins/compass-builder/compass_builder/state.py:693-715`
- `plugins/compass-builder/compass_builder/integrator.py:245-405`
- `plugins/compass-builder/compass_builder/planner.py:29-109`
- `plugins/compass-builder/compass_builder/_state_models.py`
- `tests/integration/test_builder_worktrees.py`

## Compatibility Boundary

The following contracts and their behavior are frozen:

- `compass-builder.run-spec.v1`
- `compass-builder.wave-plan.v1`
- `compass-builder.plan-bundle.v1`
- `compass-builder.run-state.v1`
- current v1 launch, worker-output, and worker-receipt contracts
- current sequential and wave-parallel runtime semantics

New v1 runs remain wave-barrier by default. V2 is selected only by an explicit v2 input
and an experimental rolling authorization. Unknown versions or strategies fail closed.
An in-flight v2 run is never translated, downgraded, or resumed through v1 state.

## TDD Route

- Mode: auto
- Decision: strict for G1 through G6; skipped for G0 documentation and G7 report-only work
- Strict authority: explicit project request for G1 contract-first tests; recorded auto
  decision for state, scheduling, execution, and recovery behavior
- Strict signals: public contracts, persistence, state transitions, compatibility,
  subprocess orchestration, and security-sensitive recovery
- Light eligibility: none for G1 through G6
- TDD-fit exception: separately authorized live model benchmarks use prevalidated harnesses
  rather than RED/GREEN against paid execution
- Test posture: strict RED test per behavior slice; matched synthetic and live comparison
  at G7
- Reason: false progress, duplicate side effects, or a v1 semantic regression would be
  materially unsafe
- Verification: focused tests, v1 compatibility suite, Builder Git integration suite,
  repository audit profile, then separately authorized benchmark evidence

## Verification

Every slice must run its focused suite, `python -m unittest tests.test_builder_models -v`,
the affected state/controller/integration suites, `git diff --check`, and the repository
audit profile before a completion claim. G7 additionally requires the maintained matched
benchmark protocol and complete Workstream E telemetry.

## Plan Basis

### BaselineUsageDraft

- Required baseline refs: current Builder contract, benchmark contract, v1 controller,
  planner, state machine, integrator, and integration tests
- Delivered context refs: the approved F1/D1 plan and the rolling-pipeline request
- Acknowledged before plan refs: all required refs above
- Cited in plan refs: all required refs above
- Missing refs: none for G0; Workstream D command-approval binding is intentionally a G4
  dependency
- Decision: continue

### Requirement Ready Check

- Requirement source refs: rolling-pipeline request and `feature_planning.md`
- Goals and scope refs: measurable tail-latency objective and v1 freeze
- User/scenario refs: A and B start together; A unlocks C while B continues
- Requirement item refs: G0 through G7, fail-stop behavior, isolation, recovery, benchmark
- Acceptance/verification criteria refs: required behavioral/adversarial test matrix and
  graduation gates
- Open blocker questions: none for G0; the questions below must be closed before their
  owning implementation slices
- Decision: ready for planning only

### Change Necessity

- User-visible need: eliminate avoidable idle capacity caused by the slowest worker in a
  dependency wave
- No-change/non-code option: v1 wave-parallel can only reduce work inside a fixed barrier
- Why code change is necessary: current controller and state transitions do not process,
  integrate, or replace an individual completion before the entire wave finishes
- Minimum change boundary: a separate v2 contract/state/scheduler/controller path that
  reuses existing Git, launch, verification, integration, and isolation primitives
- Decision: code-change after separate approval

### Existence Check

- Proposed new surface: rolling scheduler and v2 durable state
- Existing owner/reuse candidate: v1 `controller.py`, `planner.py`, and `state.py`
- Why existing surface is insufficient: those owners encode wave-wide states and a
  `wave-verified` next-wave gate; changing them in place would change v1 behavior and
  overload already substantial owners
- Creation proof: rolling needs mixed simultaneous per-story lifecycle states and an
  event-driven decision boundary absent from v1
- Entropy/retirement impact: experimental v2 stays removable as a closed family until
  graduation; v1 remains the fallback and compatibility oracle
- Decision: add-with-proof

### Architecture Integrity Lens

- Invariant: one scheduler and one serialized integration authority per run
- Canonical owner/contract: Compass Builder v2 pipeline state; Plugin Compass remains
  advisory
- Responsibility overlap: no Unlazy scheduler, no second worktree manager, no worker-owned
  state or completion authority
- Higher-level simplification: pure decisions are separated from subprocess and Git side
  effects, allowing deterministic testing
- Retirement/falsifier: reject or remove the experimental v2 route if it cannot pass safety
  gates or achieve the measured wall-clock threshold
- Verdict: proceed with an isolated v2 design

### Plan Pressure Test

- Owner/contract/retirement: separate v2 owner and rollback to untouched v1 are explicit
- Architecture integrity/higher-level path: pure scheduler plus side-effect adapter
- Verification scope: contract, pure DAG, integration, crash recovery, and benchmark
- Task executability: each slice has fixed files, gates, commands, and a safe stop
- Pressure result: proceed to G0 only

### Complexity Budget

- Artifact class: source, contract, state, integration tests, and experimental documentation
- Target files/artifacts: new v2 modules/schemas/test modules; minimal routing changes only
- Current pressure: `controller.py` is about 650 lines and `state.py` is already a mixed,
  security-sensitive persistence owner
- Projected post-change pressure: over-budget if rolling logic is added in place; within
  budget with dedicated v2 owners
- Budget result: at-risk
- Planned governance: add `rolling_scheduler.py`, `rolling_state.py`, and
  `rolling_controller.py`; keep existing v1 modules wiring-only

### Plan-Time Complexity Check

- Target files: proposed v2 modules and narrow schema-version routing
- Existing size/shape signals: v1 controller/state already contain worker, Git, durability,
  recovery, and transition responsibilities
- Owner fit: pure ready-queue decisions do not belong in the v1 process controller
- Add-in-place risk: semantic drift in v1 and hard-to-test mixed side effects
- Better file boundary: dedicated scheduler, state, event, and controller owners
- Recommendation: add owner files

## Verified v1 barrier behavior

The v1 controller chooses a complete dependency wave and one start SHA, launches every
story through one `ThreadPoolExecutor`, and stores receipts as futures finish. The executor
context does not exit until all futures finish. Only then does the controller reject
non-green workers, mark the whole wave complete, import and verify every story in immutable
story order, and enter the serial merge loop. `StateStore.next_wave_state` accepts only
`wave-verified`, and the v1 state validator requires every branch in a verified wave to be
`integration-verified`.

This behavior is correct for v1 and must remain unchanged. It also creates tail latency:
a fast A cannot be verified, integrated, or replaced by a ready D while slow B remains
active.

## Proposed v2 contract family

Create new contracts rather than modifying v1 schemas:

- `compass-builder.run-spec.v2`: `executionMode` (`sequential` or `parallel`),
  `dispatchStrategy` (`wave-barrier` or `rolling`), host/user ceilings, explicit
  experimental authorization, stories, dependencies, scopes, gates, and validation
- `compass-builder.pipeline-plan.v2`: immutable specification order, integration ordinal,
  initial ready set, per-story effort/handoff binding, and policy digests
- `compass-builder.pipeline-state.v2`: per-story lifecycle, current verified integration
  SHA, active ownership, integration queue, active blocker, and run terminal state
- `compass-builder.pipeline-event.v2`: append-only transition evidence for dispatch,
  completion, verification, import, merge intent, merge, post-check, gate result, and block
- `compass-builder.execution-bundle.v2`: closed binding of v2 run spec, plan, host evidence,
  and planning timestamp
- `compass-builder.dispatch-record.v2`: immutable worker start SHA, prerequisite receipt and
  gate digests, model/effort, scopes, and registered clone identity

V2 may embed unchanged v1 worker-output and worker-receipt documents where their meaning
is sufficient. It must not relabel a v1 document as v2 or add fields to a v1 shape.

## Scheduler invariants

The pure scheduler consumes validated v2 plan/state and emits exactly one deterministic
decision: dispatch a bounded ordered set, wait, integrate one story, block, or complete.

A story is dispatchable only when:

1. every prerequisite is `integration-verified`;
2. a slot is free under `min(host ceiling, user ceiling, calibrated ceiling)`;
3. its scopes do not overlap any running, completed-unverified, verified-unimported,
   imported, or integration-pending story under Windows-normalized ancestor comparison;
4. it declares no shared-state mutation;
5. every required outcome gate is approved and actionable under Workstream D;
6. exact model, supported effort, registered clone, and closed worker controls are bound;
7. the current `lastVerifiedIntegrationSha` is captured as its immutable worker start SHA;
8. prerequisite worker, integration, and gate evidence digests are recorded.

The ready queue is ordered by declared priority, then immutable specification order.
Integration is ordered only by the immutable topological/specification ordinal. A verified
story that is not the next ordinal waits while eligible builders continue.

## Verification and integration flow

Each completion is processed immediately and independently:

1. bounded result/process collection;
2. worker-output and receipt shape validation;
3. controller-owned commit creation and raw-object inspection;
4. write-scope verification against the recorded start SHA;
5. approved independent story-gate execution;
6. exact-SHA branch import;
7. durable enqueue for serialized integration.

The integration lane holds one lease, checks the current HEAD by compare-and-swap, records
an immutable merge intent, merges only the next ordinal, proves ordered raw parents, runs
post-merge/root gates, and advances `lastVerifiedIntegrationSha` only after a clean pass.
Readiness is then recomputed and safe dependents may launch from that exact verified SHA.

The required A/B/C scenario is:

- A and B launch from SHA S0.
- A completes first, is independently verified, and integrates as ordinal 1 to SHA S1.
- C depends only on A, is scope-compatible with still-running B, and launches from S1.
- B remains bound to S0, later verifies and integrates as ordinal 2 against the current
  HEAD through the existing lease/CAS/merge checks.
- C waits in the integration queue until all lower ordinals are integration-verified.
- A story depending on both A and B cannot launch until both are integration-verified.

## Failure, interruption, and recovery

The first worker, verification, import, merge, gate, or post-merge failure establishes a
durable blocker and stops new dispatch and new merges. Already-running workers drain under
their existing bounded timeout. Active termination is not part of the first rolling
implementation.

Per-story state distinguishes:

- `never-launched`
- `running`
- `process-unknown`
- `worker-complete-unverified`
- `verified-unimported`
- `imported-awaiting-integration`
- `merged-awaiting-post-check`
- `integration-verified`
- `blocked`

Each side effect has an immutable event identity and expected predecessor. Resume reloads
only controller-owned state and events, reconciles Git/process evidence, and either adopts
the exact already-recorded effect, continues the next phase, or blocks. It never silently
relaunches a `running`/`process-unknown` attempt or duplicates a verification, import,
merge intent, merge, post-check, or gate receipt.

## Security and isolation

- Preserve remote-free full-history clones, disabled plugins/hooks, disabled nested
  multi-agent execution, closed stdin, exact model/effort, and controller-owned commits.
- A ready queue grants no filesystem or process authority.
- Gate commands remain executable code and require the Workstream D approval identity.
- Validate every path canonically and reject traversal, reparse escape, repository root,
  foreign clone, stale branch, and unknown event/schema values.
- Keep the integration lane serialized and lease-protected even while verification and
  workers overlap.
- Concurrency is initially capped at two; rolling never means unbounded creation.
- Plugin Compass recommends only; it neither mutates v2 state nor starts workers.

## Implementation tasks and safe slices

### G0 — Baseline and architecture decision (authorized now)

Files:

- Modify `feature_planning.md`.
- Create this plan.
- Modify `docs/aegis/INDEX.md`.

Why: record the verified barrier, v1 freeze, v2 owners, failure policy, and benchmark
before any code can change.

Verification:

```powershell
git diff --check
git diff -- docs/aegis/INDEX.md
git status --short --branch
```

Estimate: 2–4 hours. Safe stop: reviewed design, no source changes.

### G1 — Versioned v2 contracts, no execution

Proposed files:

- Create `plugins/compass-builder/schemas/run-spec.v2.schema.json`.
- Create `plugins/compass-builder/schemas/pipeline-plan.schema.json`.
- Create `plugins/compass-builder/schemas/pipeline-state.schema.json`.
- Create `plugins/compass-builder/schemas/pipeline-event.schema.json`.
- Create `plugins/compass-builder/schemas/execution-bundle.v2.schema.json`.
- Create `plugins/compass-builder/schemas/dispatch-record.schema.json`.
- Create `plugins/compass-builder/compass_builder/_rolling_models.py`.
- Create `tests/fixtures/compass_builder/rolling/` fixtures.
- Create `tests/test_builder_rolling_models.py`.
- Modify `plugins/compass-builder/compass_builder/models.py` only to register new names.

Change necessity: v1 schemas cannot represent dispatch strategy or mixed per-story state.
The minimum boundary is new closed shapes and semantic validators.

Strict steps: write failing version/closure/binding/lifecycle tests; observe missing
contracts; implement the minimum validators/schemas; rerun focused tests; run all v1
canonical fixtures byte-for-byte and prove the v1 controller tests unchanged.

Verification:

```powershell
python -m unittest tests.test_builder_rolling_models tests.test_builder_models -v
python -m unittest tests.test_builder_controller tests.test_builder_state tests.integration.test_builder_worktrees -v
```

Estimate: 6–10 hours. Safe stop: v2 contracts green; runtime remains v1-only.

### G2 — Pure deterministic scheduler engine

Proposed files:

- Create `plugins/compass-builder/compass_builder/rolling_scheduler.py`.
- Create `tests/test_builder_rolling_scheduler.py`.

Change necessity: scheduling rules must be falsifiable without subprocess, filesystem, Git,
or model side effects.

Strict steps: write failing decision tests for DAG readiness, priority/spec tie-break,
joins, capacity, scope ownership, shared state, gate approval, stale evidence, integration
ordinal, wait/block/complete, and determinism; observe RED; implement a pure
`decide(plan, state)` boundary; rerun twice with permuted equivalent inputs.

Verification:

```powershell
python -m unittest tests.test_builder_rolling_scheduler -v
```

Estimate: 6–10 hours. Safe stop: pure decisions green; no runtime wiring.

### G3 — Rolling-frontier refill

Proposed files:

- Create `plugins/compass-builder/compass_builder/rolling_controller.py`.
- Create `tests/test_builder_rolling_controller.py`.
- Modify `plugins/compass-builder/compass_builder/cli.py` only for an explicit experimental
  v2 route; leave v1 routing unchanged.

Change necessity: the current wave executor cannot refill a freed slot.

Strict steps: write a synthetic timing test where A completes before B and independent D
fills the slot; prove current v2 stub cannot refill; wire bounded same-frontier dispatch;
prove capacity never exceeds two and no dependent story unlocks.

Verification:

```powershell
python -m unittest tests.test_builder_rolling_controller tests.test_builder_controller -v
```

Estimate: 8–14 hours. Safe stop: same-start-SHA frontier refill only.

### G4 — Per-completion verification and import

Dependencies: the required Workstream D gate-enforcement slice must be green first.

Proposed files:

- Create `plugins/compass-builder/compass_builder/rolling_verification.py`.
- Create `tests/test_builder_rolling_verification.py`.
- Modify `rolling_controller.py` only to invoke the new verified boundary.

Change necessity: safe rolling cannot use a worker self-report or defer immutable evidence
until unrelated workers finish.

Strict steps: fail on forged receipts, out-of-scope edits, wrong start/head SHAs, stale
branches, missing/blocked gates, worker failure, and verifier failure; reuse existing raw
Git verification/import owners; emit one durable event per accepted phase.

Verification:

```powershell
python -m unittest tests.test_builder_rolling_verification tests.test_builder_verifier -v
```

Estimate: 8–14 hours. Safe stop: completions verify/import independently; dependencies do
not unlock.

### G5 — Serialized early integration and dependency unlocking

Proposed files:

- Create `plugins/compass-builder/compass_builder/rolling_integrator.py`.
- Modify `rolling_controller.py` and `rolling_scheduler.py` through their existing narrow
  boundaries.
- Create `tests/integration/test_builder_rolling_pipeline.py`.

Change necessity: capacity savings from dependency pipelining require a verified dependency
to enter the integration SHA before its child can receive an immutable base.

Strict steps: write A/B/C, reverse-completion, multi-parent, old-base, active-scope, CAS,
merge-conflict, and post-merge-regression failures; reuse lease and raw merge proof owners;
advance readiness only after post-check; prove deterministic ordinal integration.

Verification:

```powershell
python -m unittest tests.integration.test_builder_rolling_pipeline tests.test_builder_integrator -v
```

Estimate: 12–20 hours. Safe stop: full two-worker rolling pipeline, explicit v2 only.

### G6 — Durable recovery and bounded interruption

Proposed files:

- Create `plugins/compass-builder/compass_builder/rolling_state.py`.
- Create `tests/test_builder_rolling_state.py`.
- Create `tests/integration/test_builder_rolling_recovery.py`.

Change necessity: mixed in-flight states cannot be safely resumed by v1 wave recovery.

Strict steps: crash after each launch/completion/verification/import/intent/merge/post-check
boundary; observe blocked or duplicate behavior before implementation; add exact-event
reconciliation; prove no duplicated side effect. Keep active processes drain-only. Add a
separate cancellation sub-slice only after process identity and bounded descendant
termination are proven.

Verification:

```powershell
python -m unittest tests.test_builder_rolling_state tests.integration.test_builder_rolling_recovery -v
```

Estimate: 10–16 hours. Safe stop: idempotent fail-closed restart/resume; cancellation may
remain deferred.

### G7 — Telemetry, benchmark, and graduation

Dependencies: Workstream E native per-attempt telemetry and G1 through G6.

Proposed files:

- Create `tests/fixtures/compass_builder/rolling_benchmarks/` synthetic workloads.
- Create `tests/test_builder_rolling_benchmark.py`.
- Modify the benchmark runner only through a versioned three-arm comparison input.
- Create `docs/COMPASS_BUILDER_ROLLING_EXPERIMENT.md`.

Change necessity: `auto` cannot safely select rolling from design intuition or synthetic
timing alone.

Test A (v1 wave barrier), B (v2 rolling-frontier), and C (v2 full pipeline) on identical
snapshots, tasks, model, effort, checks, warm-ups, and alternating attempt order. Run
synthetic tests first; live model execution requires separate authorization.

Verification:

```powershell
python -m unittest tests.test_builder_rolling_benchmark tests.test_builder_compare -v
python scripts/check_repo_harness.py --profile audit --format json
```

Estimate: 8–16 engineering hours plus separately authorized live benchmark time. Safe
stop: evidence report exists and `auto` still does not select rolling.

The supplied slice ranges sum to 60–104 engineering hours. This arithmetic range replaces
the inconsistent 52–90 total and excludes live benchmark elapsed time.

## Behavioral and adversarial test matrix

At minimum prove:

- A finishes before B; B finishes before A.
- A unlocks C only after A is integration-verified.
- C depending on A and B waits for both.
- Independent D fills capacity without exceeding two workers.
- A verification failure stops new dispatch and merges.
- B failure after A integration leaves A as the last verified SHA and blocks the run.
- C overlapping still-running B waits.
- Forged success, wrong start SHA, wrong head SHA, stale branch, and foreign clone fail.
- Integration HEAD drift, merge conflict, and post-merge root-gate failure block.
- Crashes at every durable phase resume without duplicated effects.
- Event and state serialization are deterministic under reordered input observations.
- v1 sequential remains sequential; v1 explicit parallel retains the exact barrier.
- Unknown v2 versions, policies, strategies, and lifecycle states fail closed.

## Matched benchmark and graduation

Use skewed durations, wide frontiers, chains, joins, failures, conflicts, and retries. For
every arm report wall time to green integration, observed input/output/reasoning/cached
tokens, first-pass acceptance, retries, verifier rejections, merge failures, conflicts,
scope violations, and human interventions. Missing Workstream E usage makes the token
efficiency verdict incomplete.

Rolling graduates only if eligible workloads show at least 20% lower median wall time,
equal or better first-pass acceptance, no increase in human intervention, no unresolved
conflicts/scope violations, and no weakened security, accessibility, validation, gate, or
recovery behavior. A separate reviewed policy change is required before `auto` may select
rolling. Concurrency stays capped at two until a separate higher-concurrency benchmark
passes the same gates.

Rollback is routing-only: disable explicit v2 activation and retain all v2 evidence for
inspection. Never translate in-flight v2 state into v1. The untouched v1 path remains the
production fallback.

## Open architecture questions and recommended defaults

1. Dependency readiness: require `integration-verified`, not story verification alone.
2. Priority versus integration: priority orders dispatch candidates; immutable
   topological/specification ordinal orders integration.
3. Durable evidence layout: append immutable event files and derive a canonical bounded
   state snapshot; do not use one rewritable event list.
4. Gate approval identity: bind the exact Workstream D command, marker, directory, shell,
   platform, environment, and transitive artifact digest before G4.
5. Restarted active processes: classify as `process-unknown` and block; do not infer death
   or relaunch from absent in-memory state.
6. Cancellation: retain drain-with-timeout initially; assess active termination as a
   separately authorized G6 sub-slice.
7. Experimental CLI: require both a v2 schema and an explicit rolling flag; never let v1
   `auto` opt into v2.
8. Token overhead threshold: report the measured trade-off in G7 and require a user policy
   decision before tokens influence automatic routing.

These are recommended design defaults. G1 approval should ratify items 1–3 and 7; G4
approval must ratify item 4; G6 controls items 5–6; G7 controls item 8.

## Execution Readiness View

- Intent Lock: reduce verified wall-clock tail latency without weakening correctness
- Scope Fence: Workstream G only; no F, Ponytail, brand, environment-sync, or Unlazy work
- Baseline Lock: v1 contracts and wave behavior remain byte/behavior compatible
- Approved Behavior: G0 planning only
- Owner/Contract Constraints: Compass Builder owns v2 execution; Plugin Compass advises
- Compatibility Boundary: no v1 semantic edits or in-flight translation
- Retirement Boundary: experimental v2 can be disabled without removing v1
- Task Batches: G0, then separately approved G1→G2→G3→G4→G5→G6→G7
- Test Obligations: contract, pure scheduler, integration, crash recovery, v1 regression,
  matched benchmark
- Review Gates: D enforcement before G4; E telemetry before G7; security review before
  live activation
- Drift/Rewind Rules: any v1 behavior change or duplicate side effect stops and returns to
  the last green slice
- Evidence Required Before Completion: fresh focused suites, v1 suite, audit profile, and
  benchmark receipts where applicable
- Advisory Boundary: method-pack execution guidance only; not GateDecision,
  PolicySnapshot, or completion authority

## Required approvals

Separate explicit approval is required for:

- G1 or any source/schema/test/fixture edit beyond this plan;
- exact gate-command execution and its trust binding;
- active process cancellation;
- live/paid model benchmarks;
- plugin installation, hook enablement, cache mutation, commit, push, publication, or
  cleanup.

## Risks and retirement

Primary risks are v1 drift, duplicate side effects after crashes, unsafe newer-base/older-
base overlap, completion-timing nondeterminism, command-oracle mismatch, and state-owner
sprawl. The design controls them through an isolated v2 family, pure decisions,
integration ordinals, active scope ownership, immutable events, Workstream D gates, and
fail-stop recovery.

V2 remains experimental and removable until graduation. If it fails safety gates or does
not reach the speed threshold, retain v1 and archive the evidence; do not keep an unused
automatic routing branch.

## Execution Route

- Decision: no execution; planning stop
- Evidence: only G0 documentation is authorized
- Fallback: retain the current v1 wave-barrier implementation
- User confirmation required: yes — explicit G1 approval
