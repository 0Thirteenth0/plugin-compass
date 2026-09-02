# Compass Builder Task 8 evidence

## Start evidence

- Repository: `C:/Users/jiahu/Desktop/Plugin Compass`
- Start HEAD: `1b24ef9fe8af55f31491cb64a6ea44de8d9da3f9`
- Branch: `main`
- Upstream divergence: `0 0`
- Staged, unstaged, and untracked paths: none
- Active Git operations: none
- Worktrees: one primary checkout
- Hosted Windows validation for start HEAD: success, including Python 3.11 full suite,
  explicit integration, harness self-tests, and diff hygiene.
- Full suite at the Task 7 closeout: 206/206 passed.

## Boundary evidence

- No live worker, benchmark, plugin mutation, installed-copy action, external service,
  or Git lifecycle mutation occurred during Task 8 setup.
- Parent TDD route remains `off / skipped`; verification is post-change and evidence-
  based.
- The user explicitly authorized the bounded addition of the missing controller executor
  owner and public-run wiring after the plan gap was surfaced.

## Implementation evidence

- `controller.py` composes existing launch, durable state, worker verification, lease-
  serialized integration, Git-environment, and bounded-process owners. It records later
  waves as running only after their registered worktrees and immutable launch records
  exist, and before process dispatch.
- Worker prompts use the existing bounded schema and are written once through the
  process owner; stdin is then closed. The process tree, input writer, and output readers
  share the same wall/output bounds.
- `benchmark.py` validates every receipt and hash-chained event ledger, exact control
  equality, equal contiguous trial sets, green status, safety metrics, first-pass rate,
  interventions, and the unrounded Decimal 20% threshold.
- `benchmark_runner.py` validates at least five pairs, alternates pair order, creates a
  distinct fresh clone at the recorded SHA for every arm, invokes the same public run
  executor, retains every terminal attempt, validates the aggregate against all receipt
  digests, and publishes the complete result directory atomically.
- No live Codex worker or benchmark was invoked. The checked-in 30% seed result is marked
  synthetic and cannot authorize a concurrency increase.

## Focused verification evidence

- `python -m unittest tests.test_builder_compare tests.test_builder_benchmark_runner -v`
  passed 9/9, including exact-boundary, misleading-display-rounding, safety precedence,
  first-pass, control/trial mismatch, ledger integrity, public-run routing, fresh clone,
  alternating order, and minimum-pair coverage.
- `python -m unittest tests.integration.test_builder_worktrees -v` passed sequential
  later-wave base binding and two-worker overlap/ordered integration in disposable Git
  repositories.
- A combined compatibility run across comparator, runner, integration, state, skill,
  and harness modules passed 42/42 before the later-wave truthfulness tightening; the
  affected state/integration tests were rerun afterward and passed 3/3.
- The exact public seed command exited 0 and reported five equal first-pass pairs,
  zero blocking metrics, 1000 ms versus 700 ms medians, and 30.00% synthetic improvement.
- New/changed Python modules compile, and `git diff --check` is clean.

## Fresh coordinator verification

- Exact-final `python -m unittest discover -s tests -v`: 217/217 passed in 423.398
  seconds.
- `python scripts/check_repo_harness.py --profile audit --format json`: exit 0 and
  `status: passed`; its 11 checks covered documentation, contracts, explicit
  integration, harness self-tests, diff hygiene, and full discovery. This audit ran
  before the final comparator warmup-coverage tightening; the exact affected focused
  suite and full discovery were rerun afterward and passed.
- Exact-final `python -m unittest tests.test_builder_compare
  tests.test_builder_benchmark_runner -v`: 9/9 passed in 10.052 seconds.
- Exact-final public fixture comparison exited 0 and produced `graduated: true` from
  five synthetic paired trials. This proves comparator mechanics only; it is not live
  performance evidence and cannot raise the two-builder ceiling.
- Exact-final direct integration and affected state coverage passed 3/3 for overlapping
  isolated workers, serial integration, and later-wave base/launch binding.
- New and changed Python modules compile; `git diff --check` is clean.
- Final repository-harness self-tests passed 10/10 after lifecycle-record closeout, and
  the final worktree inventory contains only the primary checkout.
- Final ownership/security search found no shell execution, verification bypass,
  destructive Git reset/clean, or recursive-removal pattern in the maintained Task 8
  surface.

## Complexity and governance evidence

- New production owners remain separated and below the 800-line pressure signal:
  `controller.py` 498 lines, `benchmark.py` 202 lines, and `benchmark_runner.py` 351
  lines. The pre-existing state owner is 731 lines after a wiring-only change.
- Canonical live lifecycle ownership is `controller.py`; `run --dry-run` is retained as
  an explicit non-model projection, not a second executor or fallback.
- A native Codex scheduler is the retirement trigger only when it supplies equivalent
  worktree isolation, immutable launch/receipt binding, bounded execution, verification
  barriers, serial integration, and complete attempt accounting.
- ADR action: skip. The repository has no ADR system, and introducing one for this
  decision would duplicate the durable baseline already owned by
  `docs/COMPASS_BUILDER_CONTRACT.md` and the approved MVP plan.

## Closeout and residual evidence

- Task 8 source goal is achieved. No live Codex transport or model-backed timing run was
  executed, so the actual Codex structured-output/stdin route remains a Task 9 live
  falsifier rather than a Task 8 claim.
- Live resume remains deliberately dry-run-only. Package/security/install validation,
  HOL scanning, and any concurrency-cap change remain Task 9 concerns.
- The user separately authorized Task 8 Git closeout. Implementation commit
  `97335305dbf10c7c49201b813bc42a339a409bc8` (`feat: add executable builder benchmark
  workflow`) was pushed to `origin/main`; local and remote readback matched with
  divergence `0 0`.
- GitHub Actions `Validate` run `33671040874` completed successfully in 11m46s on
  Windows Python 3.11. Contract models, 217-test discovery, focused comparator/runner
  coverage, explicit Git/worktree integration, harness self-tests, and diff hygiene all
  passed.
- The run emitted one non-blocking platform annotation: GitHub forced the pinned
  Node.js-20-based checkout/setup actions onto Node.js 24 because Node.js 20 is
  deprecated. No Task 8 validation step was degraded or skipped.
- No task branch or worktree was created. After the implementation push, the primary
  checkout was clean and contained the only registered worktree.
