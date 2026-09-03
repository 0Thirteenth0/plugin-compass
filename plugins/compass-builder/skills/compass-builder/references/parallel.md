# Parallel mode

Parallel mode requires at least two dependency-ready stories with pairwise-disjoint declared write scopes, decisive actionable validation, no shared-state mutation, isolated registered remote-free clones, and proven worker working-directory plus nested-agent disable support. Priority never establishes independence.

Workers edit only their declared scopes and leave Git untouched. They never write shared controller state or the integration repository and must not launch child workers, agents, or nested multi-agent sessions. After the wave barrier, the controller validates scope, creates one commit per story, imports the exact branch SHAs, independently verifies the complete wave, and integrates verified branches one at a time in immutable plan order.

If any hard gate fails, stop or use sequential mode only when the immutable spec permits it. Never reinterpret an explicit parallel preference as authorization to weaken a gate.
