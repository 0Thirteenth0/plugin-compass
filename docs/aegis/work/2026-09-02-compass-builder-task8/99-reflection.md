# Compass Builder Task 8 reflection

## Outcome

Task 8 now has one executable public controller, a deterministic paired benchmark
runner, and a fail-closed comparator. Synthetic tests exercise the complete scheduling,
worktree, verification, serial-integration, receipt, ledger, aggregation, and comparison
route without consuming a live model or representing synthetic timing as performance.

## Key judgments

- The plan's original comparator-only shape could not truthfully benchmark the public
  run route because no production controller composed the Task 6 and 7 primitives. The
  user authorized that bounded missing owner, and `controller.py` now owns the lifecycle
  without moving policy into the CLI.
- Speed is the optimization objective, but correctness gates dominate it. The comparator
  therefore applies exact controls, first-pass, intervention, safety, and complete-
  attempt checks before the unrounded Decimal 20% threshold.
- A fresh repository per arm and alternating pair order reduce contamination and order
  bias. Failed or interrupted attempts remain terminal evidence and cannot be omitted to
  improve the result.

## Governance and avoided misfixes

- `run --dry-run` is retained as an explicit non-model projection. It neither competes
  with nor silently substitutes for the canonical live controller.
- No daemon, generic orchestration framework, alternate Git/process owner, interactive
  conflict repair, shell execution, live model fallback, or favorable-result filter was
  added.
- Native Codex scheduling may retire this controller only after it exposes contract-
  equivalent isolation, binding, bounds, barriers, serial integration, and receipts.
- ADR action is `skip`: the repository has no ADR system, while the Builder contract and
  approved plan already durably own this decision. Creating another decision owner would
  add duplication rather than governance.

## Evidence and complexity closure

- Exact-final discovery passed 217/217 tests. The focused comparator/runner suite passed
  9/9, direct controller integration passed 3/3, the audit profile passed all 11 checks,
  compilation passed, and diff hygiene is clean.
- New production owners are 498, 202, and 351 lines; responsibilities remain separated.
  The 731-line state module received only the minimum lifecycle seam.
- Complexity Closure: within-budget. No fallback, compatibility adapter, duplicate
  controller, or unresolved ownership debt was added.

## Residuals and next boundary

- Source and synthetic mechanics have high confidence. Actual Codex CLI structured-
  output/stdin behavior and model-backed timing remain untested until an authorized live
  Task 9 run; production-live confidence is therefore intentionally lower.
- The two-builder calibration ceiling remains unchanged. Synthetic 30% timing cannot
  authorize an increase.
- Live resume, packaging, HOL/security scanning, installed-copy validation, and the live
  benchmark remain Task 9 work.
- The Task 8 delta is uncommitted and unpushed. Separate Git authorization is required
  before staging, committing, or pushing it.
