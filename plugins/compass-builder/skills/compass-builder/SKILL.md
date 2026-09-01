---
name: compass-builder
description: Plan and safely coordinate resumable sequential or isolated parallel Codex build workflows. Use for multi-story repository builds that need dependency waves, durable controller evidence, and fail-closed recovery; do not use for ordinary single-edit requests.
license: MIT
---

# Compass Builder

Compass Builder coordinates dependency-ready stories while Plugin Compass passively advises the lowest adequate supported reasoning effort. The controller alone owns durable state, registered worktrees, the integration branch, leases, and recovery evidence.

Task 5 supports validated dry-run `run` and `resume` composition only. Do not start a live worker, merge, clean up, or claim benchmark results through this version.

## Route

- Read [references/preflight.md](references/preflight.md) before either mode.
- Read [references/sequential.md](references/sequential.md) for sequential mode.
- Read [references/parallel.md](references/parallel.md) only when at least two dependency-ready stories pass every isolation gate.
- Read [references/recovery.md](references/recovery.md) when durable state is blocked or a prior attempt stopped.
- Use [references/prompts/sequential-worker.md](references/prompts/sequential-worker.md) or [references/prompts/parallel-worker.md](references/prompts/parallel-worker.md) only when constructing that mode's bounded worker prompt in a later live-worker task.

Every worker is bound to one registered worktree and branch. Workers never write controller state or integration Git, and must not launch child workers, agents, or nested multi-agent sessions. An explicit parallel preference never bypasses a safety gate.
