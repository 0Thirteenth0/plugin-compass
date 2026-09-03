# Compass Builder benchmark

## Status

The paired runner, fail-closed comparator, synthetic transport tests, and live two-builder
calibration are complete. On 2026-09-02, the authorized standard-library calibration
produced five green measured pairs: median sequential time was `192476` ms and median
parallel time was `118459` ms, a `38.46%` reduction. First-pass acceptance was 5/5 for
both arms, interventions were 0/0, and every blocking safety metric was zero. The
comparator returned `graduated: true`.

This evidence graduates only the current two-builder policy on the recorded calibration
fixture and host. It does not authorize a higher concurrency ceiling or claim universal
speedup. The complete local release evidence is retained under
`.compass-builder/task9/benchmark-paired-r1/` and is intentionally excluded from the
source package.

The checked-in `tests/fixtures/compass_builder/benchmarks/` receipts remain synthetic. Their
30% comparator result proves arithmetic, pairing, immutable-control, safety, and ledger
mechanics only. It is not evidence that parallel Codex builders are faster in practice
and cannot authorize a concurrency increase.

## Protocol

Each representative workload uses one sequential warm-up, one parallel warm-up, and at
least five measured pairs. Odd pairs run sequential first; even pairs run parallel
first. Every arm starts in a fresh disposable clone reset to the recorded fixture SHA.
Both plans must carry a byte-identical run spec, model, effort-policy version, per-story
initial effort vector, handoff digests, acceptance checks, timeout, toolchain evidence,
and normalized non-mode plan digest.

Timing begins immediately before the first worker launch and ends only at a clean,
controller-verified integrated HEAD. No completed attempt is dropped. Failure, timeout,
manual input, repair, scope violation, stale head, unresolved conflict, or incomplete
ledger evidence blocks graduation.

The event ledger is append-only, sequence-numbered, and hash-chained. Each receipt binds
its inclusive sequence range and terminal hash. The aggregate binds the full terminal
range and accounts for every warm-up and measured arm in manifest order.

## Commands

Synthetic comparator self-test:

```powershell
python plugins/compass-builder/scripts/compass_builder.py compare --sequential tests/fixtures/compass_builder/benchmarks/sequential.json --parallel tests/fixtures/compass_builder/benchmarks/parallel.json
```

An authorized live benchmark uses two previously validated plan bundles for the same
auto-mode run spec:

```powershell
python plugins/compass-builder/scripts/compass_builder.py benchmark --fixture PATH --sequential-plan SEQUENTIAL_PLAN.json --parallel-plan PARALLEL_PLAN.json --pairs 5 --timeout-ms 600000 --output OUTPUT_DIRECTORY
```

The output directory is published atomically only after every planned attempt has a
receipt. It contains `aggregate.json`, ordered `receipt-NNN.json` files,
`sequential.json`, `parallel.json`, and `events.jsonl`.

## Graduation rule

For every workload, parallel must have at least 20% lower median wall-clock time using
unrounded decimal arithmetic, equal or better first-pass acceptance, and no increase in
human interventions. Manual or unresolved conflicts, write-scope violations, stale-head
events, and timeouts must all be zero. Two-decimal round-half-up formatting is display
only and cannot promote a result below the exact threshold.
