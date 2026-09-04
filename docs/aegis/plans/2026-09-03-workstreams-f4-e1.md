# Workstreams F4 and E1 implementation plan

Status: completed
Authorized: 2026-09-03
Baseline: `docs/aegis/baseline/2026-09-03-workstreams-f3-d3-baseline.md`

## Task start snapshot

- Starting HEAD: `87d0dc8c4163577dd968549b37fe6cd3b745cc62`.
- The worktree already contained the preserved F1-F3, D1-D3, and G0 changes listed by
  `git status --short`; they are inputs, not cleanup targets.
- Coordinator retains Git lifecycle ownership. This slice is explicitly not authorized
  to stage or commit, so task checkpoints use path inventories and fresh test evidence
  rather than intermediate commits.

## Goal

Close the standalone-skill discovery release boundary with adversarial evidence (F4),
and add native token-usage telemetry to Compass Builder's existing sequential/parallel
benchmark (E1). Telemetry measures speed, observed tokens, and accepted outcomes; it is
not a pricing, budget, or cost-routing feature.

This slice stops before a live paired benchmark. It does not install or enable plugins,
enable hooks, mutate plugin caches, add a monitor, execute discovered skills, implement
G1, commit, push, publish, or clean up preserved worktree changes.

## Requirement readiness and existence check

The requirements are ready for deterministic implementation. Workstream F already has
the source-neutral model, bounded adapters, and CLI/ranking integration from F1-F3.
Workstream E already has isolated `codex exec --json` workers and a paired benchmark.

The authoritative local plugin query, `codex plugin list --json`, returned empty
`installed` and `available` lists, so it does not authorize adopting another plugin.
A bounded source-presence check found no local OpenAI Plugin Eval, Codex Usage Monitor,
or Codex Monitor capability. The cached `llm-cost-optimizer` is monetary advisory logic,
not a passive collector, and `collab-proof` consumes Claude transcript files rather than
the direct ephemeral Codex worker stream. OpenAI Plugin Eval runs its own isolated live
evaluation workflow and would duplicate Builder attempt ownership.

Decision: implement a small native collector. Reuse only the documented upstream Codex
event semantics: a top-level terminal `turn.completed` JSONL event owns the provider
usage object. Do not add an external runtime dependency.

## Architecture and compatibility boundary

### F4 release closure

F4 is test-and-documentation pressure on the F1-F3 design, not a new discovery engine.
Add adversarial fixtures/tests for every remaining product requirement: missing and
unreadable roots, malformed/oversized metadata, traversal and count bounds, symlink or
reparse escapes, Windows paths with spaces, distinct duplicate names, ambiguous bare
identities, deterministic serialization, no instruction execution, and no writes outside
test temporary directories. Production edits are limited to defects demonstrated by a
failing test. Plugin identity remains authoritative only through the Codex CLI; cache
contents never imply installed state.

### E1 direct-stream collector

Add a dedicated `usage.py` owner and a closed Python validator/JSON Schema for
`compass-builder.worker-usage.v1`. The record binds:

- run, story, launch attempt, exact model, effort, and canonical launch digest;
- the worker-receipt digest when a receipt exists;
- observed/unavailable status and one bounded machine reason;
- raw input, cached-input, cache-write-input, output, and reasoning-output counts.

Only a top-level `turn.completed` event from the direct worker stdout is eligible.
Negative, Boolean, non-integer, malformed, duplicate, or conflicting terminal events
produce explicit unavailable evidence; they never become zero. A timed-out or failed
transport also receives exactly one missing observation. The collector reads bytes only,
does not execute event content, and does not inspect sessions, hooks, status lines, or
desktop state.

The controller intercepts the transport's usage observation, binds it to the validated
worker receipt where available, emits exactly one finalized `worker-usage` event, and
stores an immutable record in a new allowlisted `worker-usage` artifact journal. External
test transports that do not report usage produce truthful missing evidence. Concurrent
workers are serialized only at the journal boundary; scheduling behavior is unchanged.

### E1 benchmark telemetry

Keep `worker-receipt.v1`, `benchmark-receipt.v1`, `benchmark-aggregate.v1`, and the
current wall-clock comparison unchanged. Add separate versioned
`benchmark-attempt-usage.v1` evidence and `benchmark-token-report.v1` output that bind
the existing benchmark receipt digest and the ordered worker-usage records for the
attempt. The report presents time, tokens, quality, retries, conflicts, failures, and
interventions together.

Derived comparison tokens are `inputTokens + outputTokens`. Cached input is a component
of input, and reasoning output is a component of output, so neither is added again.
Cache ratio is `cachedInputTokens / inputTokens`; reasoning share is
`reasoningOutputTokens / outputTokens`, with explicit unavailable values for zero
denominators. Retry and failed-attempt overhead includes every consumed worker attempt.
Any missing usage makes the token verdict incomplete. E1 reports paired token delta and
ratio but defines no token-overhead graduation threshold and does not route scheduling.

## Technical stack and ownership

- Python 3 standard library only; no new dependency.
- Existing canonical JSON, validation primitives, secure-file helpers, artifact journal,
  controller event sink, and benchmark event ledger.
- New source owners: `compass_builder/usage.py` and
  `compass_builder/_usage_models.py`; controller and benchmark runner receive wiring only.
- New schemas and synthetic fixtures under the existing Compass Builder schema/test
  roots.
- F4 remains under Plugin Compass tests/docs unless RED evidence requires a focused fix.

## Strict TDD route

1. **F4 adversarial RED.** Map the original standalone-skill requirements to existing
   coverage, add only missing adversarial cases, and witness focused failures before any
   production correction.
2. **E1 parser/contract RED.** Add fixtures and failing tests for one valid terminal
   event, cached/reasoning counts, missing usage, malformed types/counts, duplicates,
   conflicting events, deterministic validation, and no content execution.
3. **E1 controller RED.** Prove successful, failed, timed-out, and telemetry-absent
   transports each create one immutable attempt-bound record without altering launch
   isolation or v1 receipts. Prove two-worker concurrency does not overwrite evidence.
4. **E1 benchmark RED.** Prove single/parallel aggregation, retry/failure accounting,
   no component double-counting, receipt binding, missing-data incompleteness, deterministic
   report output, and a synthetic matched comparison.
5. Implement the smallest production change for each witnessed failure, refactor only
   while focused tests remain green, then run the adjacent and full suites.

Tests must use synthetic JSONL and temporary repositories. They must not invoke live
Codex workers, use paid generation, inspect live skill roots, or write outside temporary
directories.

## Verification and independent review

The slice is eligible for completion only after:

1. focused F4 and E1 tests pass;
2. existing F1-F3 and controller/benchmark regression suites pass;
3. JSON Schema and Python validation agree for all new contracts;
4. repository package/contract validation passes;
5. the full repository suite passes, with platform skips reported rather than inferred;
6. an independent specification review verifies every F4/E1 requirement and boundary;
7. an independent code-quality/security review reports no unresolved critical or
   important issue.

The live five-pair sequential/parallel benchmark, installed-copy validation, and any
token-overhead policy decision remain separately authorized future work. A Codex restart
is not expected for source/test/documentation changes; installed plugin changes are not
part of this slice.

## Safe stop

Stop after deterministic F4/E1 implementation, evidence, and reviews. Report changed
files, exact commands/results, limitations, and restart status. Do not claim the entire
feature complete until the live benchmark and remaining separately authorized release
gates pass.
