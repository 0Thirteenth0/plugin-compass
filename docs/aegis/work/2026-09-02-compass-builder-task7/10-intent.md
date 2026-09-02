# Compass Builder Task 7 intent

## Requested outcome

Continue the approved Compass Builder MVP with a reproducible repository validation
harness and Windows CI that depend only on repository files and standard Python/Git.

## Slice Card

- Goal: give contributors and CI deterministic `docs`, `contracts`, `unit`,
  `integration`, and `audit` validation profiles with truthful degraded/skip reporting.
- Parent plan/spec: `docs/aegis/plans/2026-09-01-compass-builder-mvp.md`, Task 7,
  and `docs/COMPASS_BUILDER_CONTRACT.md`.
- Files: `scripts/check_repo_harness.py`, `docs/VALIDATION.md`,
  `.github/workflows/validate.yml`, `tests/test_repo_harness.py`, bounded dependency-
  closure repairs in `tests/test_builder_launcher.py` and `tests/test_builder_state.py`,
  and these Task 7 lifecycle records.
- Boundary: no controller runtime behavior, live Codex worker, benchmark, plugin
  installation, installed-copy execution, or workstation-only validator dependency in CI.
- Verification: harness self-tests; `unit` and `audit` profile executions; full suite;
  YAML/static workflow checks; `git diff --check`; independent specification and quality
  review.
- Stop: hidden workstation dependency, recursive self-test execution, unstable JSON,
  silent skips, a degraded result labeled full, or CI that cannot run on Windows using
  repository files plus standard Python/Git.

## BaselineReadSetHint and usage

- Required and acknowledged: `docs/PRODUCT_CONTRACT.md`, `docs/TECHNICAL_DESIGN.md`,
  `docs/COMPASS_BUILDER_CONTRACT.md`, `SECURITY.md`, the active MVP plan, the initial
  Builder baseline, and Task 6 intent/checkpoint/evidence.
- Cited by the parent plan: all required sources above.
- Active `AGENTS.md` or `CONTEXT.md`: none present in the repository.
- Missing: none.

## ImpactStatementDraft

This slice adds the planned repository validation owner and Windows CI entry point. It
does not alter Plugin Compass's read-only role or Compass Builder's controller behavior.
Optional workstation-only release gates remain explicit local checks and never become
hidden CI requirements.

## Execution Readiness View

- Intent lock: reproducible evidence toward the fastest verified result, not broader
  orchestration or a performance claim.
- Scope fence: the four Task 7 artifacts, two test-only dependency-closure repairs, and
  lifecycle records only; production runtime remains untouched.
- Baseline lock: clean synchronized `main` at `403214b` and the accepted product,
  security, Builder, and plan contracts.
- Owner constraints: the harness owns profile composition/reporting; repository tests
  own behavior; GitHub Actions invokes repository commands; local release tools retain
  their existing validation ownership.
- Compatibility boundary: Windows, Python 3.11+, Git, paths with spaces, standard
  library, deterministic JSON, and no shell interpolation in Python.
- Retirement boundary: no old repository harness exists; future native CI or package
  validation may replace only equivalent checks with explicit evidence.
- Test obligations: stable ordering/JSON, success/failure/skip/degraded semantics,
  unavailable-command evidence, corrective directions, profile composition, and
  workstation-independent CI.
- Review gates: one implementer, independent specification approval, independent
  quality review, then fresh coordinator verification.
- Drift/rewind: return to plan review if the harness needs external services, installed
  plugins, live workers, benchmark execution, or a new controller responsibility.

## TDD Route

- Mode: off
- Decision: skipped
- Strict authority: not applicable
- Test posture: minimum implementation plus focused post-change harness tests and full
  regression verification.
- Reason: inherited from the approved parent plan; no strict TDD request exists.

## Change Necessity and complexity

Change Necessity:

- User-visible need: contributors and Windows CI currently lack one reproducible,
  truthful repository validation entry point.
- No-change / non-code option: documentation alone cannot execute, order, classify, or
  serialize validation results, and the repository has no CI workflow.
- Why code change is necessary: deterministic profiles, bounded subprocess evidence,
  stable JSON, and fail-closed exit semantics require executable repository logic.
- Minimum change boundary: one single-purpose standard-library harness, one focused test
  module, one validation guide, and one Windows workflow.
- Decision: code-change.

Complexity Budget:

- Artifact class: validation source, maintained tests, documentation, and CI config.
- Target files: four planned Task 7 artifacts plus two existing test-only import/coverage
  repairs; controller runtime files remain untouched.
- Current pressure: no repository harness or `.github` workflow exists.
- Projected post-change pressure: within budget if process execution, result modeling,
  profile composition, and rendering remain cohesive and testable in one bounded script;
  large existing test modules receive only local assertion replacements.
- Budget result: within-budget.
- Planned governance: keep the harness below the plan's 800-line pressure signal; use
  small immutable result/profile structures; avoid a general task runner.

Pre-Edit Owner-Fit Decision:

- Edit intent: new-responsibility.
- Owner fit: the parent plan explicitly assigns repository validation to the new harness,
  guide, tests, and workflow.
- Safer edit boundary: do not place validation orchestration in Compass Builder runtime or
  Plugin Compass.
- Decision: add owner file.

## Worktree necessity

No task-owned worktree is created. The checkout is clean, there is one implementation
writer, reviews are read-only, and the coordinator alone owns Git lifecycle mutation.
