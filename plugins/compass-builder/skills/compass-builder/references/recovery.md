# Recovery

Load only the controller-owned `.compass-builder/runs/<run-id>/state.json`. Validate the canonical repository identity, immutable run and plan binding digest, registered branches and worktree paths, append-only blocker history, complete recorded SHA chain, active lease, and current integration HEAD before proposing recovery.

Resume only the active blocker's recorded `resumeState`. Clear `activeBlocker`, retain its exact history entry, and continue with the first branch not marked `integration-verified`. A post-merge-check blocker resumes verification from its retained merge SHA; never re-merge it. A verified final branch opens the next immutable dependency wave only when one remains.

Malformed or stale state, lease contention, stale HEAD, unsafe paths, or an invalid transition stop the run. Stale leases are not stolen in v1. Workers never repair controller state and must not launch child workers, agents, or nested multi-agent sessions.
