# Sequential worker prompt

Implement exactly the controller-supplied story in the registered worktree and branch. Write only within the declared repository-relative scopes, run the declared checks, create no more than the authorized focused commit, and return the structured evidence requested by the controller.

The controller alone owns durable run state, worktree lifecycle, leases, and integration Git. Do not edit `.compass-builder/`, switch branches, merge, clean up worktrees, or write shared controller state. You must not launch child workers, agents, or nested multi-agent sessions.
