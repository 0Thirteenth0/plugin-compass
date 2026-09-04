# Compass Builder Product Contract

## Product relationship

Compass Builder is a separate companion plugin to Plugin Compass. Plugin Compass remains
read-only decision support; Compass Builder owns the orchestration workflow that applies
Plugin Compass recommendations through isolated top-level Codex worker clones.

Working identifiers:

- Plugin ID: `compass-builder`
- Skill ID: `compass-builder`
- Category: Productivity
- Initial version: `0.1.0`
- Optimization objective: fastest verified completion

The design adapts the MIT-licensed `aronprins/codex-loop` skill at commit
`823c4c75dede036278ac6de71b138a3d2a799a64`. The upstream copyright and license
notice must remain with adapted material.

## Goal

Reduce median wall-clock time from an approved story set to a green integrated commit
without weakening acceptance checks, increasing human intervention, or allowing
unresolved scope and merge violations.

## User outcomes

Compass Builder must let a user:

1. Run an approved story set in `auto`, `sequential`, or `parallel` mode.
2. Treat `auto` as a passive policy decision that chooses sequential or parallel mode.
3. Receive a deterministic explanation for the selected mode and concurrency.
4. Run independent parallel builders in remote-free, full-history Git clones outside the
   integration repository.
5. Preserve a safe sequential path for small, overlapping, ambiguous, or weakly
   validated work.
6. Apply Plugin Compass's proposal-only, lowest-adequate effort recommendation to each
   worker while preserving the selected model.
7. Stop before dispatch when dependencies, write ownership, validation, or host support
   are insufficient.
8. Resume from durable run state without treating worker self-reports as completion
   evidence.
9. Compare sequential and parallel outcomes with reproducible benchmark receipts.
10. Opt into story and root outcome gates whose trusted evidence is required before
    import, integration verification, and completion.

## Invocation contract

Codex skill invocation is the user-facing surface. The skill accepts natural-language
requests equivalent to:

- `Run Compass Builder in auto mode.`
- `Run Compass Builder sequentially.`
- `Run Compass Builder in parallel.`

The deterministic helper exposes:

```text
python plugins/compass-builder/scripts/compass_builder.py doctor --repo PATH --spec PATH --native-capabilities PATH
python plugins/compass-builder/scripts/compass_builder.py plan --repo PATH --spec PATH --mode auto|sequential|parallel --native-capabilities PATH
python plugins/compass-builder/scripts/compass_builder.py run --repo PATH --plan PATH
python plugins/compass-builder/scripts/compass_builder.py resume --repo PATH --run-id ID
python plugins/compass-builder/scripts/compass_builder.py verify-worker --repo PATH --plan PATH --receipt PATH
python plugins/compass-builder/scripts/compass_builder.py compare --sequential PATH --parallel PATH
python plugins/compass-builder/scripts/compass_builder.py benchmark --fixture PATH --sequential-plan PATH --parallel-plan PATH --pairs 5 --timeout-ms N --output PATH
python plugins/compass-builder/scripts/compass_builder.py cleanup --repo PATH --run-id ID
```

`plan` emits a closed `compass-builder.plan-bundle.v1` containing the normalized run
spec, wave plan, host-capability snapshot, planning timestamp, and canonical repository
identity. Save that output unchanged and pass it to `run --plan`. `run` persists the same
bundle with controller state so `resume --run-id` reloads the immutable inputs rather
than accepting caller replacements. `run --dry-run` and `resume --dry-run` persist
validated controller transitions without starting a model worker or performing Git
integration. An authorized `run` without `--dry-run` creates registered, remote-free
worker clones in a deterministic temporary root outside the integration repository,
launches one dependency wave at the planned concurrency, creates one scoped commit after
each worker exits, independently verifies every result, and integrates verified commits
serially. Live resume
remains unavailable in the MVP command surface; `resume` is still dry-run-only.

Outcome gates do not reinterpret v1. A caller that needs them must supply the separate
closed `compass-builder.plan-bundle.v2` contract, which adds one pristine pending
`outcomeGateLedger` bound to the same run and known stories. V2 execution additionally
requires a trusted in-process `OperatorGateProvider`; the standalone CLI has no approval
broker and fails before dispatch. The provider receives the full validated gate
definition and its digest just in time for one exact canonical workspace, scope, and
immutable target SHA. A repository file, worker response, command-line flag, or raw
mapping cannot grant approval.

`doctor`, `plan`, `verify-worker`, and `compare` are read-only. Authorized live `run`,
`benchmark`, and `cleanup` commands may create registered worker clones, launch
top-level Codex workers, update controller-owned run state, and perform gated serial Git
integration. `benchmark` consumes multiple model-backed runs and therefore requires
explicit authorization separate from source implementation.
They never mutate installed plugins. The runner must bind each worker with the locally
verified `codex exec -C <checkout>` surface; an in-session prompt alone is not checkout
isolation.

The invoking Codex skill captures the current native model catalog/tool metadata in the
`native-capabilities` input. The snapshot identifies its Codex version, selected model,
supported effort values, capture source, capture time, and a closed reasoning-config
proof naming exact key `model_reasoning_effort` plus its native evidence digest. `doctor`
validates and hashes that snapshot together with CLI evidence. The reasoning-config proof
is separate from `cliEvidenceDigest`; generic `-c key=value` help never proves a key or
value. Version/model mismatch or missing native metadata fails closed.

## Mode selection

### Sequential

Sequential is required when any of these is true:

- fewer than two stories are dependency-ready;
- declared write scopes overlap or are missing;
- a story may mutate shared runtime state;
- decisive task-specific verification is unavailable;
- the working tree is not clean for an isolated wave;
- isolated Git checkout creation or an enforceable worker working-directory binding is
  unavailable;
- the ready set has fewer than two eligible stories or its estimated parallelizable work
  is below the versioned coordination threshold;
- a prior wave stopped on a conflict, scope violation, or integrated failure.

### Parallel

Parallel is eligible only when every story in the proposed wave has satisfied
dependencies, pairwise-disjoint declared write scopes, isolated clone ownership, and
actionable acceptance checks. Workers may edit only their assigned clone and may not
mutate Git. The controller owns the one-story commit, exact branch import, shared state,
the integration branch, serial merges, and integrated verification.

Each worker is a top-level `codex exec` process bound to its registered clone with
`-C`, an exact model, a Plugin Compass-recommended supported effort, `--disable
multi_agent`, and a structured output schema. If the installed Codex CLI cannot prove
those options, parallel mode is structurally unavailable. In-session
`collaboration.spawn_agent` is not a parallel builder path unless its live schema later
adds an enforceable checkout or working-directory field.

An explicit parallel request changes preference, not safety gates. If a hard gate fails,
the run stops with evidence or falls back to sequential only when the spec permits it.

### Auto

`auto` chooses between sequential and parallel using the rules above. Users do not invoke
Plugin Compass separately. Before mode selection, `plan` converts each story's declared
complexity, ambiguity, risk, and validation strength into a versioned Plugin Compass
agent-task, obtains a handoff proposal, and records the proposed effort and handoff digest
in the plan. The same planning transaction then applies the coordination rule below.
Missing, gated, or mismatched handoff evidence prevents parallel planning.

The MVP uses versioned `coordination-policy.v1`: map the proposed worker effort bands
`low`, `medium`, `high`, and `xhigh-or-above` to work units `1`, `2`, `3`, and `4`.
For a safety-eligible ready wave, compute `sum(units) - max(units)`. Parallel is selected
only when that value is at least `2`; otherwise auto selects sequential. This is a
deterministic coordination heuristic, not a latency claim. The benchmark may replace its
threshold only through a versioned policy update with comparable receipts.

The development default is two concurrent builders. The runtime must represent a
separate host/user concurrency ceiling so later versions can raise the calibrated limit
without changing story contracts. The scheduler may choose fewer builders than the
ceiling. It must never interpret "as many as needed" as unbounded concurrency.

## Story and run contracts

Each run has a collision-resistant ID, an explicit base ref resolved once to an immutable
base SHA, integration branch, initial integration SHA, mode, concurrency ceiling,
validation commands, exact model, effort-policy version, and ordered stories. Each story
requires:

- stable `id`, `title`, and bounded description;
- `dependsOn` story IDs;
- repository-relative `writeScopes` with no traversal or absolute paths;
- acceptance checks and executable validation commands or an independent review path;
- explicit shared-state declaration;
- explicit complexity, ambiguity, risk, and validation-strength classifications used to
  obtain its Plugin Compass effort proposal;
- priority and completion state.

The deterministic planner emits a versioned wave plan. Run state lives under the
controller checkout's ignored `.compass-builder/runs/<run-id>/` directory and is updated
atomically by the controller only. Preflight requires `.compass-builder/` to be ignored,
untracked, and absent from the repository index. It verifies the state root with
`git check-ignore` and canonically contains each external worker clone under the
controller's deterministic temporary root before mutation. Its run-level state machine is:

```text
planned -> dispatching -> wave-workers-complete -> wave-merging
        -> wave-integrated-unverified
        -> wave-merging (next branch) | wave-verified
        -> dispatching (next wave) | completed
```

Any state may transition to `blocked`; `resume` validates the recorded repository,
immutable SHAs, registered paths, lease, and last completed transition before continuing.
`blocked` may transition only to the `resumeState` stored in its active blocker record;
the blocker is appended to durable history before the active field is cleared.
The integration branch is protected by a controller lease plus compare-and-swap checks
that require its current HEAD to equal the recorded expected SHA before every merge and
state transition.

Run state records `currentWaveIndex`, the actual CAS `expectedIntegrationSha`, the last
clean `lastVerifiedIntegrationSha`, a `runBindingDigest`, nullable `activeBlocker`, bounded
append-only `blockerHistory`, and every wave's ordered branch-integration ledger.
`runBindingDigest` is `sha256:` plus SHA-256 of canonical UTF-8 JSON bytes for the closed
object `{"runSpec": normalizedRunSpec, "wavePlan": normalizedWavePlan}`; the public
contract helper is the only owner of this formula.

Each blocker record contains a stable blocker ID, source run state, phase, nullable story
ID, reason, evidence digest, and validated resume state. Supported phases distinguish
pre-dispatch, dispatch, worker, verification, pre-merge, post-merge check, and controller
failures. Post-merge check failure retains the merge SHA and attempted-check digest,
records the actual expected HEAD as the merge SHA, leaves the last verified SHA unchanged,
and resumes verification without re-merging. Controller-level blockers need not corrupt a
branch entry.

Each branch entry records worker status, verification status, pre-merge expected SHA,
merge SHA, controller-check digest, post-check expected SHA, and integration status.
Within a wave, every verified branch moves through `pending`, `worker-verified`, `merged`,
and `integration-verified` in plan order. Resume validates the complete recorded SHA chain
and continues at the first non-`integration-verified` entry; it never re-merges or skips a
partially integrated branch. After an intermediate branch becomes integration-verified,
the run returns to `wave-merging` for the next ordered branch. `wave-verified` advances to
the next wave only after every entry is integration-verified.

The controller records a versioned worker receipt containing the registered checkout
(the schema retains the legacy `worktree` field name), branch, exact model and effort,
base/head SHAs, controller-owned commit, Git-derived changed files,
checks, elapsed time, status, and blocker when present. Receipt claims never replace Git
object inspection or controller-run validation at the worker SHA.

Every worker attempt also produces exactly one immutable
`compass-builder.worker-usage.v1` record and one finalized public `worker-usage` event.
The record binds the run, story, attempt, exact model, effort, and canonical digest of a
strictly decoded controller-owned launch record; it binds the worker-receipt digest when
a validated receipt exists. Attempt-one launch evidence retains `<story>.json`; attempt
two uses the collision-free `__attempt-2__<story>.json` name. Missing terminal usage is
explicit rather than zero-filled. Invalid private transport telemetry and an invalid or
cross-launch receipt use the closed `invalid-transport-telemetry` and
`worker-receipt-binding-failed` reasons, discard untrusted counts, and retain a null
receipt digest. Receipt binding compares run, story, branch, worktree, exact model,
effort, and base SHA against the validated launch; the receipt base SHA must equal the
launch `workerStartSha`. Telemetry persistence failure blocks the run before public
emission. A launch record authorizes usage only after restart-safe validation against
the canonical durable plan bundle, its host and reasoning evidence, the registered
checkout path, the story wave's recorded start SHA, the controller-owned Git isolation
environment, exact prompt and bundled worker schema, and the plan's effort and handoff
evidence. Attempt two additionally requires the actual canonical attempt-one digest and
one matching `compass-builder.retry-evidence.v1` journal record. That closed record
binds the run, story, second attempt, evidence digest, and predecessor launch digest;
only `source=controller` plus `kind=reasoning` authorizes the sole higher-effort retry.
Generic failure records cannot authorize a retry. Missing, duplicate-identity,
ambiguous, or noncanonical authority evidence fails closed. This validation does not
require a cleaned-up worker checkout to remain present.

Benchmark token telemetry is a separate evidence lane. Each benchmark arm writes one
closed `compass-builder.benchmark-attempt-usage.v1` record that binds the canonical
`benchmark-receipt.v1` digest, every ordered `(runId, storyId, attempt)` launch, and the
matching finalized public `worker-usage` event. The benchmark runner does not interpret
private worker stdout. Custom run executors that publish no usage remain compatible; the
attempt record is explicitly `incomplete`, its token summary is null, and the existing v1
receipt and aggregate are unchanged.

Successful-story denominators come only from an exact succeeded `worker-completion` and
matching `worker-branch-import` identity and head. Failed-attempt overhead includes every
observed attempt that did not reach that verified-and-integrated state, including a worker
that exited successfully but was never imported. Retry overhead includes every observed
attempt numbered above one. Comparison tokens are exactly input plus output; cached input
and reasoning output remain subsets and are never counted again.

The closed `compass-builder.benchmark-token-report.v1` binds the aggregate, every attempt
usage digest, and, when available, the unchanged v1 comparison output and its digest. It
reports measured per-arm and matched-pair token totals, component ratios, per-attempted and
per-successful-story values, retry/failed overhead, deltas, ratios, median elapsed time,
first-pass counts, terminal outcomes, conflicts, retries, and interventions. Warm-up usage
is retained in attempt provenance but excluded from measured arm and pair calculations,
matching v1. If v1 comparison construction is unavailable, receipt-bound time and quality
remain present and `v1-comparison-unavailable` is recorded; missing or invalid measured
usage makes the token verdict incomplete. Only v1 owns the existing speed, quality, and
graduation decision. Token telemetry adds no pricing, cost estimate, budget, routing rule,
token-overhead threshold, or execution authority.

## Outcome-gate authority and evidence

The v2 ledger maps acceptance outcomes to either an exact approved command with bounded
referenced inputs or an independent manual-review artifact. Every gate has a stable ID,
story or root scope, required status, verification type, decisive expectation, and
non-success handoff reason. Required non-met, denied, blocked, abandoned, unavailable, or
pending outcomes block advancement; optional non-met outcomes remain truthful evidence.

The first independent worker verification and all required story gates run in the
registered isolated clone at its verified `headSha` before branch import. The destination
ref remains absent when verification or a story gate fails. After each ordered merge,
existing controller checks run first and root gates run at the exact merge SHA before
`integration-verified` or `lastVerifiedIntegrationSha` advances. Gate failures retain the
merged SHA and last verified SHA and create the phase-specific durable blocker.

Gate receipts bind the run, full gate digest, scope/story, canonical workspace, target
SHA, provider decision, execution identity or review artifact, outcome, sequence, and
prior receipt digest. The provider seals every immutable receipt field, authenticates any
receipt before publication or adoption, and retains a monotonic run-scoped
count/terminal-digest checkpoint outside repository-controlled data. The provider must
explicitly initialize a never-before-seen run at the genesis checkpoint; a missing
checkpoint never re-anchors an existing or previously initialized history.

Before an approved command launches, the provider atomically reserves its operator and
command approval IDs under a scope-stable execution key and a unique attempt key. It
retains per-attempt history and marks that attempt evidenced only after the authenticated
receipt is published and checkpointed. An unresolved attempt blocks same-scope replay;
an authenticated non-met attempt permits only a separately approved fresh attempt.
Persisted command audits are closed detached records validated using their recorded
platform, while live execution still requires exact current-host identity and paths.
A structurally valid but unauthenticated chain or tail fails before a new decision or
checkpoint advance. Only a fully authenticated crash-recovery tail may advance the
checkpoint, and only exact durable `met` evidence may be adopted. The controller refolds
exact required phase/target coverage before integration advancement and final completion.

## Per-worker effort policy

- Plugin Compass supplies the advisory handoff decision; Compass Builder constructs the
  verified launch record and starts the top-level Codex worker.
- Preserve the user-selected model or inherit it for the first attempt.
- Choose the lowest supported effort justified by that worker's complexity, ambiguity,
  risk, and validation strength.
- Define acceptance checks before dispatch.
- Diagnose failures before changing effort.
- Permit at most one higher-effort retry on the exact same model for a demonstrated
  reasoning failure.
- Permission, tool, missing-input, merge, and validation failures do not justify more
  reasoning effort.
- Do not use `claude-code-skills:llm-cost-optimizer` unless the user explicitly changes
  the objective to cost.

## Ownership and safety invariants

- Plugin Compass never dispatches, writes application code, or owns scheduler state.
- Compass Builder never imports or executes discovered third-party plugin code.
- Worker write ownership is limited to one remote-free clone and declared scopes; workers
  do not own Git mutations.
- Parallel workers never edit shared run state.
- Workers run with nested multi-agent capability disabled and are prohibited from
  launching child workers.
- The controller merges successful branches one at a time and runs integrated checks
  after each branch and wave.
- Any unexpected conflict, out-of-scope change, stale integration HEAD, failed integrated
  check, dependency
  cycles, and unknown dependencies stop the run.
- Failed or blocked checkouts remain available for inspection; automatic cleanup applies
  only to verified merged work.
- No worker may launch another worker.
- Changed paths come from the complete Git `base..head` object range. Verification is
  boundary-aware, case-folded on Windows, checks both sides of renames, rejects traversal,
  merge commits, symlinks, and submodules in the MVP, and requires exactly one
  controller-owned story commit.
- Cleanup uses only the controller's durable ledger and confirms canonical external-root
  containment, a real in-checkout Git directory, no remotes or alternate object storage,
  expected branch/head, clean state, and verified merge. It rejects the repository root,
  primary checkout, reparse points/symlinks, and any path outside that root.
- Controller-run checks must leave the integration checkout clean, including untracked
  files. Any validation-created mutation blocks verification and retains all evidence.
- V2 outcome-gate capabilities are single-use and provider-bound. No persisted receipt,
  ledger, raw approval mapping, or controller state can be replayed as executable
  authority.

## Validation and benchmark contract

Every behavior change needs focused unit coverage plus the repository-wide suite. The
MVP requires fixtures for dependency cycles, unknown dependencies, scope overlap,
Windows paths with spaces, missing validation, explicit parallel hard-gate failure,
sequential fallback, and deterministic output.

Sequential and parallel benchmark comparisons are paired trials whose arms differ only
by scheduling mode. Each arm uses a fresh disposable repository created from the same
fixture digest and starting SHA, ordered story-set digest, exact model, effort-policy
version, per-story initial effort vector, per-story handoff digests, normalized non-mode
plan-input digest, acceptance-check digest, controller/prompt version, Codex version,
Python/Git versions, OS, CPU count, and concurrency ceiling.

The workload manifest and aggregate manifest are versioned contracts. Before any arm,
the runner records the normalized ordered workload set, expected pair count, two arms per
pair, fixture/base/spec digests, and planned attempt IDs. The aggregate accounts for every
planned warm-up and measured attempt with a receipt digest and terminal status; missing,
duplicate, reordered, or extra workloads, pairs, arms, or receipts make the comparison
incomparable rather than silently reducing the sample.

The initial protocol uses at least five valid paired trials per representative workload,
alternates arm order between pairs, performs one unmeasured warm-up per arm, applies the
same per-run timeout, and excludes no completed trial. A failed, timed-out, non-green, or
incomparable trial remains in the report and blocks graduation. Timing begins immediately
before the first worker launch and ends only after the integration HEAD passes every
required controller-run check with a clean tree.

The comparator validates all immutable controls before calculation. It defines first-pass
acceptance as a run reaching green without retry, repair dispatch, manual edit, or check
rerun caused by failure. Human intervention is any user decision or manual code/Git
action after launch. It reports detected, automatically resolved, manually resolved, and
unresolved conflicts; retries; scope violations; and timeouts separately.

Live benchmark intervals are unattended after launch: worker stdin is closed after the
bounded prompt, and the controller has no interactive repair/input surface. The
controller owns a sequence-numbered, hash-chained, append-only event ledger covering every
launch, completion, retry, repair request, input request, validation rerun, timeout, ref/
status observation, and detected external Git mutation. Each aggregate binds the final
ledger hash and expected sequence range. Missing events, gaps, hash mismatch, an input or
manual-repair event, or an unaccounted ref/status change makes intervention evidence
incomplete and blocks graduation.

For each workload, the speed improvement is
`(median_sequential_ms - median_parallel_ms) / median_sequential_ms * 100`, calculated
from integer milliseconds and rounded to two decimal places using round-half-up only for
display; the unrounded value decides the threshold. A successful graduation from the
two-builder calibration limit requires:

- at least 20% lower median wall-clock time to a green integrated commit on eligible
  workloads;
- equal or better first-pass acceptance rate using the same number of valid paired trials;
- no increase in human interventions;
- zero manual or unresolved merge conflicts, write-scope violations, stale-head events,
  and timeouts.

Benchmark output reports each metric separately. It must not collapse safety and speed
into one aggregate score.

The executable benchmark additionally publishes immutable canonical
`attempt-usage-NNN.json`, `attempt-usage.json`, and `token-report.json` artifacts inside its
new output directory. It rejects a private controller event at this boundary and never
replaces an existing output target.

## Non-goals for the MVP

- A daemon, background service, custom model router, or distributed queue.
- Orchestration inside the `plugin-compass` plugin.
- Automatic plugin installation, enablement, or marketplace mutation during a run.
- Unbounded builders or performance claims without comparable measurements.
- Persistent cross-project memory, session mining, a graphical UI, browser automation,
  or paid/external services.
- Automatic conflict resolution for overlapping logic.

## Stop conditions

- **Done:** the selected run finishes with all worker, scope, merge, and integrated
  verification evidence passing.
- **Needs verification:** code or a merge exists, but required evidence is unavailable.
- **Blocked:** a dependency, permission, host capability, checkout, or validation gate
  prevents safe execution.
- **Scope exceeded:** continuation would move execution into Plugin Compass, add a daemon
  or external service, weaken acceptance, or bypass repository and authorization rules.
