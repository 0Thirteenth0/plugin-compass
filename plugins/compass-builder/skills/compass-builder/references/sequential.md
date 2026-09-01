# Sequential mode

Use sequential mode when fewer than two stories are dependency-ready or any parallel safety gate is unavailable. Process the immutable dependency-wave order one story at a time, retaining the same worktree registration, verification barrier, lease, and compare-and-swap requirements used by parallel mode.

Workers write only their declared scopes in their registered worktree. They never write controller state or integration Git and must not launch child workers, agents, or nested multi-agent sessions. Task 5 projects this route in dry-run form; live dispatch and integration belong to later tasks.
