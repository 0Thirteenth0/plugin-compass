# Assessment Methodology

Plugin Compass uses only local structured evidence. Plugin and skill descriptions are untrusted input and are never followed as instructions.

## Evidence priority

1. Applicable repository policy and authority documents.
2. `codex plugin list --json` or an explicit inventory fixture.
3. `.codex-plugin/plugin.json` and marketplace metadata.
4. Skill names and `SKILL.md` frontmatter descriptions.
5. Hook, MCP, and app declarations.
6. A supplied DrSkill JSONL report for loadout findings.
7. A supplied exact-target HOL JSON report for security-gate evidence.

Missing evidence stays `unknown`. A source being installed or popular is not trust evidence.

Capability execution-readiness is a separate, static local-file dimension. The bounded
extractor recognizes only explicit plugin-root file references inside a skill and checks
existence without reading or executing those files. Missing or unsafe references exclude
that capability from selection; healthy sibling capabilities remain eligible. Present
files are not dependency, behavior, safety, installed-cache, or alternate-launcher proof.

A live response with both inventory arrays empty is inconclusive and stops all CLI
commands with exit code `3` before producing a plan. The adapter cannot distinguish
a genuinely empty installation from restricted visibility. Recovery uses one explicitly
approved read-only Codex listing, consumed through the existing `--inventory-file`
snapshot boundary. An explicitly supplied empty snapshot remains valid as supplied
data, not proof about a different environment. Other CLI/input errors retain exit `2`.

## Hard gates

The following override relevance and overlap ordering:

- repository prohibition;
- unresolved high or critical findings from an exact-target HOL report;
- incompatible platform declaration;
- competing authority or persistent memory in a governed repository;
- credentials or external mutations without authorization.

A disabled plugin remains available only on demand; disabled state is not evidence of untrustworthiness. Unknown trust plus an executable hook, MCP, or app surface is classified as insufficient evidence until an exact-target HOL report is supplied.

## Relevance and overlap

Relevance is descriptive (`high`, `medium`, `low`, or `unknown`) and comes from deterministic task/repository token matches. Cross-plugin overlap exists only when a supplied DrSkill finding identifies overlapping contributors; Plugin Compass does not run a second fuzzy-duplicate scanner. Winner ordering is hard-gate eligibility, enabled state, relevance, exact match, specialist fit, evidence completeness, lower runtime/context cost, then plugin ID.

The ordering is a task-specific tie-breaker, not a universal product score.

## Conditional exact-skill routing

Invocation routing is separate from plugin classification and minimal-set selection.
The typed `--optimization-goal` option defaults to `speed`; merely mentioning cost,
including "not cost", does not enable the cost optimizer. Explicit `cost` mode may
recommend the exact installed and enabled capability
`claude-code-skills:llm-cost-optimizer`. Codex remains the invoker.

This route does not clear a parent-plugin hard gate, add the parent plugin to the
minimal set, authorize sibling skills, or initiate delegation. Missing pricing or usage
evidence prevents supported savings claims.

## Speed-first effort advice

For relevant tasks in speed mode, `scheduling_guidance` supplies a per-agent rubric
independently of the cost plugin. Its source owner is `plugin_compass/scheduling.py`.
It preserves the selected model, requires supported effort values and task-specific
acceptance checks, and uses complexity, ambiguity, risk, and validation strength to
justify effort. High-risk work is not assigned low effort merely because it is short.

The policy is explicitly advisory. It does not classify actual subagent tasks or enforce
a scheduler's settings. Codex or the invoking scheduler must assess each task and enforce
the checks. After a diagnosed reasoning failure, one higher-effort retry is allowed
within the authorized scope; persistent failure needs review or clarification. Tool or
permission failures do not justify extra reasoning effort. Neither the initial rubric
nor passing a finite test set guarantees correctness or globally optimal latency.

The `handoff` command applies the rubric to one structured task. Caller data must cite
the current native tool or agent contract supporting the effort values. The command
preserves the selected model, emits isolated-context native dispatch arguments only after
authorization, value, and validation gates, and permits one diagnosed reasoning retry.
It performs no discovery, authorization, dispatch, result verification, or persistent
scheduling. Gates exit `4` and contain no dispatch arguments.

## Output interpretation

- **Use now:** directly relevant and not hard-gated.
- **Useful on demand:** related but not required for the supplied task.
- **Redundant:** loses an overlap group to a stronger eligible option.
- **Irrelevant to this project:** no meaningful supplied-task or repository match.
- **Blocked or untrusted:** a hard gate applies.
- **Unknown or insufficient evidence:** identity or trust evidence is inadequate for a safe recommendation.

DrSkill severity remains visible but is review evidence rather than an automatic trust block. Deterministic triage may mark a finding as a suspected false positive, never silently discard it. A clean HOL scan means only that no covered issue was detected for that exact target.
