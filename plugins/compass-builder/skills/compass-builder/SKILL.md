---
name: compass-builder
description: Plan and safely coordinate resumable sequential or isolated parallel Codex build workflows. Use for multi-story repository builds that need dependency waves, durable controller evidence, and fail-closed recovery; do not use for ordinary single-edit requests.
license: MIT
---

# Compass Builder

Compass Builder coordinates dependency-ready stories while Plugin Compass passively advises the lowest adequate supported reasoning effort. The controller alone owns durable state, registered worktrees, the integration branch, leases, and recovery evidence.

Task 6 keeps `run` and `resume` as validated dry-run composition; it does not dispatch live workers. It adds controller-owned receipt verification, lease-serialized integration, independent post-merge validation, and fail-closed cleanup eligibility. Do not claim benchmark results through this version.

`verify-worker --repo --plan --receipt` treats the receipt as a claim, reloads controller-owned launch and state evidence, derives scope and commit shape from isolated raw Git objects, and reruns every required worker check. Integration must acquire the branch lease, freshly repeat that verification, merge only the resulting immutable SHA, prove the merge's ordered raw parents, and durably record validation or blocker evidence.

`cleanup --repo --run-id` is destructive. Invoke it only with explicit user authorization. The controller derives candidates exclusively from its durable verified-merge ledger, acquires the integration lease, checks canonical containment, Git worktree membership, branch/head identity, and cleanliness, then records each removal durably. Unsafe, dirty, raced, foreign, or unregistered worktrees are retained.

## Route

- Read [references/preflight.md](references/preflight.md) before either mode.
- Read [references/sequential.md](references/sequential.md) for sequential mode.
- Read [references/parallel.md](references/parallel.md) only when at least two dependency-ready stories pass every isolation gate.
- Read [references/recovery.md](references/recovery.md) when durable state is blocked or a prior attempt stopped.
- Use [references/prompts/sequential-worker.md](references/prompts/sequential-worker.md) or [references/prompts/parallel-worker.md](references/prompts/parallel-worker.md) only when constructing that mode's bounded worker prompt in a later live-worker task.

Every worker is bound to one registered worktree and branch. Workers never write controller state or integration Git, and must not launch child workers, agents, or nested multi-agent sessions. An explicit parallel preference never bypasses a safety gate.
