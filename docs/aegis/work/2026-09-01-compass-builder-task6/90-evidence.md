# Compass Builder Task 6 evidence

## Start evidence

- Repository: `C:/Users/jiahu/Desktop/Plugin Compass`
- Start HEAD: `e6d859f4947df929a924e991b37ff19b5d3dcb78`
- Branch: `main`
- Upstream divergence: `0 0`
- Worktree: one primary checkout
- Staged/unstaged/untracked paths: none
- Active Git operations: none
- Baseline Task 5 focused suite: 23 tests passed.
- Baseline full suite: 143 tests passed.

## Slice evidence

- Implemented controller-owned worker verification from raw Git objects, exact launch and
  receipt bindings, durable current-wave state, registered worktree identity, scope and
  mode checks, and independent reruns of every required worker check.
- Implemented lease-serialized integration that freshly re-verifies under the lease,
  merges only the immutable verified SHA, proves exact ordered raw merge parents, runs
  post-merge checks, and records success or durable fail-closed evidence.
- Implemented durable merge intents and cross-process recovery. One exact orphaned merge
  can be adopted under lease and resumed at `wave-integrated-unverified`; an unproven HEAD
  is retained as manual-recovery evidence and is never accepted or reset silently.
- Implemented cleanup derived only from the verified merge ledger, with canonical
  containment, registry/branch/head/cleanliness checks, integration lease ownership,
  immediate target revalidation, and immutable `removing`/`removed` recovery records.
- Added finite no-shell process execution with capped output, reader-error propagation,
  POSIX process groups, and Windows suspended creation followed by kill-on-close Job
  assignment before resume. Validation commands use native platform argv parsing.
- Hardened controller artifacts with lexical containment, retained Windows directory
  handles without delete sharing, POSIX descriptor-relative no-follow traversal,
  descriptor identity checks, exclusive publication, record/count/aggregate bounds, and
  SHA-256 payload-to-filename verification.
- Extended the closed launch record with immutable `workerStartSha`; real later-wave
  verification now binds to the current wave's `startExpectedSha`, not the original run
  base. Retry launch records preserve the same start SHA.
- Preserved the public CLI surface exactly as `verify-worker --repo --plan --receipt` and
  `cleanup --repo --run-id`; `run` and `resume` remain dry-run-only and no live dispatcher
  was introduced.

## Change-necessity reconciliation

The raw-object, process, repository, execution-bundle, hardened-filesystem, auxiliary
journal, and shared-error modules were not all named in the initial file list. Review
demonstrated that keeping these responsibilities inside `state.py` duplicated artifact
policy, exceeded the complexity signal, and created inconsistent resource and filesystem
boundaries. The cohesive extraction reduced `state.py` from a peak of 978 lines to 726
while keeping run-state transitions and CAS ownership in `StateStore`.

The launch-record schema and launcher changed only to bind later-wave workers to their
actual immutable start SHA. This is required by the Task 6 multi-wave contract and does
not enable live worker execution.

## Verification evidence

- Final targeted ancestor-swap and receipt-digest regressions: 2/2 passed.
- Final focused Task 6 suite: 47/47 passed in 302.797 seconds.
- Final repository-wide suite: 195/195 passed in 341.984 seconds.
- Plugin Creator validator: passed.
- Skill validator: passed.
- Python `compileall`: passed.
- `git diff --check`: passed.
- Tracked and untracked trailing-whitespace scan: no matches.
- Final size signal: `state.py` 726 lines; no new responsibility may be added there
  without a new complexity decision.

## Independent review evidence

- Specification review: approved. The exact prior Windows ancestor-swap probes now fail
  safely with zero escaped writes and no substituted read; cross-process merge recovery,
  CLI surfaces, schemas, and compatibility were accepted.
- Quality/security review: ready to merge with no remaining findings. The final targeted
  filesystem probes and payload-digest mismatch were independently rejected as expected.

## Boundary evidence

- No live Codex worker, benchmark, plugin install, production worktree cleanup, automatic
  conflict repair, or installed-plugin mutation occurred.
- One writer owned source changes; specification and quality reviewers were read-only.
- All worktrees created or removed by tests were disposable and lived in temporary test
  repositories. The primary repository remained the sole task checkout.
