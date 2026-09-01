# Third-Party Notices

## aronprins/codex-loop

- Repository: https://github.com/aronprins/codex-loop
- Pinned commit: `823c4c75dede036278ac6de71b138a3d2a799a64`
- License: MIT
- Copyright: Copyright (c) 2026 Aron Prins

The upstream MIT copyright notice is preserved verbatim in `LICENSE` alongside the
Plugin Compass Contributors notice for this package's original scaffold.

Compass Builder adapts workflow concepts, not copied source text, from these pinned files:

- Upstream `SKILL.md` dependency-wave, isolated-worktree, merge-barrier, and recovery
  concepts are adapted in local `skills/compass-builder/SKILL.md`,
  `skills/compass-builder/references/preflight.md`, `sequential.md`, `parallel.md`, and
  `recovery.md`, plus controller implementations in `compass_builder/state.py` and
  `compass_builder/lease.py`.
- Upstream `subagent-prompt.md` role separation is adapted in local
  `skills/compass-builder/references/prompts/sequential-worker.md`.
- Upstream `parallel-subagent-prompt.md` isolated-worker role separation is adapted in
  local `skills/compass-builder/references/prompts/parallel-worker.md`.

Local changes remove priority-only independence, shared worker writes to controller
state, in-session nested workers, and implicit stale-state recovery. No upstream source
text is reproduced beyond names needed for attribution.
