# DrSkill Gap Analysis and MVP Decision

## Decision

Keep the `plugin-compass` Codex plugin envelope, but replace the proposed standalone
inventory and security engine with a thin evidence-adapter and decision layer.

Plugin Compass will not compete with DrSkill. It will combine authoritative Codex
inventory, optional DrSkill health findings, optional HOL scanner reports, bounded
repository authority, and a supplied task to produce the task-specific conclusions
that those tools do not provide.

## Verified Baseline

The following observations were made locally on 2026-08-28 without using DrSkill
`audit`, `--deep`, or `--mcp-connect`:

- `drskill-core==0.7.2` is installed in an isolated pipx environment and its
  dependency check passes.
- `drskill scan --harness codex --json` emitted 128 findings: 10 errors and 118
  warnings.
- The scan estimated the Codex startup catalog at approximately 42,864 tokens
  against DrSkill's default 6,000-token budget.
- The findings referenced 497 distinct contributor paths. The large
  `claude-code-skills` bundle dominated the finding references.
- The scan found one near-duplicate pair: `control-in-app-browser` and
  `control-chrome`.
- Several elevated findings were contextual false positives. For example, scanner
  source code that mentions credential paths was itself reported as a credential
  read, and path-valued `node_repl` settings were reported as credential-shaped
  values.
- `drskill list` has no JSON mode in version 0.7.2.
- `drskill scan --json` emits a JSON object per finding rather than a single
  inventory document.
- `drskill lint <plugin-root> --json` does not recognize the Codex
  `.codex-plugin/plugin.json` layout. It exited 2 for this plugin root and reported
  that only root `plugin.json`, `.claude-plugin/plugin.json`, skill, marketplace,
  or MCP targets are supported.

These results prove that DrSkill is useful evidence, but not a complete Plugin
Compass implementation or a Codex packaging validator.

## Capability Ownership

| Requirement | Canonical owner | Plugin Compass responsibility |
|---|---|---|
| Installed/enabled plugin identity | `codex plugin list --json` | Normalize and cite it |
| Local plugin and skill metadata | Local manifests and skill frontmatter | Read only declared local roots; preserve provenance |
| Loadout defects and token-budget findings | DrSkill static scan | Parse findings, retain check/severity/evidence, and mark heuristic status |
| Plugin package validation | Plugin Creator validator | Report validation evidence; do not reimplement the validator |
| Focused skill validation | Skill Creator and DrSkill skill lint | Report both results without treating either as a security proof |
| Plugin security gate | HOL `plugin-scanner` | Normalize target-specific results; unresolved high/critical findings block |
| Repository authority | Applicable local authority documents | Read bounded named documents and derive explicit constraints |
| Task and repository relevance | Plugin Compass | Produce transparent descriptive ratings |
| False-positive triage | Plugin Compass plus human evidence | Distinguish observed, heuristic, reviewed, disputed, and unknown findings |
| Overlap winner | Plugin Compass | Select the eligible task-specific winner and explain tie-breakers |
| Minimal capability set | Plugin Compass | Choose only capabilities needed for the supplied task |
| Session adaptation prompt | Plugin Compass | Generate a deterministic ready-to-paste prompt |
| Historical usage | DrSkill `audit`, outside the default MVP path | Require separate authorization; never run implicitly |
| Installation or enable/disable actions | User/Codex package manager | Out of scope |

## What DrSkill Replaces

DrSkill replaces these proposed Plugin Compass responsibilities:

- generalized duplicate and shadow detection;
- generalized skill-spec linting;
- generalized injection-pattern scanning of skill bundles;
- aggregate catalog-token budget detection;
- MCP configuration health checks;
- optional historical usage mining;
- Agent Plugins and Claude Code plugin linting where those layouts apply.

Plugin Compass must delete or retire any code whose only purpose is to reproduce
those checks.

## What Remains Unique

DrSkill does not answer the user's central decision question: "What should this
Codex session use for this repository and task?"

The remaining MVP must:

1. combine plugin identity, capability metadata, repository policy, and tool findings;
2. interpret findings without automatically treating heuristic matches as facts;
3. distinguish availability from trust (for example, disabled is not malicious);
4. rank task relevance and select a winner inside a relevant overlap group;
5. minimize the selected capability set;
6. explain every conclusion with local evidence references;
7. render the same plan as deterministic JSON, Markdown, and a session prompt.

## Architecture Consequences

- Retain the Codex plugin package and one focused skill.
- Retain a small standard-library CLI because deterministic JSON and fixture-based
  tests are explicit product requirements.
- Do not import DrSkill internals. Its supported command output is an external
  evidence format.
- Do not parse `drskill list` tables. Use Codex JSON and local declared metadata for
  inventory; consume DrSkill JSONL only for findings.
- Do not run DrSkill automatically during ordinary inventory. Full assessment may
  consume a supplied report or use an explicit `--collect-drskill` option.
- Restrict DrSkill invocation to `scan --harness codex --json`. The MVP must never
  invoke `audit`, `ack`, `review`, `--deep`, or `--mcp-connect`.
- Replace the prototype's custom unsafe-command and duplicate scanners with adapters
  for DrSkill and HOL evidence.
- Keep shallow manifest and `SKILL.md` frontmatter extraction because neither Codex
  plugin-list JSON nor DrSkill scan JSON provides a stable machine-readable
  capability inventory.

## Finding Interpretation Policy

Tool findings are evidence claims, not final verdicts.

- HOL high/critical findings that match the exact plugin target are blocking until
  resolved or superseded by newer target-specific evidence.
- DrSkill `error` and `warning` findings are review signals. They do not become a
  Plugin Compass trust block solely because of their severity label.
- Manifest parse failure blocks capability claims that depend on that manifest, but
  it is not automatically a security finding.
- Disabled plugins cannot be classified `Use now`, but may be `Useful on demand` if
  otherwise relevant and trusted. Plugin Compass still must not enable them.
- Suspected false positives remain visible with their original evidence and a
  separate triage status. They are never silently discarded.
- Missing or stale evidence remains `unknown`.

## Partial Scaffold Disposition

| Existing area | Decision |
|---|---|
| `.codex-plugin/plugin.json`, marketplace, licenses, security docs | Keep |
| Focused `plugin-compass` skill | Keep, then rewrite against the final CLI |
| Typed evidence and output models | Simplify and keep |
| Direct Codex inventory adapter | Keep in a smaller module |
| Local manifest and skill-frontmatter reader | Keep only bounded parsing |
| Custom injection/unsafe declaration scanner | Retire in favor of DrSkill/HOL evidence |
| Custom fuzzy duplicate engine | Retire; consume DrSkill overlap findings |
| Repository-wide language and validation-file crawl | Reduce to named authority and bounded context evidence |
| Task relevance, overlap winner, minimal set, prompt rendering | Keep and test |
| CLI, schemas, fixtures, and tests | Implemented and fixture-tested |

The pre-decision scanner prototype has been replaced. The remaining Python package is
the implemented adapter and decision layer with CLI, schema, fixtures, and regression
tests.

## Revised Work Order

1. Freeze this ownership decision in the product contract and technical design.
2. Write fixtures for Codex inventory, DrSkill JSONL, HOL reports, repository policy,
   and expected recommendation plans.
3. Replace prototype discovery and scanning with narrow evidence adapters.
4. Implement finding triage, task relevance, winner selection, minimal-set planning,
   deterministic rendering, and the focused skill.
5. Validate with Plugin Creator, Skill Creator, DrSkill skill lint, tests, and HOL
   plugin scanning.

## Stop Conditions

Stop and request direction if completion would require:

- importing unsupported DrSkill internals;
- parsing unstable human-rendered tables;
- reading session history without separate authorization;
- executing or connecting to discovered plugin components;
- modifying the user's enabled plugin set;
- treating a heuristic warning as proven malicious behavior;
- adding networking, persistent memory, orchestration, or a graphical interface.
