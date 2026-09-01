# Compass Builder MVP implementation plan

## Goal

Adapt the smallest useful parts of Codex Loop into a separate, Windows-compatible
`compass-builder` Codex plugin that selects safe sequential or parallel execution,
applies Plugin Compass effort proposals per worker, enforces worktree/write-scope
boundaries, and produces measurable verification receipts.

## Architecture

Plugin Compass remains the passive policy and handoff-proposal owner. Compass Builder is
a separate skill plus a Python 3.11+ standard-library controller. It binds each parallel
worker to an isolated worktree through a top-level `codex exec -C` process, while Codex
inside that process owns implementation. The controller owns worktree/branch creation,
leases, durable run state, receipt validation, serial integration, cleanup, and final
acceptance. In-session subagents remain read-only reviewers until their live schema
provides enforceable working-directory binding.

## Tech stack

- Codex plugin and skill packaging
- Python 3.11+ standard library
- JSON Schema fixtures and deterministic JSON
- Git branches and worktrees
- Top-level `codex exec` subprocesses with argument arrays and structured output
- `unittest`
- Plugin Creator, Skill Creator, and HOL plugin-scanner validation

## Baseline and authority references

- `docs/PRODUCT_CONTRACT.md`
- `docs/TECHNICAL_DESIGN.md`
- `docs/COMPASS_BUILDER_CONTRACT.md`
- `SECURITY.md`
- `docs/aegis/baseline/2026-09-01-compass-builder-baseline.md`
- Upstream `aronprins/codex-loop` commit
  `823c4c75dede036278ac6de71b138a3d2a799a64`

## Compatibility boundary

- Windows paths, including spaces; PowerShell-friendly examples.
- Git repositories with an explicit resolvable base ref and worktree support.
- Codex CLI with locally verified `exec -C`, exact model, reasoning-effort config,
  `--disable multi_agent`, `--output-schema`, and JSON event support.
- Plugin Compass `plugin-compass.agent-task.v1` and `plugin-compass.handoff.v1`.
- No embedded credentials, external service, daemon, or execution inside Plugin Compass.
  Authorized worker subprocesses are bounded to registered worktrees.

## TDD route

- Mode: off
- Decision: skipped
- Strict authority: not applicable
- Test posture: minimum implementation followed by focused and full post-change
  regression checks
- Reason: the user requested planning and execution, not strict RED/GREEN sequencing.
- Verification: every task below defines exact focused and repository-wide checks.

## Goal frame

- Requested outcome: build a faster, safe builder workflow by adapting Codex Loop.
- Success evidence: contract checks, package validation, deterministic unit tests,
  isolated two-builder integration tests, and comparable sequential/parallel receipts.
- Stop condition: stop on an authority conflict, unsafe shared writes, unresolved merge,
  failed integrated verification, missing native capability, or scope expansion.
- Non-goals: no executor inside Plugin Compass, daemon, UI, persistent memory, plugin
  management, unbounded concurrency, or cost-first routing.
- Risk hints: Git lifecycle mutation, concurrent writes, stale task state, misleading
  speed claims, and duplicated policy ownership.

## Requirement ready check

- Requirement sources: user-approved adaptation direction and
  `docs/COMPASS_BUILDER_CONTRACT.md`.
- Goals and scope: explicit modes, passive auto policy, bounded concurrency, worktree
  isolation, effort selection, serial integration, recovery, and benchmark criteria.
- User scenario: an approved PRD contains multiple implementation stories and Codex must
  choose the fastest safe verified execution path.
- Acceptance criteria: contract and benchmark sections in
  `docs/COMPASS_BUILDER_CONTRACT.md`.
- Open blocker questions: none for the two-builder MVP.
- Decision: ready.

## Change necessity

- User-visible need: Plugin Compass currently advises but cannot execute a sequential or
  parallel builder workflow.
- No-change option: native in-session dispatch lacks worktree binding, while ad-hoc
  top-level Codex processes lack durable mode, lease, scope, receipt, and benchmark
  contracts.
- Why code is necessary: dependency planning, scope overlap detection, receipt checks,
  and deterministic comparisons require repeatable logic.
- Minimum boundary: one separate plugin, one focused skill, and one small standard-library
  helper package; Plugin Compass receives no orchestration code.
- Decision: code-change.

## Existence and first-principles review

- Proposed new surface: `compass-builder` companion plugin.
- Reuse candidates: Codex Loop, top-level `codex exec -C`, Git worktrees, Plugin Compass
  handoff, and the existing repo-local marketplace.
- Why reuse alone is insufficient: upstream Codex Loop trusts prose for independence,
  defaults parallelism to four, lacks deterministic write-scope enforcement, and does
  not apply per-worker Plugin Compass effort proposals or emit benchmark receipts.
- Creation proof: the companion owns only the missing orchestration contract while
  reusing every available native execution surface.
- Entropy and retirement: pin and attribute upstream; retire helper behavior when native
  Codex exposes equivalent deterministic planning and receipts.
- Decision: add with proof.

First principle: minimize total time to a verified integrated result.

Non-negotiables: Plugin Compass stays read-only; workers are isolated; write ownership is
declared and checked; merges are serial; acceptance cannot be weakened.

Assumptions dropped: parallel is always faster, equal priority proves independence,
higher effort guarantees correctness, skill prose alone enforces paths, and more workers
are always better.

Smallest sufficient path: adapt the skill and prompt contracts and add one local
controller that uses existing `git` and `codex exec -C` surfaces; do not add a daemon,
provider router, or executor inside Plugin Compass.

Escalation signal: evidence that native Codex cannot reliably target the assigned
worktree or report model/effort/commit data requires a design revision before parallel
implementation continues.

## Architecture integrity lens

- Invariant: one canonical owner exists for policy, planning, execution, shared state,
  and final acceptance.
- Canonical owners: Plugin Compass owns capability/effort advice; Compass Builder owns
  run planning, registered Git lifecycle, worker-process launch, state, and receipt
  validation; Codex workers own story implementation; repository tests own behavioral
  evidence.
- Responsibility overlap: copying Plugin Compass effort logic or adding dispatch calls
  to Plugin Compass is prohibited.
- Higher-level simplification: consume Plugin Compass's existing agent-task/handoff
  contracts instead of implementing another model router.
- Retirement/falsifier: remove helper rules replaced by a verified native Codex contract;
  revise the design if deterministic scope enforcement cannot be achieved.
- Verdict: proceed with the separate companion boundary.

## Detected repository and validation mapping

- Architecture: `docs/PRODUCT_CONTRACT.md`, `docs/TECHNICAL_DESIGN.md`, and
  `docs/COMPASS_BUILDER_CONTRACT.md`.
- Contracts: JSON Schemas plus fixture-backed `unittest` cases.
- Validation: `python -m unittest discover -s tests -v`, Plugin Creator, Skill Creator,
  `git diff --check`, and HOL plugin-scanner.
- CI: absent in the current baseline; the MVP adds a Windows-capable GitHub Actions job.
- Historical debt: none accepted for the new plugin; new contract violations block.

## Files and ownership

| Owner | Planned paths |
| --- | --- |
| Marketplace/package | `.agents/plugins/marketplace.json`, `plugins/compass-builder/.codex-plugin/plugin.json` |
| Skill orchestration | `plugins/compass-builder/skills/compass-builder/` |
| Controller/runtime | `plugins/compass-builder/compass_builder/`, `plugins/compass-builder/scripts/compass_builder.py` |
| Contracts | `plugins/compass-builder/schemas/`, `plugins/compass-builder/examples/` |
| Tests/fixtures | `tests/test_builder_*.py`, `tests/fixtures/compass_builder/` |
| Validation/CI | `scripts/check_repo_harness.py`, `docs/VALIDATION.md`, `.github/workflows/validate.yml` |
| Attribution/docs | `plugins/compass-builder/LICENSE`, `plugins/compass-builder/THIRD_PARTY_NOTICES.md`, root README |
| Controller isolation | `.gitignore` for the repository-local `.compass-builder/` run root |

The existing large `plugin_compass/models.py` and `plugin_compass/decision.py` files are
not extended. New responsibilities go into the new package, keeping projected source
complexity within budget.

## Validation matrix

| Change type | Minimum check | Escalation |
| --- | --- | --- |
| Contract/schema | Targeted schema/model tests, then full suite | Block on either failure |
| Planner/mode rules | Targeted planner tests, determinism repeat, then full suite | Block on any mismatch |
| Worker receipt/scope | Targeted verifier and temporary-repo tests, then full suite | Block on forged/stale evidence |
| Skill/prompt | Skill Creator, independent forward test, then full suite | Block on owner/invocation drift |
| Package/marketplace | Plugin Creator and full suite | Installed-copy smoke test after separate authorization |
| Git/worktree behavior | Isolated temporary-repo integration module and full suite | Controlled top-level-Codex spike |
| Release candidate | Full suite, integration module, comparator gate, and HOL scan | Block on any safety/quality failure |

Skipped checks must record the unavailable command and reason; a degraded path is never
reported as full validation.

## Implementation tasks

### Task 1: Scaffold the separate plugin and preserve upstream attribution

Files:

- Create `plugins/compass-builder/.codex-plugin/plugin.json`.
- Create `plugins/compass-builder/skills/compass-builder/` and required resources.
- Create `plugins/compass-builder/LICENSE` and
  `plugins/compass-builder/THIRD_PARTY_NOTICES.md`.
- Modify `.agents/plugins/marketplace.json` by appending `compass-builder`.
- Modify `.gitignore` to ignore the dedicated `.compass-builder/` controller root.

Steps:

1. Run the Plugin Creator scaffold against this repo-local marketplace:

   ```powershell
   python "C:\Users\jiahu\.codex\skills\.system\plugin-creator\scripts\create_basic_plugin.py" compass-builder --path "C:\Users\jiahu\Desktop\Plugin Compass\plugins" --marketplace-path "C:\Users\jiahu\Desktop\Plugin Compass\.agents\plugins\marketplace.json" --with-skills --with-scripts --with-assets --with-marketplace
   ```

2. Set strict version `0.1.0`, the `Productivity` category, and an interface that states
   native Codex scheduling and write capability. Do not add hooks, MCP, apps, or
   unsupported manifest fields.
3. Preserve Aron Prins's MIT notice and record the exact upstream commit and adapted
   files in `THIRD_PARTY_NOTICES.md`.
4. Add `/.compass-builder/` to `.gitignore`; do not use global excludes or mutate the
   user's Git configuration.
5. Run:

   ```powershell
   python "C:\Users\jiahu\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py" "C:\Users\jiahu\Desktop\Plugin Compass\plugins\compass-builder"
   git -c "safe.directory=C:/Users/jiahu/Desktop/Plugin Compass" diff --check
   python -m unittest discover -s tests -v
   ```

Expected result: plugin validation exits `0`; the marketplace retains Plugin Compass and
appends Compass Builder.

### Task 2: Define all versioned controller, state, receipt, and benchmark contracts

Files:

- Create `plugins/compass-builder/schemas/run-spec.schema.json`.
- Create `plugins/compass-builder/schemas/wave-plan.schema.json`.
- Create `plugins/compass-builder/schemas/run-state.schema.json`.
- Create `plugins/compass-builder/schemas/host-capabilities.schema.json`.
- Create `plugins/compass-builder/schemas/worker-receipt.schema.json`.
- Create `plugins/compass-builder/schemas/benchmark-receipt.schema.json`.
- Create `plugins/compass-builder/schemas/benchmark-workloads.schema.json`.
- Create `plugins/compass-builder/schemas/benchmark-aggregate.schema.json`.
- Create `plugins/compass-builder/compass_builder/models.py`.
- Create fixtures under `tests/fixtures/compass_builder/` and
  `tests/test_builder_models.py`.

Steps:

1. Require collision-resistant run ID, explicit base ref and resolved base SHA,
   integration branch/expected SHA, exact model, effort-policy version, host/user ceilings,
   validation commands, ordered stories, dependencies, shared-state declaration, and
   repository-relative write scopes. Each story also declares `complexity`, `ambiguity`,
   `risk`, and `validationStrength`; the wave plan binds the resulting Plugin Compass
   effort proposal and handoff digest.
2. Encode the run transitions `planned`, `dispatching`, `wave-workers-complete`,
   `wave-merging`, `wave-integrated-unverified`, `wave-verified`, next-wave
   `dispatching`, `completed`, and `blocked`. Require `currentWaveIndex` plus an ordered
   per-branch ledger with worker/verification/integration states, pre-merge expected SHA,
   merge SHA, controller-check digest, and post-check expected SHA; reject every
   undeclared transition or broken SHA chain.
3. Bind benchmark receipts to fixture, start SHA, ordered stories, acceptance checks,
   exact model, effort policy, per-story initial effort vector and handoff digests,
   normalized non-mode plan-input digest, controller/prompt versions, toolchain,
   environment, arm, pair/trial number, timing, retries, interventions, conflicts, scope
   violations, event-ledger terminal hash/range, and final green SHA using stable digests.
4. Define versioned workload and aggregate manifests. The workload manifest fixes the
   ordered workload set, fixture/base/spec digests, pair count, two arms per pair, warm-up
   IDs, and measured attempt IDs before execution. The aggregate must account for every
   planned ID exactly once with receipt digest and terminal status; reject omissions,
   duplicates, additions, or reordering.
5. Reject absolute/traversal scopes, duplicate or unknown IDs, cycles, unsupported modes,
   zero/negative durations, missing metrics, and stale/mismatched immutable identifiers.
6. Run:

   ```powershell
   python -m unittest tests.test_builder_models -v
   python -m unittest discover -s tests -v
   ```

Expected result: valid fixtures round-trip to byte-identical JSON; each malformed or
incomparable fixture fails with a field, reason, and corrective direction.

### Task 3: Implement read-only host doctor and deterministic mode/wave planning

Files:

- Create `plugins/compass-builder/compass_builder/doctor.py`.
- Create `plugins/compass-builder/compass_builder/planner.py`.
- Create `plugins/compass-builder/compass_builder/handoff.py`.
- Create `plugins/compass-builder/compass_builder/cli.py` and `__main__.py`.
- Create `plugins/compass-builder/scripts/compass_builder.py`.
- Create `tests/test_builder_doctor.py`, `tests/test_builder_planner.py`, and
  `tests/test_builder_handoff.py`.

Steps:

1. Derive CLI/Git evidence from captured `codex --version`, `codex exec --help`, `codex
   features list`, `git --version`, and `git worktree list --porcelain`; require `exec
   -C`, exact model selection, structured output, and a stable `multi_agent` feature that
   can be disabled. Do not infer `model_reasoning_effort` support from generic `-c` help.
   Require a current native-capability snapshot supplied by the invoking Codex control
   plane that names the exact selected model and supported efforts with provenance and
   version; fail closed when it is absent, stale, or inconsistent. Hash all raw evidence
   in the plan.
2. Resolve the explicit base ref once to an immutable SHA. Do not require branch names
   `main` or `master`.
3. Require repository-local `/.compass-builder/` to be ignored by a tracked `.gitignore`,
   absent from the index, and confirmed for both state and worktree probe paths by
   `git check-ignore`. Reject tracked content, global-exclude-only matches, reparse points,
   or roots outside the controller checkout.
4. Normalize scopes by path segment and Windows case-folding; reject ancestor/descendant
   overlap, separator/case aliases, traversal after normalization, shared-state writes,
   and missing decisive validation.
5. For every story, create Plugin Compass `plugin-compass.agent-task.v1` from its declared
   classifications, resolve Plugin Compass through authoritative Codex inventory or an
   explicitly supplied root, and invoke only its `handoff` command. Bind the successful
   `recommended_effort`, policy version, target task digest, and handoff digest into the
   wave plan before calculating mode. Gated or mismatched handoffs make parallel
   structurally unavailable.
6. Implement `coordination-policy.v1`: effort units `low=1`, `medium=2`, `high=3`,
   `xhigh-or-above=4`; parallel benefit is `sum - max` and must be at least `2` after all
   safety gates. Concurrency is `min(2, host ceiling, user ceiling, eligible ready set)`.
7. Add negative tests for every sequential/parallel gate: fewer than two ready, dirty
   tree, unavailable worktrees or binding, shared writes, missing validation, overlap,
   prior-wave failure, host ceiling below two, unknown/cyclic dependencies, low
   coordination benefit, unauthorized fallback, missing/stale native capability evidence,
   unsupported effort, tracked/unignored controller roots, and failed/gated handoff.
8. Wire and run:

   ```powershell
   python -m unittest tests.test_builder_doctor tests.test_builder_planner tests.test_builder_handoff -v
   python -m unittest discover -s tests -v
   ```

Expected result: identical evidence/spec inputs produce identical plans and reasons;
auto selects sequential when value or safety is insufficient, while explicit parallel
fails closed on every hard gate.

### Task 4: Apply Plugin Compass effort advice to worktree-bound Codex launches

Files:

- Create `plugins/compass-builder/compass_builder/launcher.py`.
- Create `plugins/compass-builder/compass_builder/git_environment.py`.
- Create `plugins/compass-builder/schemas/launch-record.schema.json`.
- Create `plugins/compass-builder/schemas/worker-output.schema.json`.
- Create `tests/test_builder_launcher.py`.

Steps:

1. Consume only the planner-bound Plugin Compass `recommended_effort` and handoff digest;
   do not execute or translate its target-specific `collaboration.spawn_agent` arguments.
2. Require an exact model for top-level workers. Preserve that model and map the approved
   effort to the locally verified Codex config key without adding another effort policy.
3. Build a no-shell argument array equivalent to:

   ```text
   codex exec -C WORKTREE -m MODEL -c model_reasoning_effort="EFFORT" --disable multi_agent --ephemeral -s workspace-write --approve-for-me --json --output-schema WORKER_SCHEMA -
   ```

   Send the bounded worker prompt on stdin. Never use bypass-sandbox, bypass-approval,
   hook-trust bypass, extra writable directories, or shell interpolation.
4. Launch with a controller-owned sanitized Git environment: disable system configuration
   and system attributes, point global config and attributes to registered empty files,
   use an empty template/hooks directory, disable commit/tag signing, and preserve fixed
   repository-local identity and line-ending settings. Do not repurpose `HOME` or rely on
   the caller's global Git state.
5. Treat startup/model/config/tool/permission failure as blocked without raising effort.
   Allow one same-model higher-effort retry only after controller evidence identifies a
   reasoning failure.
6. Run:

   ```powershell
   python -m unittest tests.test_builder_handoff tests.test_builder_launcher -v
   python -m unittest discover -s tests -v
   ```

Expected result: launch records contain exact model/effort/worktree/evidence; absent host
support, inconsistent native-capability evidence, or a gated Plugin Compass decision
produces no process arguments.

### Task 5: Adapt Codex Loop and implement durable controller state, leases, and resume

Files:

- Create `plugins/compass-builder/skills/compass-builder/SKILL.md` and
  `agents/openai.yaml`.
- Create `references/preflight.md`, `references/sequential.md`,
  `references/parallel.md`, and `references/recovery.md` plus focused worker prompts.
- Create `plugins/compass-builder/compass_builder/state.py`.
- Create `plugins/compass-builder/compass_builder/lease.py`.
- Create `plugins/compass-builder/examples/run-spec.json`.
- Create `tests/test_builder_state.py`, `tests/test_builder_lease.py`, and
  `tests/test_builder_skill_contract.py`.

Steps:

1. Adapt upstream dependency waves, worktree isolation, merge barriers, and recovery;
   remove priority-only independence and every parallel shared-state write.
2. Store controller-only state at ignored
   `.compass-builder/runs/<run-id>/state.json`. Write through a same-directory temporary
   file, flush/fsync, and atomically replace. Validate repository identity, SHAs, registered
   paths, and transition before resume.
3. Use collision-resistant run IDs, branches `cb/<run-id>/<story-id>`, dedicated
   `.compass-builder/worktrees/<run-id>/<story-id>` paths, and an exclusive
   integration-branch lease keyed by Git common directory plus branch. Record expected
   HEAD and perform compare-and-swap before merge and state transitions.
4. Put the no-nested-worker rule in every worker prompt and enforce it through the
   launcher feature flag. The controller alone owns run state and integration Git.
5. Persist `currentWaveIndex` and each wave's ordered branch ledger. Resume validates the
   complete pre-merge/merge/post-check SHA chain, continues at the first branch not marked
   `integration-verified`, never re-merges or skips an entry, and moves `wave-verified`
   back to `dispatching` only when another planned wave exists.
6. Wire `run` and `resume` through `cli.py`; a dry-run fixture must cover every run and
   branch state, next-wave loop, and partial-wave recovery before a live worker is allowed.
7. Run:

   ```powershell
   python -m unittest tests.test_builder_state tests.test_builder_lease tests.test_builder_skill_contract -v
   python "C:\Users\jiahu\.codex\skills\.system\skill-creator\scripts\quick_validate.py" "C:\Users\jiahu\Desktop\Plugin Compass\plugins\compass-builder\skills\compass-builder"
   python -m unittest discover -s tests -v
   ```

Expected result: invalid transitions, concurrent controller leases, stale heads, nested
worker capability, and corrupted/inconsistent resume records all fail closed.

### Task 6: Enforce Git-derived scope, serial integration, and safe cleanup

Files:

- Create `plugins/compass-builder/compass_builder/verifier.py`.
- Create `plugins/compass-builder/compass_builder/integrator.py`.
- Create `plugins/compass-builder/compass_builder/cleanup.py`.
- Create `tests/helpers/git_repo_factory.py`.
- Create `tests/test_builder_verifier.py`, `tests/test_builder_integrator.py`, and
  `tests/test_builder_cleanup.py`.

Steps:

1. Create every temporary repository from seed files with local-only Git configuration:
   fixed `user.name`, `user.email`, initial branch, `core.autocrlf=false`,
   `core.filemode=false`, deterministic commit dates, and no inherited test commands.
   Every Git process uses `GIT_CONFIG_NOSYSTEM=1`, `GIT_ATTR_NOSYSTEM=1`, a registered
   empty `GIT_CONFIG_GLOBAL`, empty template/hooks directory, controlled attributes file,
   signing disabled, and fixed author/committer identity and dates; never replace `HOME`.
2. Derive the complete changed set from Git objects in `base..head`, require exactly one
   non-merge worker commit, compare both sides of renames/deletes, and reject symlink or
   submodule modes for the MVP. Independently rerun every required check at the receipt
   SHA and reconcile receipt model/effort/SHA/files with Git and launch records.
3. Add forged/stale/wrong-SHA/dirty/unrelated-ancestry/missing-check/shared-state cases;
   include case/separator aliases, ancestor scopes, renames, deletes, symlinks, submodules,
   and multi-commit/merge-commit bypass attempts. Add a hostile ambient-config regression
   with global signing, hooks, attributes, aliases, and line-ending settings and prove the
   repository factory/controller environment neutralizes them.
4. Acquire the lease, require integration HEAD equals expected SHA, merge one verified
   branch with `--no-ff`, stop on any conflict, and atomically update the ordered branch
   ledger. Retain every worktree on failure.
5. After each branch merge, record `wave-integrated-unverified`, run every required check,
   and require a clean tracked and untracked integration checkout before recording that
   branch `integration-verified`. Validation-created mutations block, retain evidence,
   and do not advance the expected SHA. After the ordered branch ledger and wave checks
   pass, record `wave-verified`.
6. Cleanup only registered verified-merged worktrees after canonical containment,
   `git worktree list --porcelain`, expected branch/head, clean-state, non-reparse-point,
   and primary-checkout exclusions pass. Wire `verify-worker` and `cleanup` through CLI.
7. Run:

   ```powershell
   python -m unittest tests.test_builder_verifier tests.test_builder_integrator tests.test_builder_cleanup -v
   python -m unittest discover -s tests -v
   ```

Expected result: concurrent controllers, stale HEAD, forged evidence, every scope bypass,
unsafe cleanup target, conflict, and partial-wave failure stop without deleting evidence.

### Task 7: Add a reproducible repository harness and Windows CI

Files:

- Create `scripts/check_repo_harness.py`.
- Create `docs/VALIDATION.md`.
- Create `.github/workflows/validate.yml`.
- Create `tests/test_repo_harness.py`.

Steps:

1. Implement repo-local `docs`, `contracts`, `unit`, `integration`, and `audit` profiles
   with human output plus stable optional JSON. Every behavior profile runs focused checks
   followed by the full suite.
2. Record every skip with its unavailable command and reason; never label a degraded
   profile full validation.
3. Make Windows CI depend only on repository files and standard Python/Git: run schema
   checks, `python -m unittest discover -s tests -v`, the explicit integration module,
   harness self-tests, and `git diff --check`. Do not reference workstation-only Plugin
   Creator, Skill Creator, or HOL paths in CI.
4. Keep Plugin Creator, Skill Creator, installed-copy, and HOL checks as documented local
   release gates with their exact current paths.
5. Run:

   ```powershell
   python scripts/check_repo_harness.py --profile unit
   python scripts/check_repo_harness.py --profile audit
   python -m unittest discover -s tests -v
   ```

Expected result: commands exit `0`; each failure names the command, path/case, reason, and
corrective direction; CI has no hidden workstation dependency.

### Task 8: Implement the comparator and executable paired-benchmark runner

Files:

- Create `plugins/compass-builder/compass_builder/benchmark.py`.
- Create `plugins/compass-builder/compass_builder/benchmark_runner.py`.
- Create `tests/integration/__init__.py` and
  `tests/integration/test_builder_worktrees.py`.
- Create `tests/test_builder_compare.py`.
- Create `tests/test_builder_benchmark_runner.py`.
- Create seed fixtures and receipts under
  `tests/fixtures/compass_builder/benchmarks/`.
- Create `docs/COMPASS_BUILDER_BENCHMARK.md`.

Steps:

1. Implement `compare` as a fail-closed validator over the benchmark contract. Reject
   mismatched fixture/start/story/check/model/effort-policy/controller/prompt/toolchain/
   environment digests, per-story initial-effort vectors, handoff digests, normalized
   non-mode plan-input digests, failed/non-green arms, zero durations, missing metrics,
   incomplete workload/attempt accounting, and unequal trial sets. Test exact 20%
   boundaries using unrounded Decimal arithmetic and round-half-up display.
2. Test safety precedence: any timeout, stale head, manual or unresolved conflict, scope
   violation, increased intervention, or lower first-pass acceptance fails graduation
   even when parallel is faster.
3. Implement the public `benchmark` command. It validates `--pairs >= 5`, creates a fresh
   disposable repository for every arm from the recorded fixture SHA, performs one
   unmeasured warm-up per arm, alternates sequential-first and parallel-first by pair,
   invokes the public `run` path for both modes with the same model/policy/story/check
   digests and timeout, and atomically writes every attempt receipt plus an aggregate
   manifest. It excludes no completed trial and preserves failures/timeouts.
4. Make each live interval unattended after the initial bounded prompt and close worker
   stdin. Emit a sequence-numbered, hash-chained append-only controller event ledger for
   launch/completion, retry, repair/input request, check/rerun, timeout, ref/status
   observation, and external Git mutation. Bind the terminal ledger hash/range to the
   aggregate; comparator rejects gaps, mismatch, missing terminal coverage, manual-input/
   repair events, or unaccounted Git changes.
5. Define timing as first worker launch through clean green integrated HEAD. Record every
   retry, repair, intervention, detected/resolved/unresolved conflict, scope violation,
   timeout, and check result at attempt level.
6. Run the integration module directly and invoke the public comparator:

   ```powershell
   python -m unittest tests.test_builder_compare tests.test_builder_benchmark_runner -v
   python -m unittest tests.integration.test_builder_worktrees -v
   python plugins/compass-builder/scripts/compass_builder.py compare --sequential tests/fixtures/compass_builder/benchmarks/sequential.json --parallel tests/fixtures/compass_builder/benchmarks/parallel.json
   python -m unittest discover -s tests -v
   ```

Expected result: the fixture runner uses a fake deterministic worker transport and proves
the complete arm-order/repository/receipt route without model usage; live receipts remain
pending until separately authorized. The two-builder limit is not raised unless a later
authorized live benchmark passes every speed, quality, and safety threshold.

### Task 9: Package and security-scan; benchmark and install only after authorization

Files:

- Finalize package documentation and marketplace metadata.
- Update `docs/COMPASS_BUILDER_BENCHMARK.md` with the actual comparable verdict.

Steps:

1. Run the complete local release gate:

   ```powershell
   python "C:\Users\jiahu\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py" "C:\Users\jiahu\Desktop\Plugin Compass\plugins\compass-builder"
   python "C:\Users\jiahu\.codex\skills\.system\skill-creator\scripts\quick_validate.py" "C:\Users\jiahu\Desktop\Plugin Compass\plugins\compass-builder\skills\compass-builder"
   python -m unittest tests.integration.test_builder_worktrees -v
   python -m unittest discover -s tests -v
   python plugins/compass-builder/scripts/compass_builder.py compare --sequential tests/fixtures/compass_builder/benchmarks/sequential.json --parallel tests/fixtures/compass_builder/benchmarks/parallel.json
   C:\Users\jiahu\.local\bin\plugin-scanner.cmd lint "C:\Users\jiahu\Desktop\Plugin Compass\plugins\compass-builder"
   C:\Users\jiahu\.local\bin\plugin-scanner.cmd verify "C:\Users\jiahu\Desktop\Plugin Compass\plugins\compass-builder"
   C:\Users\jiahu\.local\bin\plugin-scanner.cmd scan "C:\Users\jiahu\Desktop\Plugin Compass\plugins\compass-builder" --format json
   ```

2. Before publishing an actual performance verdict, obtain separate authorization and
   run the model-backed route exactly through the public surface:

   ```powershell
   python plugins/compass-builder/scripts/compass_builder.py benchmark --repo <seed-repository> --workloads <workload-manifest> --pairs 5 --timeout-seconds <timeout> --output .compass-builder/benchmarks/<benchmark-id>
   python plugins/compass-builder/scripts/compass_builder.py compare --sequential .compass-builder/benchmarks/<benchmark-id>/sequential.json --parallel .compass-builder/benchmarks/<benchmark-id>/parallel.json
   ```

   Retain the aggregate manifest and all arm receipts, and update the benchmark document
   with their immutable digests. Fixture receipts may validate mechanics but may never be
   labeled an actual verdict.
3. Block release on any failed gate or unresolved high/critical exact-target finding;
   preserve lower findings and their disposition.
4. Stop and request separate user authorization before cachebuster mutation, reinstall,
   restart, or installed-copy execution. If authorized, use Plugin Creator's supported
   update flow and run `doctor`, one sequential fixture, and one worktree-bound parallel
   fixture against the installed copy.

Expected result: source/package gates and the benchmark verdict are explicit. Installation
remains pending until separately authorized; no plan text grants that authority.

## Plan pressure test

- Owner/contract/retirement: owners are separated; upstream and native replacements have
  explicit retirement paths.
- Architecture integrity: no executor enters Plugin Compass and no model router is
  duplicated.
- Verification scope: unit, temporary-repo integration, top-level worktree-bound worker,
  comparator, installed-copy, and benchmark evidence are distinct.
- Task executability: each task names files, commands, expected outcomes, and stop gates.
- Pressure result: proceed.

## Complexity budget

- Artifact class: source, test, skill, and plan artifacts.
- Target: new single-purpose files; avoid adding responsibilities to the existing 685-line
  decision owner or 516-line model owner.
- Current pressure: the existing Plugin Compass files are cohesive but already sizeable;
  the new runtime has a separate owner.
- Projected pressure: within budget when planner, verifier, doctor, handoff, and models
  remain separate.
- Planned governance: split any new maintained file approaching 800 lines or mixing Git,
  policy, and serialization responsibilities.

## Execution readiness view

- Intent lock: fastest verified completion, not maximum parallelism or minimum cost.
- Scope fence: separate companion plugin and deterministic helper only.
- Baseline lock: current `main`, product/security contracts, and pinned upstream commit.
- Approved behavior: auto/sequential/parallel planning, Plugin Compass effort proposals,
  top-level worktree-bound workers, leases/CAS, durable state/resume, serial integration,
  safe cleanup, receipts, and paired comparison.
- Owner constraints: Plugin Compass advises; the invoking Codex starts Compass Builder;
  the Compass Builder controller owns subprocess dispatch, registered Git lifecycle, and
  integration; worker Codex processes own only story edits and their one allowed commit;
  tests accept.
- Compatibility: Windows, Python 3.11+, Git worktrees, live native tool schema.
- Retirement: native verified scheduling/receipt features may replace helper behavior;
  copied upstream material retains attribution.
- Task batches: Tasks 1-3 foundation; Tasks 4-6 controlled execution; Tasks 7-9
  validation and conditional release/install.
- Review gates: contract/spec review after planning; spec-compliance then code-quality
  review for every coherent implementation task.
- Drift/rewind: stop and revise the plan when a new executor owner, shared writer, unsafe
  fallback, or unsupported native field appears.
- Evidence before completion: scoped commit, focused/full tests, validators, exact-target
  HOL result, installed-copy smoke test, and benchmark receipts.
- Advisory boundary: this plan is execution guidance, not final completion authority.

## Execution route

- Decision: independent read-only reviews may run in parallel; implementation remains one
  writer task at a time until the controller passes the worktree-binding spike.
- Evidence: current in-session dispatch lacks working-directory binding, while local
  `codex exec --help` exposes enforceable `-C` for the planned top-level worker path.
- Fallback: sequential execution if top-level Codex/worktree binding or decisive
  validation is unavailable.
- User confirmation required: no for source implementation and local tests; yes later for
  plugin reinstall/restart or a benchmark run whose model usage materially exceeds the
  ordinary implementation workflow.

## Risks and rollback

- A worker may touch undeclared generated/shared files: reject the receipt before merge.
- A second controller or external Git actor may race integration: hold the branch lease
  and stop on any compare-and-swap mismatch.
- A task classification may understate reasoning needs: diagnose failed evidence and allow
  one same-model higher-effort retry.
- Worktree cleanup may become destructive: retain blocked worktrees and remove only
  registry-owned, canonically contained, clean, verified-merged worktrees confirmed by
  Git; reject roots, primary checkouts, symlinks, and reparse points.
- Upstream changes may drift: stay pinned until a separately reviewed upgrade.
- Parallel timing may not beat sequential: retain sequential as the safe default for
  ineligible workloads and do not raise concurrency without benchmark evidence.

## Retirement

- Retire priority-only independence and any copied upstream instruction that lets workers
  write shared run state in parallel.
- Do not introduce a compatibility adapter inside Plugin Compass.
- Retire helper logic only after a native Codex surface provides equivalent deterministic
  planning, scope, receipt, and validation evidence.
