# Compass Builder Task 7 evidence

## Start evidence

- Repository: `C:/Users/jiahu/Desktop/Plugin Compass`
- Start HEAD: `403214b5381c2bc99300e96e5c0dd563a162baa3`
- Branch: `main`
- Upstream divergence: `0 0`
- Staged, unstaged, and untracked paths: none
- Active Git operations: none
- Worktrees: one primary checkout
- Task 6 full suite: 195/195 passed before its scoped commit.
- Repository harness, Task 7 self-test, and `.github` workflow: absent at start.

## Boundary evidence

- No live worker, benchmark, installed-copy action, plugin mutation, external service, or
  Git lifecycle mutation occurred during Task 7 setup.
- The parent TDD route is `off / skipped`; verification remains post-change and
  evidence-based.

## Implementation evidence

- Added `scripts/check_repo_harness.py` with closed `docs`, `contracts`, `unit`,
  `integration`, and `audit` profiles; stable human/JSON receipts; truthful passed,
  failed, skipped, and degraded states; and a final full suite for every behavior
  profile.
- Reused the existing `compass_builder.process_runner` owner unchanged. Python checks
  have a 900-second bound, Git has a 60-second bound, each stream is capped at 1,048,576
  bytes, and timeout or overflow is a failed actionable check rather than a skip.
- Added `docs/VALIDATION.md` with exact repository and workstation-only gates and the
  installed-copy authorization boundary.
- Added `.github/workflows/validate.yml` for `windows-latest` using repository files,
  Python 3.11, and Git only. `actions/checkout` and `actions/setup-python` use immutable
  40-character commit references, checkout credential persistence is disabled, and no
  workstation plugin path appears in CI.
- Added `tests/test_repo_harness.py`, including a standard-library import-closure guard
  over repository Python sources and regression probes for both removed dependencies.

## Specification-review repair evidence

- Initial specification review found that clean Windows CI was not dependency-closed:
  `tests/test_builder_launcher.py` imported `jsonschema`, while
  `tests/test_builder_state.py` imported `jsonschema` and `referencing`; the repository
  has no dependency manifest and the accepted Task 7 contract permits only repository
  files plus standard Python/Git.
- Decisive reproduction:
  `python -S -m unittest tests.test_builder_launcher tests.test_builder_state -v`
  failed with two `ModuleNotFoundError: No module named 'jsonschema'` import errors before
  running tests.
- Repository search found no other `jsonschema`/`referencing` imports or package
  manifest. Installing from the network, vendoring a schema engine, skipping tests, or
  labeling skipped evidence full would violate the Task 7 boundary.
- PatchShape: two downstream test-only dependency imports.
- CanonicalOwner: the tests' schema/runtime assertions and Task 7 dependency-closure
  regression, not the Windows workflow or production runtime.
- UpwardDrillSignal: CI is a consumer of the suite and must not hide an undeclared test
  dependency.
- Decision: fix the test owners and add recurrence detection; production runtime remains
  unchanged.
- Causal topology: single-root-multi-symptom. The absence of repository dependency-
  closure enforcement allowed both test modules to rely on workstation packages.
- Counterfactual/falsifier: if the imports were not the active cause, `python -S` would
  fail elsewhere first; it failed at exactly the two searched imports. The repaired
  command must run the affected modules and harness self-tests successfully under
  `python -S`.

## Review evidence

- Independent specification review: no findings and `Spec compliant`. It inspected the
  complete launch-record and plan-bundle schema assertions, dependency closure, profile
  composition, CI commands, skip semantics, and deterministic output. Its independent
  targeted `python -S` run passed.
- Initial independent quality review found two Important issues: unbounded subprocess
  execution and mutable GitHub Action tags. The implementation reused the existing
  bounded process owner and pinned both actions to verified commit references.
- Independent quality re-review: no Critical, Important, or Minor findings; both issues
  closed; ready for coordinator closeout. Its real Windows tests covered timeout,
  overflow, descendant-tree termination, and pipe-reader failure.
- Non-blocking review residual: importing `compass_builder.process_runner` passes through
  the package initializer, so an unrelated eager-import regression could prevent the
  harness from producing a structured receipt. No import cycle or current failure was
  found; direct file loading may be considered only if future bootstrap evidence
  justifies the added mechanism.

## Fresh coordinator verification

- `python -S -m unittest tests.test_repo_harness -v`: 10/10 passed in 0.148 seconds.
- `python -S -m unittest tests.test_builder_launcher tests.test_builder_state tests.test_repo_harness`:
  41/41 passed in 31.728 seconds without site packages.
- `python -m py_compile scripts/check_repo_harness.py tests/test_repo_harness.py`: exit 0.
- `python scripts/check_repo_harness.py --profile unit`: exit 0; 2 checks passed,
  including the final repository-wide suite; no failures or skips.
- `python scripts/check_repo_harness.py --profile audit`: exit 0; 11 checks passed,
  including documentation, contracts, integration, harness self-tests, diff hygiene,
  and the final repository-wide suite; no failures or skips.
- `python -m unittest discover -s tests`: 205/205 passed in 327.099 seconds.
- `git diff --check`: exit 0 before lifecycle-record finalization; it is rerun during
  Git closeout after these records are complete.

## Boundaries and residual evidence

- No live worker, benchmark, plugin install/update, installed-copy execution, external
  service, or production runtime change occurred.
- GitHub-hosted `windows-latest` execution remains external evidence to be produced by
  the workflow after push; static workflow checks and direct Windows workstation tests
  are green but do not substitute for that run.
- Workstation-only Plugin Creator, Skill Creator, HOL, and installed-copy checks remain
  documented release gates for Task 9; Task 7 CI does not infer them as passed.

## Post-push Windows CI feedback and corrective evidence

- Initial push: commit `b960df177196a9d532de6e0a7580b65706867b6d` reached
  `origin/main`; local/upstream divergence was `0 0` and the worktree was clean.
- GitHub Actions run `33625923165` used the new `windows-latest` workflow. Checkout,
  Python 3.11 setup, and contract-model validation passed. Repository discovery ran all
  205 tests but ended with one failure and one error, so later workflow steps correctly
  did not run.
- Failure root: `test_fixture_inventory_resolves_relative_roots_with_spaces` inferred a
  path-with-spaces condition from the developer checkout name `Plugin Compass`; the
  hosted checkout is named `plugin-compass`. The test now asserts the exact fixture path
  and adds an isolated inventory whose inventory directory and relative plugin root both
  contain spaces.
- Error root: the auxiliary-journal identity-swap test patched all
  `compass_builder.secure_files.os.stat` calls. On hosted Python 3.11, reconstructing an
  `os.stat_result` in that patch left the unrelated optional `st_file_attributes` field
  as `None`, causing the reparse precheck to fail before the intended identity check.
  The test now patches only `_stat_at`, the exact identity seam it is meant to falsify,
  and returns only the changed device/inode evidence consumed there.
- Canonical owner: both defects were host-dependent test assumptions. Production
  adapter, secure-file, controller, and workflow code remain unchanged.
- Fresh targeted check under `python -S`: both adapter path tests and the journal
  identity-swap test passed, 3/3 in 1.015 seconds.
- Fresh affected-module check under `python -S`: `tests.test_adapters`,
  `tests.test_builder_state`, and `tests.test_repo_harness` passed, 43/43 in 31.250
  seconds.
- Fresh direct discovery: 206/206 passed in 332.236 seconds.
- `python -m py_compile tests/test_adapters.py tests/test_builder_state.py` and
  `git diff --check`: exit 0.
- Exact Python 3.11 is not installed on the workstation. The corrective hosted workflow
  is the remaining direct same-runtime falsifier; local Python 3.14 evidence cannot be
  represented as a substitute.
