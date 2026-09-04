# Workstreams F3 and D3 implementation plan

## Goal

Implement the next approved safe slice: connect bounded standalone-skill records to
Plugin Compass inventory, assessment, recommendation, and prompt outputs; and connect
Compass Builder outcome gates to trusted operator decisions, story verification,
post-merge root verification, durable evidence, and completion authority.

## Architecture

Plugin Compass keeps authoritative plugin discovery unchanged, then composes explicitly
configured standalone roots through the existing F2 adapter and a new source-neutral
skill-decision owner. Standalone skills never become fake plugins and discovery never
invokes them.

Compass Builder keeps the existing closed v1 execution bundle and controller path
unchanged. Gated execution is opt-in through a closed v2 bundle containing a pristine D1
ledger. A dedicated gate-enforcement owner obtains just-in-time capabilities from an
in-process trusted operator provider, runs D2 gates serially, and publishes immutable
evidence bound to the exact story or integration commit. The controller remains the sole
scheduler, importer, integrator, state owner, and completion authority.

## Tech Stack

- Python 3.11+ standard library
- Existing Plugin Compass F1 source-neutral records and F2 bounded adapter
- Existing Compass Builder D1 ledger, D2 approval capability, snapshot, and runner
- Existing controller, verifier, integrator, state store, and immutable artifact journal
- `unittest`, JSON Schema fixtures, temporary Git repositories, and repository audit

## Baseline/Authority Refs

- `feature_planning.md`, approved Workstreams F and D decisions
- `docs/aegis/plans/2026-09-02-workstreams-f1-d1.md`
- `docs/aegis/plans/2026-09-02-workstreams-f2-d2.md`
- `docs/aegis/plans/2026-09-02-rolling-dependency-pipeline.md`
- User authorization on 2026-09-02: proceed to the next recommended F3 and D3 slice
- Current HEAD at task start: `87d0dc8c4163577dd968549b37fe6cd3b745cc62`
- Task-start worktree: dirty with the preserved F1, D1, G0, F2, and D2 changes listed by
  `git status --short`; no cleanup, reset, commit, or push is authorized

## Compatibility Boundary

- `codex plugin list --json` remains the only authority for plugin identity and
  enabled/installed state. An inconclusive plugin inventory remains inconclusive and is
  not rescued by standalone discovery.
- Standalone roots are explicit caller configuration. Do not infer roots from user home,
  environment variables, plugin caches, or arbitrary directories.
- Plugin-only calls with no configured standalone roots preserve existing selection
  behavior. Existing nested plugin capabilities and plugin-keyed outputs remain during
  the versioned migration.
- Plan output advances to v5, inventory to v3, and prompt to v3 using additive
  source-neutral skill assessment, ambiguity, recommendation, and discovery fields.
- Existing Compass Builder `plan-bundle.v1` behavior remains unchanged. Outcome-gate
  enforcement requires `plan-bundle.v2`; a v2 run without a trusted operator provider
  launches no workers and fails closed.
- A CLI flag or repository file cannot self-assert human approval. This slice exposes the
  trusted in-process host/provider boundary; the standalone CLI recognizes v2 but cannot
  execute it without such a provider.
- No scheduler, sync engine, skill invocation, plugin/cache mutation, live gate run,
  installation, hooks, benchmark, cleanup, commit, push, or publication is authorized.

## TDD Route

- Mode: off
- Decision: strict
- Strict authority: the standalone-skill requirement explicitly requires test-driven
  implementation, and the approved F1/D1 and F2/D2 plans carry strict RED/GREEN forward
- Sequence: focused failing behavior tests, witnessed RED, minimal implementation,
  focused GREEN, independent specification review, independent quality/security review,
  controller-owned integration tests, full repository audit
- Live safety: use fixtures, fake providers, bounded subprocess fakes, and temporary Git
  repositories only; do not execute a live repository-defined gate

## Requirement readiness

- Intent: make discovered standalone skills useful in every read-only Plugin Compass
  decision surface and make outcome gates authoritative in opt-in Builder runs.
- In scope: F3 and D3 only.
- Out of scope: F4 adversarial release closure, E1 telemetry work, G1 rolling scheduling,
  native UI brokerage, automatic live resume, cancellation, synchronization, and skill
  execution.
- Dependencies: F1, F2, D1, and D2 are implemented and independently reviewed; G4 waits
  on D3 but is not started here.
- Decision: ready to implement under the compatibility and safe-stop gates below.

## Change necessity and existence check

- F2 currently stops at `discover_standalone_skills`; CLI inventory, planning, ranking,
  prompt generation, and ambiguity handling never receive its records.
- D2 currently stops at `run_approved_gates`; the controller imports before worker
  verification and neither verifier nor integrator consumes outcome-gate evidence.
- No existing source-neutral skill-decision owner or Builder gate-enforcement/evidence
  owner exists. Reuse the F1/F2 and D1/D2 primitives rather than duplicating them.
- Minimum change: add the two dedicated owners, versioned output/bundle contracts, and
  wiring-only edits in existing CLI/decision/controller/verifier/integrator owners.

## F3 tasks

1. RED: add source-neutral decision tests for eligibility, trust/readiness, stable
   ranking, duplicate-name ambiguity, exact qualified resolution, combined minimal
   coverage, and plugin-only compatibility.
2. RED: add CLI/contract tests for explicit user/project/system roots on `inventory`,
   `assess`, `recommend`, and `prompt`; deterministic v3/v5 output; degraded diagnostics;
   paths with spaces; and zero execution or writes outside test temporaries.
3. Repair F2 logical-root collisions: exact duplicate declarations may deduplicate, but
   one logical source identity mapped to different paths reports
   `conflicting-root-identity` and scans neither path.
4. Add source-neutral skill assessment, recommendation, and ambiguity models plus a
   dedicated `skill_decision.py` ranking owner. Never use filesystem traversal order or
   an undocumented source-precedence rule.
5. Add repeatable, two-argument explicit-root flags and optional exact qualified skill
   selection. Unknown selections fail; ambiguous bare-name matches select none and name
   every sorted candidate.
6. Compose packaged and standalone skills in decision output. A standalone winner or an
   unresolved skill collision suppresses duplicate legacy plugin coverage; selected
   packaged skills retain their parent-plugin recommendation for compatibility.
7. Update v5 recommendation, v3 inventory, and v3 prompt contracts and renderers while
   preserving source, trust, metadata, readiness, and provenance in every skill result.

## D3 tasks

1. RED: prove v1 sequential/parallel behavior is unchanged; v2 validates one pristine
   pending ledger bound to the same run and known stories; missing provider launches no
   worker; and raw repository/worker/mapping data cannot approve execution.
2. Add a closed `plan-bundle.v2` contract that extends the current immutable execution
   inputs with `outcomeGateLedger`. Do not add optional gate fields to v1.
3. Add a trusted `OperatorGateProvider` boundary. Request each command or manual decision
   just in time for one exact gate, workspace, and target SHA; never persist or replay the
   authority capability. Tighten D2 capabilities against reuse.
4. Add append-only, hash-chained gate-evidence receipts binding the run, gate definition,
   scope/story, exact target SHA, workspace, operator-decision audit digest, execution
   identity, outcome, sequence, and prior receipt. Fold only exact valid evidence.
5. Move the first independent worker verification before branch import. Run required
   story gates in the registered isolated clone at its verified `headSha`, then import
   only after all required story gates are met. Failure leaves the destination ref absent
   and records a verification-phase handoff.
6. Run root gates after an exact merge is durably recorded and existing integration
   checks pass, but before `integration-verified`, `lastVerifiedIntegrationSha`, or final
   completion advances. Failure retains the merged HEAD and records a post-merge-check
   handoff.
7. Required pending, unmet, blocked, abandoned, denied, or unavailable decisions block.
   Optional non-met gates retain truthful evidence without granting completion. Manual
   review uses a distinct sealed decision bound to the exact review artifact digest.
8. Reuse only an exact durable `met` receipt after a crash; changed SHA, definition,
   workspace, environment, executable, references, or review artifact invalidates it.
   A non-met attempt always requires a fresh operator decision.

## Complexity Budget

- Artifact class: source, maintained tests, public contracts, and execution plan
- F3 pressure: `decision.py` is about 687 lines and already owns plugin policy; add only
  composition there and put new skill policy in `skill_decision.py`. `cli.py` receives
  parsing/wiring only.
- D3 pressure: `controller.py` is about 656 lines and owns lifecycle; add wiring-only
  calls and put approval/evidence/folding in `gate_enforcement.py` and validation in
  `_gate_evidence_models.py`. Keep state-machine ownership in current modules.
- Projected pressure: at-risk because controller and integration tests are large, but no
  new policy owner is added to either overloaded file.
- Budget result: at-risk, governed by new cohesive owners, narrow wiring, disjoint lanes,
  and mandatory independent quality review.
- Retirement: v1 remains supported; compatibility fields may retire only in a later
  separately authorized schema migration after consumers move to source-neutral data.

## Verification

- F3 focused model, discovery, decision, CLI, schema, rendering, and determinism tests
- D3 focused bundle, approval-reuse, evidence-chain, enforcement, controller, verifier,
  integrator, and temporary-repository tests
- Adjacent Plugin Compass and Compass Builder regression suites
- `python scripts/check_repo_harness.py --profile audit --format json`
- `git diff --check`
- Independent specification and code-quality/security approvals for each lane
- Explicitly report the four existing POSIX-only D2 runtime skips on this Windows host

## Safe stop

Stop after F3 and D3 are green, reviewed, documented, and pass the repository audit.
Report changed files, tests, results, remaining limitations, and restart requirements.
Do not start F4, E1, G1, UI approval brokerage, live gate execution, installation, hooks,
benchmarking, cleanup, commit, push, or publication without separate authorization.

## Implementation outcome

Completed on 2026-09-03 within the authorized F3/D3 boundary.

- F3 connects explicit bounded standalone user, project, and system roots to inventory,
  assessment, recommendation, and prompt output through source-neutral skill records.
  Qualified identities, ambiguity, provenance, trust/readiness, deterministic ordering,
  and an exact bounded minimum-cardinality cover remain distinct from plugin identity.
- D3 adds the closed opt-in v2 gate lifecycle while preserving v1. Story verification
  and gates precede import; existing integration checks precede root gates; exact required
  evidence precedes verified-state and completion advancement.
- The trusted provider owns explicit genesis initialization, monotonic checkpoints,
  receipt sealing/authentication, consumed approval IDs, and durable per-attempt command
  reservations. Scope-stable execution keys and unique attempt keys prevent uncertain
  replay without blocking a separately approved retry after authenticated non-met
  evidence.
- Detached command audits are closed records validated with their recorded platform.
  The JSON Schema and Python validator agree on path, marker/artifact, platform/isolation,
  Windows direct-execution, and exact POSIX source/staged mode semantics.
- No skill was invoked, installed, copied, edited, or synchronized. No live repository
  gate ran, and no scheduler, executor, UI broker, hook, or installed-cache mutation was
  added.

## Review outcome

- F3 independent specification and quality/security reviews: approved.
- D3 independent specification review: approved after per-attempt retry reconciliation
  and the direct v1 loader boundary were repaired test-first.
- D3 independent quality/security review: approved after crash-safe reservations,
  checkpoint-loss handling, pre-publication authentication, detached-audit closure,
  cross-platform schema parity, and the standard-library-only test boundary were repaired
  test-first.
- The review loop deliberately returned to RED for every actionable finding; no finding
  was waived.

## Verification outcome

- F3 focused/adjacent controller run: 88 tests passed.
- D3/D2/controller/model/state adjacent run: 115 tests, 112 passed and 3 expected
  POSIX-only skips.
- D3 verifier/integrator/worktree run: 49 tests, 48 passed and 1 expected POSIX-only
  skip.
- Complete repository discovery: 361 tests, 357 passed and 4 expected POSIX-only skips.
- Final repository audit: `status=passed`, `validationLevel=full`, every ordered check
  passed, including schemas/models, disposable-Git integration, harness self-tests, the
  complete suite, and `git diff --check`.

## ADR backfill check

No formal ADR was added. This repository has no established ADR directory or index, and
the installed Aegis package's optional ADR-gate references were unavailable. The decisions
are additive, reversible, and already owned by `feature_planning.md`, the product and
Builder contracts, the technical design, this plan, and the active F3/D3 baseline. Those
authoritative surfaces were synchronized, so a second decision record would duplicate
rather than clarify authority.

## Complexity closure

- `plugin_compass/decision.py`: 811 lines; legacy plugin-keyed compatibility composition
  is the retirement target after consumers migrate to source-neutral output.
- `plugin_compass/adapters/standalone.py`: 856 lines; bounded discovery remains cohesive,
  but split traversal mechanics from result assembly if F4 adds new policy there.
- `compass_builder/controller.py`: 787 lines; D3 added lifecycle wiring only and kept gate
  policy in dedicated owners.
- `compass_builder/gate_enforcement.py`: 774 lines; the provider/evidence transaction
  boundary remains cohesive but is at the soft-pressure threshold.
- `tests/test_builder_d3_gates.py`: above 1,200 lines; split by approval, evidence, and
  lifecycle concerns before adding another gate workstream.

The slice remains at-risk but acceptable: production policy was not added to the
controller, independent reviews approved the boundaries, and the full audit is green.
Refactoring these files is deferred because doing it inside this security-sensitive slice
would expand change surface without improving its acceptance outcome.

## Remaining limitations

- Four POSIX-only runtime cases remain skipped on Windows and require Linux execution
  evidence before cross-platform runtime completion is claimed.
- A production Codex host provider and UI approval broker are not implemented; v2 cannot
  run through the standalone CLI without that trusted host boundary.
- F4 adversarial release closure, E1 telemetry, G1 rolling scheduling, controlled speed
  benchmarks, installation, and installed-copy validation remain separately authorized
  future work.
