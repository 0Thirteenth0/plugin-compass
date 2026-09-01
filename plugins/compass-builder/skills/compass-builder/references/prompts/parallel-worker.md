# Parallel worker prompt

Implement exactly the controller-supplied dependency-ready story in its isolated registered worktree and `cb/<run-id>/<story-id>` branch. Write only within the declared disjoint scopes, run the declared checks, create no more than the authorized focused commit, and return structured evidence without coordinating with sibling workers.

The controller alone owns durable run state, wave barriers, worktree lifecycle, leases, and serial integration Git. Do not edit `.compass-builder/`, switch branches, merge sibling work, clean up worktrees, or write shared controller state. You must not launch child workers, agents, or nested multi-agent sessions.
