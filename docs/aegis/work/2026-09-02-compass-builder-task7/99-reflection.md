# Compass Builder Task 7 reflection

## Outcome

Task 7 established one reproducible repository validation owner and one clean-Windows
CI entry point. Contributors can select focused profiles while every behavior profile
still ends in the full suite, and automation receives stable, fail-closed evidence.

## Key judgments

- The repository tests, not CI package installation, were the canonical owner of the
  hidden `jsonschema` and `referencing` dependency drift. Replacing those imports with
  complete structural assertions plus existing runtime validators preserved coverage
  and restored standard-library dependency closure.
- The harness reused the Task 6 bounded process owner instead of introducing a second
  subprocess implementation. This added explicit validation-specific time limits while
  preserving no-shell execution, continuous pipe draining, and process-tree teardown.
- Workstation-only Plugin Creator, Skill Creator, HOL, and installed-copy checks remain
  explicit release evidence. Omitting them from clean CI is a truthful boundary, not a
  degraded repository run.

## Avoided misfixes and retirement

- No network install, vendored schema engine, conditional skip, or weakened full-suite
  claim was used to accommodate undeclared test dependencies.
- The old undeclared imports are removed and guarded against recurrence.
- Host-specific checkout naming and broad `os.stat` patching assumptions exposed by the
  first clean-Windows run are retired; both tests now own their fixtures and patch the
  narrow seam they actually verify.
- Mutable `actions/checkout@v4` and `actions/setup-python@v5` references were never
  retained; the workflow uses immutable verified commit references.
- Unbounded `subprocess.run(capture_output=True)` behavior was removed before closeout;
  the canonical existing bounded runner is retained with tests and documented limits.

## Evidence and complexity closure

- Both independent reviews are clean after repair.
- `unit` passed 2/2 checks, `audit` passed 11/11 checks, and direct discovery passed all
  205 tests. The affected dependency-closure path also passed 41 tests under `python -S`.
- The new harness is 488 lines and its maintained test owner is 319 lines, both below
  the 800-line pressure signal. Responsibilities remain separated across execution,
  documentation, CI wiring, and tests.
- Complexity Closure: within-budget. No fallback, adapter, duplicate owner, live runtime
  branch, or unresolved governance debt was added.

## Residuals and next boundary

- The first GitHub-hosted Windows run correctly failed on two host-dependent test
  assumptions. The bounded repairs pass locally; same-runtime Python 3.11 confirmation
  is pending the corrective post-push run.
- Package/security/install evidence and any live benchmark remain Task 9 concerns.
- Task 8 may add the synthetic comparator and paired-benchmark runner, but it must begin
  as a new bounded slice and cannot raise the two-builder cap without later authorized
  live evidence.
