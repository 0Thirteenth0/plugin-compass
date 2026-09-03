# Plugin Compass

![Plugin Compass icon](plugins/plugin-compass/assets/plugin-compass-icon.png)

Plugin Compass is an evidence-driven Codex workflow for choosing the smallest useful
plugin and skill set, selecting the lowest adequate reasoning effort, and safely
executing approved repository work.

This marketplace contains two deliberately separate plugins:

| Plugin | Access | Responsibility |
| --- | --- | --- |
| [Plugin Compass](plugins/plugin-compass) | Read-only | Inventories installed capabilities and recommends a minimal, evidence-backed toolset, execution mode, and same-model effort level. |
| [Compass Builder](plugins/compass-builder) | Write | Runs approved stories sequentially or in isolated parallel worker clones, verifies their outputs, and integrates them serially. |

Plugin Compass advises; Codex and Compass Builder execute. This keeps discovery and
trust decisions separate from repository mutation.

## How it works

```text
repository + task
       |
       v
Plugin Compass assessment
       |
       v
mode and effort proposal: auto | sequential | parallel
       |
       v
Compass Builder workers -> independent verification -> serial integration
```

`auto` is passive from the user's perspective. It chooses between sequential and
parallel execution and assigns the lowest reasoning effort that satisfies each story's
complexity, risk, and acceptance checks. Explicit mode requests never bypass safety
gates.

The currently calibrated concurrency ceiling is **two builders**. A higher ceiling must
pass the same performance, quality, and safety gates before it can be enabled.

## Install

Requirements: a Codex CLI with plugin support, Git, and Python 3.11 or newer.

```powershell
codex plugin marketplace add 0Thirteenth0/plugin-compass --ref main
codex plugin add plugin-compass@plugin-compass-local
codex plugin add compass-builder@plugin-compass-local
```

Start a new Codex task after installation so the newly installed skills are discovered.

## Example prompts

Assess a repository without changing it:

> Use Plugin Compass to assess this repository and recommend the smallest
> evidence-backed plugin and skill set for continued development.

Let the workflow choose the fastest safe build mode:

> Use Compass Builder in auto mode for this approved story set. Apply the proposed
> same-model reasoning effort, enforce each write scope and acceptance check, and
> integrate only independently verified results.

Force a safe sequential run when parallel coordination would not help:

> Use Compass Builder sequentially for these approved stories and stop on any failed
> acceptance check or scope violation.

## Measured result

The Task 9 calibration used the same repository fixture, story set, model, effort
policy, and acceptance checks across five alternating measured pairs:

| Metric | Sequential | Parallel |
| --- | ---: | ---: |
| Median time to a green integrated commit | 192,476 ms | 118,459 ms |
| First-pass acceptance | 5/5 | 5/5 |
| Human interventions | 0 | 0 |

That is a **38.46% median wall-clock reduction** for the recorded two-story,
two-builder calibration. All tracked conflict, timeout, stale-head, manual-edit, repair,
and scope-violation metrics were zero. This result graduates only the current fixture
and two-builder policy; it is not a universal speed claim.

See the [benchmark protocol and evidence](docs/COMPASS_BUILDER_BENCHMARK.md) for the
complete graduation rules.

## Safety model

- Plugin Compass never executes discovered plugins or mutates the installation.
- Every Builder worker receives one declared story, write scope, and acceptance set.
- Workers run in remote-free, full-history clones outside the integration repository.
- Workers edit files only; the controller owns Git commits and integration.
- Plugins, hooks, nested agents, and inherited user configuration are disabled in
  worker processes while repository rules remain active.
- Scope drift, stale heads, merge commits, dirty trees, conflicts, failed checks, and
  incomplete evidence fail closed.
- Integration is serial and automatic conflict repair is intentionally unsupported.

Read the [product contract](docs/PRODUCT_CONTRACT.md) and
[Compass Builder contract](docs/COMPASS_BUILDER_CONTRACT.md) for the normative
boundaries.

## Validate

Run the deterministic repository audit:

```powershell
python scripts/check_repo_harness.py --profile audit --format json
```

The audit covers documentation, schemas, unit and integration behavior, harness
self-tests, the repository-wide test suite, and `git diff --check`. Workstation-only
package, installed-copy, live-model, and security gates are documented separately in
[validation](docs/VALIDATION.md).

## Project documentation

- [Technical design](docs/TECHNICAL_DESIGN.md)
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)
- [DrSkill gap analysis](docs/DRSKILL_GAP_ANALYSIS.md)
- [Compass Builder MVP plan](docs/aegis/plans/2026-09-01-compass-builder-mvp.md)
- [Security policy](SECURITY.md)

Licensed under the [MIT License](LICENSE).
