# Compass Builder initial baseline

## Facts

- Plugin Compass is implemented, installed, read-only, and explicitly excludes
  orchestration and persistent scheduler state.
- Plugin Compass already emits speed-first scheduling guidance and proposal-only native
  handoffs for one task.
- Native Codex exposes subagent dispatch and Git provides worktree isolation.
- The current in-session `collaboration.spawn_agent` schema exposes no worktree or
  working-directory field, so its workers cannot be treated as isolated builders.
- The installed Codex CLI exposes top-level `codex exec -C <DIR>`, exact model selection,
  config overrides, structured output, JSON events, workspace-write sandboxing, and a
  stable `multi_agent` feature that can be disabled.
- The repository has a clean `main` branch backed by the private GitHub repository
  `0Thirteenth0/plugin-compass`.
- The current Python suite contains 52 passing unit/CLI tests.
- `aronprins/codex-loop` is MIT licensed and its `main` branch resolved to
  `823c4c75dede036278ac6de71b138a3d2a799a64` during planning.
- Upstream Codex Loop provides sequential and dependency-wave skill contracts, worker
  prompts, isolated worktrees, serial merge barriers, and resumable PRD state.

## Assumptions to validate

- A separate companion plugin can reuse the existing repo-local marketplace without
  changing Plugin Compass's authority.
- A bounded standard-library controller around top-level `codex exec -C` can provide
  deterministic isolation without a daemon.
- Declared repository-relative write scopes are practical for the first target workloads.
- Exact-model top-level dispatch plus Plugin Compass handoff proposals can preserve the
  selected model and apply an effort level supported by verified host evidence.

## Unknowns

- The real speedup distribution across representative project types.
- The safe concurrency ceiling above two builders on this host.
- How often apparently disjoint scopes still conflict through generated or shared files.
- Whether future Codex versions expose stronger native scheduling receipts that retire
  parts of the helper.

## Baseline commands

```powershell
git -c "safe.directory=C:/Users/jiahu/Desktop/Plugin Compass" status --short --branch
python -m unittest discover -s tests -v
codex plugin list --json
```
