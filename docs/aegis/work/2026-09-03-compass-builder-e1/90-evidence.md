# Compass Builder Workstream E1 evidence

Date: 2026-09-03
Scope: deterministic native worker-token telemetry and synthetic benchmark reporting
Baseline: `docs/aegis/baseline/2026-09-03-workstreams-f3-d3-baseline.md`
Plan: `docs/aegis/plans/2026-09-03-workstreams-f4-e1.md`

## Boundary

E1 observes the top-level terminal usage record already emitted by `codex exec --json`.
It does not inspect sessions or desktop state, estimate price, set budgets, route models,
change scheduling policy, execute discovered skills, or add a second worker owner. The
existing worker receipt and benchmark receipt/aggregate/comparison v1 contracts retain
their meaning. Live workers and the five-pair sequential/parallel benchmark remain
outside this deterministic implementation slice.

## Implemented evidence contracts

- `compass-builder.worker-usage.v1` binds one worker launch and its optional validated
  receipt to bounded raw input, cached-input, cache-write-input, output, and reasoning
  token counts. Missing or invalid usage is explicit and never normalized to zero,
  except for an absent upstream cache-write field whose absence is separately recorded.
- `compass-builder.retry-evidence.v1` is the sole durable attempt-two authority. It is
  content-addressed and bound to run, story, evidence digest, and the canonical
  attempt-one launch. Only controller-owned reasoning evidence authorizes a retry.
- `compass-builder.benchmark-attempt-usage.v1` binds a benchmark receipt to every
  public `(runId, storyId, attempt, launchDigest)` lifecycle and worker-usage record.
- `compass-builder.benchmark-token-report.v1` binds the benchmark aggregate, all
  attempt-usage digests, optional unchanged v1 comparison output, measured wall-clock
  and quality evidence, and per-arm/per-pair token summaries. Warmups are retained as
  provenance but excluded from measured comparisons.

Comparison tokens are exactly `inputTokens + outputTokens`. Cached input and reasoning
output remain components and are not double-counted. Retry overhead includes every
attempt greater than one. Failed-attempt overhead is derived from authoritative
verified/integrated lifecycle evidence, not a worker's self-reported success. Any
missing or invalid measured usage makes the token verdict explicitly incomplete.

## Strict RED evidence

The parser/contract tests were written before E1a production code. They failed because
the public model, parser, schema, and exports did not exist. Subsequent RED cases proved
that duplicate/conflicting terminal events, non-LF Unicode separators, booleans and
out-of-range counts, absent usage, and detached receipt identities required explicit
fail-closed handling.

The E1b controller tests were written before transport and journal wiring. They failed
on the missing immutable usage journal, missing one-record-per-attempt behavior, and
missing public event. A later adversarial retry-authority cycle ran five focused methods
with 18 expected failures in 25.978 seconds. It proved that generic `failure.v1`
evidence could incorrectly authorize attempt two and that no closed retry-evidence
contract or API existed.

The E1c benchmark tests were written before production code. Ten focused tests ran in
11.285 seconds and produced 15 intended assertion failures with no errors, proving the
attempt/report APIs, schemas, lifecycle identity, persistence, and fake-executor
incompleteness behavior were absent. Later RED cycles proved three derived-evidence
defects: tampered attempt totals were accepted, an embedded v1 comparison digest could
detach, and a succeeded-but-not-integrated worker incorrectly contributed zero failed
overhead.

## GREEN evidence

### E1a parser and contract

- Focused parser/schema validation: 45 tests passed.
- The parser accepts only a top-level `turn.completed.usage` event, preserves LF-only
  framing, bounds bytes/events/counts, rejects duplicate JSON keys, and applies the
  cache/reasoning subset constraints in both Python and schema parity tests.

### E1b controller and retry authority

- Final retry-authority matrix: 7/7 focused methods passed in 39.773 seconds.
- E1a/E1b/launcher regression: 55/55 passed in 242.606 seconds.
- State/models/controller/verifier/worktree regression: 81/81 passed in 267.853 seconds.
- Independent specification review: approved; 43/43 E1 usage regressions passed.
- Independent quality/security review: approved; 5/5 adversarial retry cases passed
  in 39.948 seconds.
- Coordinator fresh falsifying check: 6/6 retry schema, cross-binding, ambiguity,
  generic-failure-only, invalid source/kind, and valid authority cases passed in
  54.164 seconds.

### E1c synthetic benchmark telemetry

- First focused GREEN: 10/10 lifecycle and aggregation tests passed in 9.861 seconds.
- Runner integration: 14/14 E1c plus benchmark-runner tests passed in 19.180 seconds.
- Derived-contract hardening: 5/5 passed in 0.032 seconds.
- Final focused E1c suite: 13/13 passed in 0.171 seconds.
- E1c and adjacent benchmark/model/usage/controller/launcher/state regression:
  127/127 passed in 336.836 seconds.
- Verifier/integrator/cleanup/disposable-worktree integration: 59/59 passed in
  542.183 seconds, with one expected Windows skip for the POSIX process-group case.

### E1c independent-review repairs

The post-implementation specification review reproduced three important validation
gaps. Before source or schema edits, 17 focused tests ran with 13 passes, two expected
failures, and two expected errors: a digest-bound usage record could be omitted from an
incomplete attempt, the public cross-document report binder was absent, and the two
schemas lacked the required sibling-equality and complete-verdict declarations.

The repaired contracts now:

- require embedded usage records to exactly match every worker with a non-null
  `workerUsageDigest`, while allowing omission only when both digest and status are null;
- keep token-report shape validation document-local and expose a separate public binder
  that validates the aggregate, receipt order/digests, attempt usage, optional v1
  comparison, and every derived arm/pair field before a built report is returned;
- declare measured `trialNumber == pairNumber` through the closed, versioned semantic
  extension and enforce warmup zero values structurally;
- forbid `tokenVerdict=complete` with any token-incompleteness reason while continuing
  to allow `v1-comparison-unavailable` alone with a complete token verdict.

Fresh repair evidence:

- Focused E1c GREEN: 17/17 passed in 0.241 seconds.
- E1c benchmark/model/runner regression: 52/52 passed in 20.619 seconds.
- Adjacent worker-usage/controller regression: 43/43 passed in 252.791 seconds.
- Python compilation, both schema JSON parses, Plugin Creator validation, Skill Creator
  validation, and `git diff --check` passed.
- Optional locally installed Draft 2020-12 validation accepted both schemas and valid
  fixtures, rejected nonzero warmup fields and a complete verdict with
  `missing-attempt-usage`, and accepted `v1-comparison-unavailable` alone. Repository
  tests remain standard-library-only; sibling equality is evaluated by the repository
  semantic validator.
- A fresh audit ran after focused GREEN. Eleven of twelve checks passed, including
  contract/model/schema, Git integration, harness self-tests, and diff validation. The
  repository-wide discovery command exceeded its 900-second bound, so this fresh audit
  correctly reported `status: failed` and `fullValidation: false`; no full-validation
  claim is made from that run.

A subsequent specification re-review found one remaining representable inverse-parity
gap: the JSON Schema rejected token-incompleteness reasons with a complete verdict but
accepted an incomplete verdict with no token-incompleteness reason. The narrow RED test
failed 1/1 because the conditional had no `else`. The schema now requires at least one
of `incomplete-attempt-usage`, `missing-attempt-usage`, or `invalid-attempt-usage` when
`tokenVerdict` is `incomplete`; `v1-comparison-unavailable` alone remains non-token-
incomplete. Focused benchmark-usage plus model validation passed 48/48 in 0.218 seconds.
Schema parsing, test-file Python compilation, optional Draft 2020-12 probes for empty,
v1-only, and genuine token-incomplete reason sets, and `git diff --check` all passed.
No additional repository audit was started because the coordinator already owned a
direct full-discovery run.

The later quality re-review reproduced five important E1c gaps and one minor parity
gap. Before production edits, 40 focused benchmark-usage, runner, and comparison tests
ran in 25.008 seconds with 20 intended failures: one missing explicit run authority,
four reordered worker lifecycles, green receipts with a missing story or a failed/
unintegrated worker, receipt/usage effort drift, six scalar schema-parity cases,
boolean `tokenDelta`, four forged v1 comparison decision fields, and one concurrent
ledger-chain corruption case. The byte-stable v1 comparison characterization and valid
cross-worker interleaving controls remained green. A subsequent parity audit added two
narrow RED probes and confirmed that 129 distinct attempt stories and an overbound
embedded blocking metric were also accepted by Python despite their schema bounds.

The repaired benchmark telemetry now requires one top-level attempt `runId` supplied
from the authoritative bundle run spec, binds every worker identity and usage event to
that run, checks usage effort against receipt controls, and requires every ordered story
in a green receipt to reach an observed succeeded completion and matching branch import.
Each worker follows an independent launch -> usage -> completion -> import state machine;
cross-worker interleaving remains valid. The event ledger locks sequence allocation,
predecessor read, hashing, append, and tail update as one transaction, while the runner
serializes its public controller-event collector. E1c comparison binding now reuses the
single canonical v1 computation in `benchmark.py`, including improvement, eligibility,
reasons, and top-level graduation, without changing public v1 output bytes. Signed token
deltas reject booleans, report medians use the exact integer-or-half-integer wire format,
and Python enforces the schema's metric, status, blocking-metric, and story bounds.

Fresh quality-repair GREEN evidence:

- Final focused E1c/runner/comparison suite: 41/41 passed in 23.801 seconds.
- E1c/runner/comparison/model/worker-usage regression: 90/90 passed in 23.397 seconds.
- Python compilation, schema JSON parsing, local-registry optional Draft 2020-12 fixture
  validation, Plugin Creator package validation, and `git diff --check` passed.
- The runner concurrency fake exercised parallel public event sinks; the 256-event
  barrier test produced one unique contiguous sequence and exact predecessor/hash chain.
- No full audit was started because the coordinator owns final repository discovery.

A further quality re-review found one important retry-chain gap and one minor embedded-
comparison parity gap. Before production edits, five focused methods ran in 0.106
seconds with seven intended failures: the valid low-to-medium retry was incomplete,
same-effort retry was complete, an attempt-2-only document validated, and comparison
reasons accepted booleans, integers, empty strings, and 4097-character strings. The
missing/early/after-success retry probes already failed closed under the older blanket
effort check and remained as regression guards. A second two-method RED cycle produced
three intended failures, proving rehashed attempt documents could forge retry status/
effort and detach attempt-one effort from receipt controls at report binding.

The repair now requires attempt two to launch only after the same story's attempt one
has completed unsuccessfully, forbids retry after success/import, preserves contiguous
`[1]` or `[1, 2]` identities, requires a strictly higher retry effort, and permits a
green receipt with a failed first attempt only when the higher-effort retry succeeds and
imports. Persisted attempt shape validation independently rejects implausible prior
status or same/lower effort, and report binding rechecks attempt-one effort against the
bound receipt. `EFFORT_ORDER` moved beside `EFFORTS` in the shared primitive validation
owner; launcher, handoff, and E1c now reuse that single canonical tuple. Embedded v1
comparison reasons now mirror the schema's exact string type and 1-4096 character
bounds.

Fresh retry-chain repair evidence:

- Narrow retry/parity GREEN: 7/7 passed in 0.162 seconds.
- E1c/runner/comparison/model/launcher/handoff regression: 96/96 passed in 18.948
  seconds.
- Python compilation, all schema JSON parses, optional Draft 2020-12 reason-boundary
  probes, Plugin Creator package validation, and `git diff --check` passed.
- No full audit was started; the coordinator retained final-discovery ownership.

The final exact-binding re-review found that a rehashed embedded worker usage record
could change its launch digest or terminal status while retaining the same worker key.
One strict RED method produced nine intended failures in 0.204 seconds: launch-digest,
ordinary terminal-status, and retry-predecessor status mismatches were each accepted by
standalone attempt validation, report construction, and a fully re-digested report
binder input. The attempt validator now retains each worker's launch digest, terminal
status, and usage digest by exact `(runId, storyId, attempt)` key and requires the
embedded record to match all three. Null digest/status incomplete workers remain
recordless. The exact-binding probe passed 1/1 in 0.155 seconds, and the focused E1c,
runner, model, and canonical retry-effort regression passed 83/83 in 22.062 seconds.

### Repository gates

```powershell
python scripts/check_repo_harness.py --profile audit --format json
```

The pre-review implementation audit exited `0` with all 12 checks passed and
`fullValidation: true`. After the review repairs, a fresh harness audit passed 11 of 12
checks; its only incomplete check was repository-wide discovery exceeding the wrapper's
fixed 900-second command timeout, so that receipt correctly reported
`fullValidation: false` rather than hiding the timeout.

The coordinator then ran the timed-out check directly against the final stable worktree:

```powershell
python -m unittest discover -s tests -q
```

Final result: exit `0`; `Ran 455 tests in 814.751s`; `OK (skipped=4)`. The four skips
are the documented platform-specific cases. Package validation, Python syntax
compilation, schema/fixture checks, focused specification and quality probes, and
`git diff --check` also passed after the final production edit.

## Changed-file inventory

Production owners and wiring:

- `plugins/compass-builder/compass_builder/_usage_models.py`
- `plugins/compass-builder/compass_builder/usage.py`
- `plugins/compass-builder/compass_builder/_retry_models.py`
- `plugins/compass-builder/compass_builder/_benchmark_usage_models.py`
- `plugins/compass-builder/compass_builder/benchmark_usage.py`
- `plugins/compass-builder/compass_builder/_validation.py`
- `plugins/compass-builder/compass_builder/models.py`
- `plugins/compass-builder/compass_builder/__init__.py`
- `plugins/compass-builder/compass_builder/controller.py`
- `plugins/compass-builder/compass_builder/durable_artifacts.py`
- `plugins/compass-builder/compass_builder/launcher.py`
- `plugins/compass-builder/compass_builder/handoff.py`
- `plugins/compass-builder/compass_builder/state.py`
- `plugins/compass-builder/compass_builder/benchmark.py`
- `plugins/compass-builder/compass_builder/benchmark_runner.py`

Public schemas and fixtures:

- `plugins/compass-builder/schemas/worker-usage.schema.json`
- `plugins/compass-builder/schemas/retry-evidence.schema.json`
- `plugins/compass-builder/schemas/benchmark-attempt-usage.schema.json`
- `plugins/compass-builder/schemas/benchmark-token-report.schema.json`
- `tests/fixtures/compass_builder/worker-usage.valid.json`
- `tests/fixtures/compass_builder/worker-usage.events.jsonl`
- `tests/fixtures/compass_builder/retry-evidence.valid.json`
- `tests/fixtures/compass_builder/benchmark-attempt-usage.valid.json`
- `tests/fixtures/compass_builder/benchmark-token-report.valid.json`

Tests and documentation:

- `tests/test_builder_usage.py`
- `tests/test_builder_controller_usage.py`
- `tests/test_builder_benchmark_usage.py`
- `tests/test_builder_benchmark_runner.py`
- `tests/test_builder_compare.py`
- `tests/test_builder_models.py`
- `docs/COMPASS_BUILDER_CONTRACT.md`
- `docs/VALIDATION.md`
- `plugins/compass-builder/README.md`

## Limitations and safe stop

- All E1 evidence is synthetic or repository-local; no live Codex worker or paid
  benchmark was run.
- The five-pair matched benchmark, performance graduation decision, installed-copy
  validation, and any adaptive scheduler policy remain separately authorized work.
- No plugin installation, cache mutation, hook enablement, commit, push, publication,
  or cleanup of preserved user changes occurred.
- No Codex restart is required for source, test, schema, or documentation changes.
  Installing a later plugin build would be a separately authorized action and may then
  require a restart.
- The post-review audit wrapper retained `fullValidation: false` because its fixed
  command timeout expired. The same final repository-wide discovery command completed
  directly in 814.751 seconds with all 455 tests passing and four expected platform
  skips; no wrapper receipt was rewritten or reinterpreted.
