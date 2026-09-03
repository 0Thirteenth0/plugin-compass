# Compass Builder Task 9 intent

## Requested outcome

Complete the approved Compass Builder MVP release slice: repair release metadata, run
the source/package/security gates, produce a minimal model-backed paired benchmark, and
conditionally validate the installed copy without weakening any safety or comparison
gate.

## Slice Card

- Goal: produce truthful source, package, exact-target security, live benchmark, and
  installed-copy evidence for the current Compass Builder implementation.
- Parent plan/spec: `docs/aegis/plans/2026-09-01-compass-builder-mvp.md`, Task 9;
  `docs/COMPASS_BUILDER_CONTRACT.md`; and `docs/VALIDATION.md`.
- Files: package and skill metadata, benchmark documentation, one canonical launcher-
  prompt repair and focused test if verified necessary, Task 9 lifecycle records, and
  ignored controller-owned benchmark inputs/receipts.
- Boundary: two-builder ceiling; one representative two-story standard-library fixture;
  lowest adequate supported effort on the fixed model; five measured pairs plus required
  warmups; no favorable-result filtering, automatic conflict repair, unbounded workers,
  Plugin Compass orchestration, marketplace hand editing, or unrelated plugin mutation.
- Verification: Plugin Creator, Skill Creator, repository focused/full/integration gates,
  public live-run smoke, public paired benchmark/comparator, HOL exact-target lint/verify/
  scan, supported Plugin Creator reinstall flow, and installed-copy doctor/sequential/
  parallel smoke checks.
- Stop: any failed source/package/security gate, unresolved high/critical exact-target
  finding, incomparable benchmark control, live worker permission/input request, repeated
  systemic live-route failure, installed-source mismatch, or required scope expansion.

## BaselineReadSetHint and usage

- Required and acknowledged: `docs/PRODUCT_CONTRACT.md`, `docs/TECHNICAL_DESIGN.md`,
  `docs/COMPASS_BUILDER_CONTRACT.md`, `SECURITY.md`, the active MVP plan and initial
  Builder baseline, Task 8 intent/checkpoint/evidence/reflection, Plugin Creator and Skill
  Creator instructions, and the repository validation/benchmark guides.
- Cited by the parent plan: all required project sources above.
- Active `AGENTS.md` or `CONTEXT.md`: none present in the repository.
- Agent Harness read-only assessment: the repository already has canonical contracts,
  one validation harness, CI, lifecycle records, benchmark receipts, and Git delivery;
  Task 9 needs release evidence rather than another harness layer.
- Missing helper reference: the installed Agent Harness package references
  `../../references/harness-patterns.md`, but that file is absent. Its complete assessment
  procedure and repository authority remain sufficient for this bounded mapping.

## ImpactStatementDraft

This slice converts the verified source implementation into a truthfully described and
validated release candidate, then measures the public controller route without allowing
synthetic or selectively retained evidence to become a performance claim.

## Live execution amendment

The initial prompt-only change proved insufficient during authorized live execution.
`--approve-for-me` workers could edit files, but linked worktrees allowed the managed
approval environment and integration checkout to share repository state. Explicit
`workspace-write` with approval disabled was forced read-only by the host. The smallest
verified correction is therefore controller-owned Git mutation plus remote-free,
full-history worker clones in a deterministic temporary root outside the integration
repository. A direct external-clone diagnostic showed zero primary-checkout mutations,
and the production parallel smoke and paired benchmark then completed green. This is a
Task 9 safety-boundary correction, not a new service, scheduler, retry policy, or broader
product scope.

## Execution Readiness View

- Intent lock: fastest verified completion, not minimum cost or a favorable benchmark.
- Scope fence: Task 9 package/skill metadata, one proven prompt-contract repair, release
  gates, one live calibration workload, supported reinstall, and installed-copy evidence.
- Baseline lock: clean synchronized `main` at
  `fd708d651e92c70a57f554500348774f9ee6090d`; Task 8 implementation commit `9733530`
  passed the complete hosted Windows Python 3.11 workflow.
- Owner constraints: `launcher.py` alone owns the worker prompt; Plugin Creator owns
  package validation/update; Skill Creator owns skill conformance; HOL owns the exact-
  target security gate; the existing controller/benchmark/comparator owners remain
  unchanged.
- Compatibility boundary: Windows, Python 3.11+, full-history Git clones, current native Codex CLI,
  standard-library repository and fixture, paths with spaces, no shell interpolation.
- Retirement boundary: live evidence may calibrate the two-builder ceiling but does not
  authorize unbounded concurrency; native contract-equivalent scheduling/receipts remain
  the controller retirement trigger.
- Task batches: prompt/metadata truth; source validators and HOL; fixture/host/plan
  preflight; one public live smoke; paired benchmark and comparator; conditional install
  and installed-copy smoke; closeout.
- Test obligations: launcher prompt invariant, package/skill validators, focused and full
  suites, integration, exact-target HOL result, every benchmark attempt/ledger receipt,
  installed-copy parity, diff hygiene, and hosted validation after Git closeout.
- Review gates: first-principles source inspection before model use; fail closed at each
  release gate; fresh complete verification before a release-readiness claim.
- Drift/rewind: return to plan review if a new executor, retry policy, shared writer,
  compatibility fallback, external service, or broader fixture portfolio is required.
- Advisory boundary: passing one representative workload supports only the documented
  two-builder calibration decision; it does not prove universal superiority.

## Authorization and TDD route

- Authorization: after Task 8 closeout explicitly identified Task 9 as live benchmark,
  security/package validation, and installed-copy testing, the user replied `proceed`.
  This authorizes the bounded Task 9 operations above, including minimal model use and
  the supported local reinstall flow; it does not authorize unrelated plugin changes or
  a concurrency increase without passing receipts.
- TDD Mode: off
- Decision: skipped
- Strict authority: not applicable
- Test posture: add the minimum focused regression for the proven prompt-contract gap,
  then run the complete post-change release matrix.

## Change necessity and complexity

Change Necessity:

- User-visible need: live workers must produce scoped edits while the controller creates
  the exactly one commit that the verifier requires, and the installed plugin must
  describe implemented rather than future behavior.
- No-change / non-code option: running the 24-call minimum benchmark with an incomplete
  worker prompt risks systematic false failure and wasted model usage; leaving scaffold
  metadata would make packaging misleading.
- Why code change is necessary: prompt-only Git ownership was not enforceable on the live
  host; controller-owned commits and a repository-independent worker checkout boundary
  are required to keep integration clean.
- Minimum change boundary: worker no-Git prompt, controller-owned commit and exact ref
  import, external clone creation, verifier/cleanup support, focused regression tests,
  and metadata/documentation corrections; no new daemon or external service.
- Decision: code-change.

Complexity Budget:

- Artifact class: controller/checkout safety correction, maintained tests, JSON/YAML/
  Markdown metadata, ignored benchmark evidence, and lifecycle records.
- Target files: existing controller, state, verifier, cleanup, benchmark-runner, launcher,
  focused tests, plugin/skill metadata, benchmark documentation, and Task 9 work records.
- Current pressure: responsibilities remain with their existing canonical owners. No new
  maintained runtime module is needed.
- Projected post-change pressure: within-budget.
- Planned governance: keep benchmark input generation outside maintained runtime; do not
  add another executor, scanner, package manager, or evidence owner.

Pre-Edit Owner-Fit Decision:

- Edit intent: safety-boundary correction plus metadata truth correction.
- Owner fit: launcher instructions, controller Git ownership, state path registration,
  verifier trust, cleanup, and benchmark lifecycle stay in their existing owners.
- Safer edit boundary: workers edit files only; the controller validates scope before
  staging, creates one deterministic commit, imports one exact ref, and never repairs the
  integration checkout.
- Decision: edit-in-place.

## Worktree necessity

No task-owned development worktree is created. The source checkout has one writer. Live
workers and benchmark arms use registered disposable clones outside the integration
repository; verified clones are removed through the durable cleanup ledger, while failed
clones are retained for inspection.
