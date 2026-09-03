# Compass Builder Task 9 evidence

## Start evidence

- Repository: `C:/Users/jiahu/Desktop/Plugin Compass`
- Start HEAD: `fd708d651e92c70a57f554500348774f9ee6090d`
- Branch: `main`
- Upstream divergence: `0 0`
- Staged, unstaged, and untracked paths: none
- Active Git operations: none
- Worktrees: one primary checkout
- Task 8 implementation hosted validation: success on Windows Python 3.11, run
  `33671040874`.

## Baseline findings

- Public benchmark CLI is the implemented `--fixture`, `--sequential-plan`,
  `--parallel-plan`, `--pairs`, `--timeout-ms`, and `--output` surface. The older
  `--repo/--workloads` example in the parent plan is superseded by the product contract
  and benchmark guide.
- The package manifest says the runtime is absent and the skill UI prompt asks only for
  dry-run planning, contradicting the verified Task 8 implementation.
- `build_worker_prompt` requires scoped edits and structured output but omits the exactly-
  one-commit outcome enforced by worker verification. Fixing the canonical prompt before
  live model use avoids systematic false failure and wasted benchmark calls.
- Current native CLI evidence: `codex-cli 0.153.0-alpha.5`, exact `-C`, `-m`, `--json`,
  `--output-schema`, `--disable`, `--ephemeral`, workspace-write, and approval-routing
  surfaces; `multi_agent stable true`; Git 2.55.0; Python 3.14.6; Windows; 24 CPUs.
- HOL is not exposed as an available session skill, but the repository documents the
  exact local `plugin-scanner.cmd` CLI. Its callable status and results remain pending.

## Live root-cause evidence

- Linked-worktree runs with `--approve-for-me` produced correct worker edits but exposed
  the integration checkout to shared repository/session effects. A bounded clean-window
  workaround failed and was removed rather than hidden behind a delay.
- Both post-`exec` and global-form `workspace-write`/`never` runs returned structured
  blockers stating that the managed host permission profile was read-only. This route
  could not implement files and was rejected.
- Workers were changed to edit files only. The controller now rejects worker HEAD/index
  mutation, stages only after scope validation, creates one deterministic commit, and
  reruns required checks.
- A standalone external clone with no remote completed under the production
  `--approve-for-me` command with two intended untracked story files and zero integration
  repository dirty observations at 250 ms sampling.
- The maintained boundary now creates no-hardlink, no-tag, full-history worker clones in
  a deterministic temporary root outside the integration repository, removes every
  remote before launch, rejects alternate object storage, imports the exact branch SHA
  only after worker exit, and preserves serial verification/integration.
- The apparent four-file failure in an intermediate fixture was isolated to CRLF checkout
  drift under Windows system `core.autocrlf=true` versus the controller's fixed
  `core.autocrlf=false`. A clean-under-controller-policy preflight was added, and the
  calibration fixture was recreated under that exact policy.

## Maintained regression evidence

- `python -m unittest tests.test_builder_state tests.test_builder_verifier tests.test_builder_cleanup tests.integration.test_builder_worktrees -v`
  passed 49 tests in 526.357 seconds.
- The parallel integration regression proves two simultaneously active workers, separate
  in-checkout Git databases, no remotes, exact ref import, ordered serial merges, clean
  controller checks, and ledger-authorized Windows cleanup.
- The production parallel smoke completed in 133102 ms at final green SHA
  `da41742110d2ae3287caaa3098d6d8e2e9219abb`. All recorded retry, intervention,
  conflict, scope, stale-head, timeout, repair, manual-edit, and check-failure metrics
  were zero.
- `python -m unittest discover -s tests -v` passed all 223 tests in 804.972 seconds.

## Paired live benchmark evidence

- Evidence root: ignored local directory
  `.compass-builder/task9/benchmark-paired-r1/`.
- Controls: fixture SHA `50a54cc535199596cf4ebefac861e6212339d2e8`, fixture digest
  `sha256:78242f11ab6039ddbb20741411c69cb3d8f2beadf149e8f50188084ae72c3f73`,
  exact model `gpt-5.6-sol`, initial effort `medium` for both stories, concurrency
  ceiling 2, 300000 ms worker timeout, and identical non-mode plan digest
  `sha256:798dfefdb8300d51fd3997025317e84dc2d1506f224a2da4e8d725ded8b1f87f`.
- Both warmups and all ten measured arms completed green. The aggregate accounts for 192
  hash-chained events with terminal hash
  `sha256:9859bc42db52c80ff41a5bc7998b514d4d0bd60edb1100d532e9494c241bc22b`.
- Independent comparator result: `graduated: true`; median sequential `192476` ms;
  median parallel `118459` ms; improvement `38.46%`; first-pass 5/5 versus 5/5;
  interventions 0 versus 0; every blocking safety metric zero; no exclusion reasons.
- The result graduates only the documented two-builder calibration policy. It does not
  authorize a higher concurrency ceiling or a universal performance claim.

## Source package and security evidence

- Repository harness `audit --format json` passed all documentation, contract,
  integration, harness-self-test, diff, and complete-suite checks and reported
  `fullValidation: true`.
- Plugin Creator validation passed for the exact source plugin directory before and
  after cachebuster generation.
- Skill Creator validation passed for the bundled `compass-builder` skill.
- `git diff --check` passed with no output.
- HOL `plugin-scanner` 3.0.18 exact-target lint, verify, and JSON scan passed policy and
  package verification at grade A. The base version scored 97 with 0 critical, high,
  medium, or low findings and 5 informational optional-interface-asset notices. The
  required Plugin Creator cachebuster version scored 93 with the same five notices and
  one low scanner-compatibility finding that incorrectly rejects the valid SemVer build
  metadata suffix `+codex.20260903002035`. The Cisco skill scanner completed with 0
  findings; no MCP scanner was applicable because the package has no `.mcp.json`.

## Installed-copy evidence

- Plugin Creator generated version `0.1.0+codex.20260903002035`; `codex plugin add`
  installed it from `plugin-compass-local` at the exact cache path documented in
  `docs/VALIDATION.md`.
- All 89 installed files matched all 89 source files by relative path and SHA-256. Plugin
  Creator and Skill Creator validation passed against the installed directory, and HOL
  reproduced the source cachebuster-version grade A/policy-pass result.
- Installed `doctor` passed against the fresh user-owned fixture and native evidence.
  Before launch, the CLI also correctly rejected a relative repository path and Git
  rejected a sandbox-owned fixture; neither rejection launched a worker. The fixtures
  were recreated under the user account without any global `safe.directory` change.
- Installed sequential smoke: planner-selected `low` effort, one worker, completed in
  64156 ms at green SHA `6e512e8dc5019ce344ef314bc5e907e6d7eeccdb`; all safety and
  intervention metrics zero; independent 6-test discovery passed.
- Installed parallel smoke: planner-selected `medium` effort, two concurrent workers,
  completed in 102707 ms at green SHA
  `71624b1ccfe13aa098c02a15463223b357d2f29e`; all safety and intervention metrics zero;
  independent 12-test discovery passed.
- Completed worker clones remain preserved for inspection. No installed-copy cleanup was
  inferred from the execution authorization.
