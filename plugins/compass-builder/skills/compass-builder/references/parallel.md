# Parallel mode

Parallel mode requires at least two dependency-ready stories with pairwise-disjoint declared write scopes, decisive actionable validation, no shared-state mutation, isolated registered worktrees, and proven worker working-directory plus nested-agent disable support. Priority never establishes independence.

Workers may commit only on their assigned `cb/<run-id>/<story-id>` branch. They never write shared controller state or integration Git and must not launch child workers, agents, or nested multi-agent sessions. The controller waits at a wave barrier; serial verification and integration are later-task responsibilities.

If any hard gate fails, stop or use sequential mode only when the immutable spec permits it. Never reinterpret an explicit parallel preference as authorization to weaken a gate.
