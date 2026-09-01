# Preflight

Validate the immutable run spec and wave plan before controller mutation. Resolve the canonical checkout root, Git common directory, base SHA, integration branch, and current integration HEAD. Stop if identity is ambiguous, any SHA is stale, or `.compass-builder/` is tracked or not ignored.

Use dependency readiness and declared non-overlapping write scopes, not priority alone, to form waves. Register each worker as branch `cb/<run-id>/<story-id>` under `.compass-builder/worktrees/<run-id>/<story-id>`. Reject traversal, repository-root, primary-checkout, reparse-point, or unregistered targets.

The controller alone owns state, leases, integration Git, and lifecycle. Every worker must remain in its one registered worktree and must not launch child workers, agents, or nested multi-agent sessions.
