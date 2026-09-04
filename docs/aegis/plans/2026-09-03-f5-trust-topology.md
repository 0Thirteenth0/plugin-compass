# F5 trust baseline and execution-topology plan

Status: proposed. Planning only. No F5 implementation, installation, scan, commit, push,
or publication is authorized by this record.

## Scope and reconciliation

F5 is independent of the active G1 rolling-contract slice. The current G1 worktree is
preserved exactly as found: `models.py`, `_rolling_models.py`, six rolling schemas,
rolling fixtures, and `test_builder_rolling_models.py` are the only uncommitted product
changes. F5 must not edit, absorb, or reinterpret them.

The repository currently establishes these authorities:

- Codex owns authoritative installed-plugin identity through
  `codex plugin list --json`.
- Plugin Compass owns read-only inventory, evidence normalization, recommendation, and
  proposal-only per-task handoff.
- Configured standalone skill roots are bounded, source-neutral, and read-only.
- DrSkill contributes quality, compatibility, overlap, and injection-surface evidence;
  it does not establish security trust.
- HOL contributes exact-target plugin security evidence.
- Compass Builder is the only planner, scheduler, durable-state owner, worker launcher,
  verifier, importer, integrator, and recovery authority.

F5 adds one explicitly invoked, bounded state-writing operation and one pure advisory
contract. Ordinary `inventory`, `assess`, `recommend`, and `prompt` operations remain
read-only. F5A and F5B are separate approval and implementation lanes and must never be
combined into one atomic change.

## Confirmed gaps and reusable components

### Confirmed gaps

1. Existing DrSkill and HOL adapters normalize supplied or freshly collected evidence,
   but reports are not bound to immutable artifact bytes, manifest digest, policy
   version, and authoritative inventory identity.
2. There is no content-addressed evidence store, deterministic trust index, freshness
   evaluator, or full/pending/target scan selector.
3. Plugin Compass's public trust projection cannot distinguish current evidence from
   stale, incomplete, or absent evidence without changing schema meaning.
4. Existing scheduling guidance is advisory and optimizes fastest verified completion,
   but there is no closed, versioned commander/worker topology contract.
5. Existing `plugin-compass.handoff.v1` proposes one native subagent dispatch. It is not
   a multi-story Builder run contract and must remain unchanged.
6. Compass Builder already selects sequential or parallel execution and G1 introduces
   immutable rolling v2 contracts. F5B must not become a second scheduler.

### Reusable components

- `adapters/codex.py`: authoritative plugin inventory and roots.
- `adapters/standalone.py`: configured-root identity, bounded traversal, strict
  frontmatter, no-follow reads, path/reparse defenses, and degraded-root reporting.
- `skill_models.py`: deterministic source-neutral skill identities and provenance.
- `readiness.py`: bounded readiness evidence.
- `adapters/drskill.py` and `adapters/hol.py`: scanner-output normalization foundations.
- `scheduling.py`: lowest-adequate supported-effort policy and retry/escalation rubric.
- `handoff.py`: strict proposal-only authorization separation.
- Compass Builder `planner.py`, `handoff.py`, and G1 v2 contracts: execution-side gates,
  immutable binding, and scheduler ownership.

The large standalone adapter must not become the trust store or scanner orchestrator.
Reusable path primitives may be extracted only behind regression tests that preserve F4
behavior byte-for-byte.

## Architecture and ownership

| Contract or state | Canonical owner | Consumer | Authority |
| --- | --- | --- | --- |
| Installed plugin inventory | Codex CLI | Plugin Compass | Read-only source of truth |
| Standalone roots | Explicit caller configuration | Plugin Compass | Read-only bounded roots |
| Artifact fingerprint v1 | Plugin Compass F5A | Trust selector/store/readers | Pure, deterministic |
| Trust evidence v1 | Plugin Compass F5A | Trust index and recommendations | Immutable static evidence only |
| Trust index v1 | Plugin Compass F5A | Ordinary Plugin Compass commands | F5A writer; all ordinary readers read-only |
| Execution topology v1 | Plugin Compass F5B | Codex and Compass Builder handoff | Advice only |
| Authorization context | User/Codex/repository policy | Handoff boundary | Never granted by JSON itself |
| Run spec, plan, state, dispatch | Compass Builder | Builder scheduler/controller | Sole execution authority |

## F5A design

### Explicit command surface

Add an explicitly invoked `plugin-compass:trust-baseline` skill with implicit invocation
disabled. It calls a dedicated trust-baseline entry point rather than adding writes to
ordinary Plugin Compass CLI paths:

- `--scope pending` is the default and selects only never-assessed, changed, stale,
  mismatched, incomplete, newly installed, or upgraded artifacts.
- `--scope all` forcibly refreshes every authoritatively discovered artifact.
- `--target <qualified-identity>` accepts exactly one unambiguous plugin or skill.

Discovery and fingerprinting never execute plugin or skill content. Scanner processes
use exact argv arrays, `shell=False`, closed stdin, bounded output and timeout, a fixed
working directory, and an explicit allowlist. Remediation commands remain inert data.

### Artifact identity and fingerprint

Use distinct plugin and skill artifact kinds. A reusable fingerprint binds source type,
qualified identity, authoritative plugin ID and marketplace, plugin version, canonical
root, relative capability path, manifest digest, deterministic content digest, and the
inventory snapshot digest.

The content digest is the SHA-256 of a canonical, portable, sorted list of regular-file
records `(relative path, size, file digest)`. A plugin fingerprint covers the bounded
installed package surface. A skill fingerprint covers its bounded skill directory,
including `SKILL.md` and referenced scripts/assets/resources within the artifact root.
Symlinks, junctions/reparse points, special files, path escapes, unstable file identity,
limit overruns, or mid-scan changes make the result incomplete and non-reusable.

Fingerprint before and after each scanner run. A changed second fingerprint invalidates
the scan instead of binding evidence to unstable bytes.

### Evidence states

Do not reinterpret the existing `trust_status` enum. Introduce a versioned projection:

- `trust_evidence_status`: `reviewed`, `not_assessed`, `stale`, `incomplete`, or `blocked`.
- `freshness_status`: `current`, `missing`, `stale`, or `incomplete`.
- `security_eligibility`: `eligible`, `ineligible`, or `not_required`.

This separates a security verdict from evidence freshness. Only fresh, exact-target,
policy-complete evidence may project to the existing eligible/trusted behavior.
Executable plugin surfaces with absent, stale, mismatched, or incomplete required
security coverage remain ineligible. Ordinary skills may remain `not_assessed` when the
policy permits. DrSkill alone never upgrades security eligibility.

### Immutable store

Use one configured Plugin Compass data root outside repositories, plugin roots,
standalone roots, and plugin caches:

```text
<plugin-data>/trust/
  reports/sha256/<report-digest>.json
  indexes/sha256/<index-digest>.json
  current-index.json
```

Reports and index snapshots are canonical JSON and content-addressed. Existing report
bytes are never rewritten. `current-index.json` is replaced atomically only after every
new report and index snapshot is durably written and re-read successfully. Temporary
files use exclusive creation in the same directory; interruption retains the previous
valid pointer. Corruption, missing content, and partial scans are explicit. Store only
bounded normalized findings, never secrets or unrestricted raw scanner output.

Retention is append-only for F5: no scan or ordinary command deletes historical evidence.
Configured byte/report quotas fail closed before writing a partial report or advancing the
index. Garbage collection is a separately designed and authorized future maintenance
operation; until then, an operator may archive the entire data root but Plugin Compass
does not silently prune it.

### Scanner policy

- Plugin Creator: packaging and structural evidence.
- DrSkill: quality, compatibility, duplication, overlap, and instruction-surface
  evidence; never sufficient for security trust.
- HOL or a separately approved exact-target static scanner: required for hooks, MCP
  servers, apps, commands, process launchers, or other mutation-capable plugin surfaces.
- Standalone skills: inert metadata/readiness/reference/instruction-risk inspection.

Every report binds scanner name/version, scanner executable identity where available,
policy version, evidence-schema version, timestamp, normalized result, limitations, and
bounded findings. Missing tools or unknown coverage produce `incomplete` or unknown
coverage, never a clean result.

## F5B design

### Pure topology contract

`plugin-compass.execution-topology.v1` consumes a validated task/decomposition request,
dependency and write-scope evidence, current host model/effort capabilities, validation
strength, and caller-reported authorization requirements. It deterministically emits:

- `keep-local`, only for genuinely small work with a recorded overhead justification;
- `sequential-builder`, meaning exactly one delegated builder at a time for substantial
  work; or
- `parallel-builders`, only when at least two dependency-ready scopes pass ownership,
  isolation, authorization, and safety gates.

The contract contains no dispatch operation. It includes commander responsibilities,
builder ownership, dependency/isolation evidence, distinct review charters, runtime-wave
behavior, per-role model/effort recommendations, acceptance checks, retry limits,
escalation conditions, rationale, evidence references, required authorization, and
dispatch-withheld reasons.

There is no product-level reviewer ceiling and no `reviewer_limit` field. A technical
payload-size bound may protect parsing, but it cannot be used as policy or inherit the
builder concurrency ceiling. Review assignments are distinct by evidence responsibility;
runtime capacity merely divides them into successive waves.

Model choice is inherited unless an exact supported model is supplied. Effort selection
uses the lowest adequate value actually exposed by the host. Unsupported or stale values
fail closed; there is no effort translation or model substitution. The objective is
fastest verified completion. `llm-cost-optimizer` is used only when the user explicitly
chooses cost optimization.

### Builder handoff

Keep `plugin-compass.handoff.v1` unchanged. Add a separate
`plugin-compass.compass-builder-handoff.v1` proposal that binds the topology digest,
repository identity, immutable task/decomposition digest, host-capability digest,
authorization requirements, and supported Builder contract version.

The topology document cannot grant authority. Before constructing Builder-consumable
arguments, Codex supplies a trusted in-process authorization context and current native
tool/model/effort evidence. Missing, stale, ambiguous, unsupported, or conflicting data
withholds arguments.

Compass Builder validates the envelope before mapping recognized fields into its own
run-spec. It remains the sole owner of scheduling, durable state, launch, verification,
serial integration, and recovery. `keep-local` creates no Builder run. G2 consumes only
validated Builder `run-spec.v2` and `pipeline-plan.v2`, not the advisory topology.

## Safe implementation slices

Every slice requires separate approval and strict RED/GREEN execution. Tests use only
fixtures and temporary directories.

### F5A1 — Artifact identity, fingerprint, and freshness contracts

Proposed files:

- Create `plugins/plugin-compass/plugin_compass/artifact_models.py`.
- Create `plugins/plugin-compass/plugin_compass/artifact_fingerprint.py`.
- Create `plugins/plugin-compass/schemas/artifact-fingerprint.schema.json`.
- Create `plugins/plugin-compass/schemas/trust-evidence.schema.json`.
- Create `tests/test_trust_fingerprints.py`.
- Create `tests/fixtures/plugin_compass/trust/artifacts/` fixture tree.

RED/GREEN: first add failures for identity, root/version/manifest/content invalidation,
duplicate names, spaces, deterministic ordering, bounds, symlink/reparse/path escape,
unstable reads, and no execution/write; then implement the smallest pure contract and
fingerprinter.

Validation:

```powershell
python -m unittest tests.test_trust_fingerprints -v
python -m unittest tests.test_source_neutral_skill_models tests.test_standalone_skill_discovery tests.test_adapters -v
```

Compatibility: no public command/schema change. Safe stop: fingerprints only; no store,
scanner, or CLI. Residual limitation: no reusable evidence. Estimate: 3–5 hours.

### F5A2 — Immutable evidence store and trust index

Proposed files:

- Create `plugins/plugin-compass/plugin_compass/trust_store.py`.
- Create `plugins/plugin-compass/plugin_compass/trust_reader.py`.
- Create `plugins/plugin-compass/schemas/trust-index.schema.json`.
- Create `tests/test_trust_store.py`.
- Create `tests/fixtures/plugin_compass/trust/store/`.

RED/GREEN: immutable address checks, canonical ordering, atomic pointer replacement,
interruption recovery, corruption/missing-report handling, store/path/reparse bounds,
secret/raw-output rejection, and proof that test writes remain inside temporary data.

Validation:

```powershell
python -m unittest tests.test_trust_store tests.test_trust_fingerprints -v
python -m unittest tests.test_cli tests.test_decision tests.test_skill_decision -v
```

Compatibility: no automatic loading and no legacy migration. Safe stop: an internal
store exercised only in temporary tests. Residual limitation: no scan selection or
scanner evidence. Estimate: 3–5 hours.

### F5A3 — Full, pending, and targeted scan selection

Proposed files:

- Create `plugins/plugin-compass/plugin_compass/trust_selection.py`.
- Create `plugins/plugin-compass/plugin_compass/trust_scan_plan.py`.
- Create `plugins/plugin-compass/schemas/trust-scan-plan.schema.json`.
- Create `tests/test_trust_selection.py`.
- Create `tests/fixtures/plugin_compass/trust/selection/`.
- Modify `adapters/codex.py` only if a pure authoritative-inventory digest projection is
  required.

RED/GREEN: all/pending/target behavior; never-assessed/install/upgrade/rebuild/moved
root/manifest/content changes; unchanged skip; ambiguous target rejection; missing roots;
duplicate skills; deterministic order; replayed target rejection.

Validation:

```powershell
python -m unittest tests.test_trust_selection tests.test_trust_store tests.test_trust_fingerprints -v
python -m unittest tests.test_adapters tests.test_standalone_skill_discovery tests.test_standalone_skill_ordering -v
```

Compatibility: authoritative discovery rules are unchanged. Safe stop: deterministic
scan plans only, no external scanners. Residual limitation: plans cannot establish trust.
Estimate: 2–4 hours.

### F5A4 — Scanner adapters, hardening, and recommendation integration

Proposed files:

- Create `plugins/plugin-compass/plugin_compass/scanner_policy.py`.
- Create `plugins/plugin-compass/plugin_compass/adapters/plugin_creator.py`.
- Create `plugins/plugin-compass/plugin_compass/standalone_risk.py`.
- Create `plugins/plugin-compass/plugin_compass/trust_baseline.py`.
- Create `plugins/plugin-compass/scripts/plugin_compass_trust.py`.
- Create `plugins/plugin-compass/skills/trust-baseline/SKILL.md`.
- Create `plugins/plugin-compass/skills/trust-baseline/agents/openai.yaml` with
  `policy.allow_implicit_invocation: false`.
- Create `tests/test_trust_baseline.py` and
  `tests/test_trust_recommendation_integration.py`.
- Create `tests/fixtures/plugin_compass/trust/scanners/`.
- Modify `adapters/drskill.py`, `adapters/hol.py`, `models.py`, `skill_models.py`,
  `decision.py`, `skill_decision.py`, `rendering.py`, and `cli.py` only for exact-bound
  evidence and read-only trust-index projection.
- Create `plugins/plugin-compass/schemas/inventory.v4.schema.json`,
  `plugins/plugin-compass/schemas/recommendation-plan.v6.schema.json`, and
  `plugins/plugin-compass/schemas/prompt.v4.schema.json` without overwriting old schemas.
- Update `.codex-plugin/plugin.json` and trust-baseline skill metadata to disclose the
  explicit scanner/process/data-write surface truthfully.

RED/GREEN covers every required F5A adversarial, scanner-semantics, recommendation,
read-only ordinary-command, and no-content-execution case. Fake scanner executables and
fixture inventories replace live installations.

Validation:

```powershell
python -m unittest tests.test_trust_baseline tests.test_trust_recommendation_integration -v
python -m unittest tests.test_adapters tests.test_cli tests.test_decision tests.test_skill_decision tests.test_f3_cli tests.test_f4_release_closure -v
```

Compatibility: legacy report flags remain ephemeral; they are never silently promoted
to reusable reviewed evidence. New public versions are additive. Safe stop: explicit
trust baseline available, ordinary commands still read-only, no installed-copy change.
Residual limitation: static evidence is not a guarantee or cryptographic attestation.
Estimate: 4–7 hours.

### F5B1 — Execution-topology schema and deterministic recommendation

Proposed files:

- Create `plugins/plugin-compass/plugin_compass/topology_models.py`.
- Create `plugins/plugin-compass/plugin_compass/execution_topology.py`.
- Create `plugins/plugin-compass/schemas/topology-request.schema.json`.
- Create `plugins/plugin-compass/schemas/execution-topology.schema.json`.
- Create `tests/test_execution_topology.py`.
- Create `tests/fixtures/plugin_compass/topology/`.

RED/GREEN: closed schema/Python parity, deterministic bytes, keep-local evidence,
supported model/effort validation, withheld authorization, and no tool invocation.

Validation:

```powershell
python -m unittest tests.test_execution_topology tests.test_handoff -v
python -m unittest tests.test_builder_handoff tests.test_builder_planner -v
```

Compatibility: `handoff.v1` and Builder v1/v2 contracts remain unchanged. Safe stop:
pure topology advice only. Residual limitation: no Builder consumption. Estimate: 3–5
hours.

### F5B2 — Commander, builder, and reviewer semantics

Proposed files:

- Modify `topology_models.py` and `execution_topology.py`.
- Create `tests/test_execution_topology_roles.py`.
- Extend `tests/fixtures/plugin_compass/topology/`.

RED/GREEN: substantial work cannot keep local; sequential means one delegated builder;
parallel requires two isolated ready scopes and cannot bypass gates; builder ceilings do
not cap reviewers; review charters are unique; limited runtime produces waves; every role
has rationale/checks/escalation/retry; no cost routing unless explicitly requested.

Validation:

```powershell
python -m unittest tests.test_execution_topology tests.test_execution_topology_roles -v
python -m unittest tests.test_builder_planner tests.test_builder_models tests.test_builder_rolling_models -v
```

Compatibility: advisory output only. Safe stop: complete topology semantics with no
handoff or execution. Residual limitation: caller evidence is not independently observed.
Estimate: 3–5 hours.

### F5B3 — Authorized Compass Builder handoff

Proposed files:

- Create `plugins/plugin-compass/plugin_compass/builder_handoff.py`.
- Create `plugins/plugin-compass/schemas/compass-builder-handoff.schema.json`.
- Create `tests/test_builder_topology_handoff.py`.
- Create `plugins/compass-builder/compass_builder/topology_input.py`.
- Create `plugins/compass-builder/schemas/topology-handoff.schema.json` as a pinned
  compatibility carrier whose expected canonical digest is tested against the producer.
- Create `tests/test_builder_topology_input.py`.
- Modify `plugins/compass-builder/compass_builder/cli.py` and
  `plugins/compass-builder/compass_builder/planner.py` to wire the input validator only
  after G2's input boundary is fixed; do not modify the G1 contract files.

RED/GREEN: exact topology/repository/host/schema/digest binding; stale/ambiguous/unknown
field/model/effort failures; trusted authorization context; no self-authorizing JSON;
recognized-field-only mapping; keep-local produces no run; Plugin Compass never launches;
Builder never consumes invalid topology.

Validation:

```powershell
python -m unittest tests.test_builder_topology_handoff tests.test_builder_topology_input -v
python -m unittest tests.test_handoff tests.test_builder_handoff tests.test_builder_planner tests.test_builder_rolling_models -v
```

Compatibility: additive envelope; `handoff.v1`, Builder v1, and G1 v2 bytes stay frozen.
Safe stop: validated authorized proposal and Builder input projection, with no live run.
Residual limitation: the Codex authorization provider/API must be available to perform an
actual handoff. Estimate: 4–7 hours.

### F5 closure — Compatibility, documentation, validation, installed copy

Proposed files:

- Modify `feature_planning.md`, `docs/PRODUCT_CONTRACT.md`,
  `docs/TECHNICAL_DESIGN.md`, `docs/VALIDATION.md`,
  `docs/COMPASS_BUILDER_CONTRACT.md`, `plugins/plugin-compass/README.md`,
  `plugins/plugin-compass/SECURITY.md`, `plugins/compass-builder/README.md`, and
  `plugins/compass-builder/SECURITY.md`.
- Create `tests/test_f5_release_closure.py` without weakening or modifying F4 evidence.
- Update `plugins/plugin-compass/.codex-plugin/plugin.json` package metadata/cache-buster
  only after all code and documentation are green.

RED/GREEN: packaging, schema registry/closure, metadata, source-vs-installed-copy parity,
compatibility fixtures, and no unapproved write path. Installed-copy verification and any
restart require separate installation authorization.

Validation:

```powershell
python -m unittest tests.test_f5_release_closure -v
python -m unittest discover -s tests -v
```

Then run Plugin Creator and Skill Creator validation, DrSkill quality scanning, and the
approved exact-target security scanner. Safe stop: source tree validated; installation,
commit, push, and publication still withheld. Residual limitation: installed-copy parity
cannot be claimed before an authorized install/restart. Estimate: 3–5 hours.

## Compatibility and migration

1. Preserve existing public schema files and `handoff.v1` exactly.
2. Add new trust/topology schemas as closed v1 contracts.
3. Version inventory/recommendation/prompt outputs only when trust-index fields are
   integrated; keep existing fields and meanings stable.
4. Preserve the legacy `trust_status` projection and add separate evidence/freshness
   fields. Never reinterpret an old `trusted` or `not_assessed` value silently.
5. Existing saved DrSkill/HOL reports remain accepted by legacy explicit flags, but do
   not enter the reusable trust index until an explicit baseline run produces a fully
   bound report.
6. No migration writes occur during ordinary Plugin Compass commands.
7. Old trust indexes remain immutable; a policy/schema/scanner upgrade creates new
   reports and an index snapshot, marking superseded evidence stale.

## Test strategy

The fixture matrix has four independent axes: source (`plugin`, packaged skill,
standalone-user, standalone-project, system), mutation (unchanged, content, manifest,
version, root, disappearance), scanner outcome (clean, findings, unavailable, malformed,
oversized, wrong target), and topology (local, sequential, safe parallel, gated parallel,
review waves). Pairwise fixtures cover ordinary cases; dedicated adversarial fixtures
cover every path, symlink/reparse, ambiguity, replay, interruption, authorization, and
unknown model/effort requirement in the request.

Contract tests validate every fixture through both JSON Schema and Python and compare
canonical bytes. Behavioral tests instrument process launch and file writes so a test
fails if discovery executes artifact content, a scanner launches a remediation command,
an ordinary command writes, or the explicit writer escapes its temporary data root.
Compatibility tests retain v1/v3/v5 golden fixtures unchanged. Closure runs the complete
repository suite after all focused suites; live directories, paid services, and installed
plugins are never test dependencies.

## Rollback strategy

- Before installation, rollback is limited to reverting the separately reviewed F5 slice;
  no slice mutates installed state.
- Public changes are additive and old schemas stay present, so the prior Plugin Compass
  version can read its former inputs and ignore the F5 data directory.
- After an authorized install, reinstall the last known-good plugin versions. Do not
  delete the trust store automatically; immutable reports are harmless to the older
  reader and remain available for audit.
- If index validation fails, readers ignore the invalid candidate, retain the last valid
  pointer, and report degraded evidence. They never reconstruct trust by guessing.
- F5B rollback removes only the optional topology input path. Builder run specs, plans,
  state, and G1/G2 contracts remain authoritative and unchanged.

## Security model and limitations

- Exact bytes plus authoritative identity establish evidence applicability, not safety.
- Static scanners may miss vulnerabilities; unknown coverage remains unknown.
- A local SHA-256 store detects content mismatch but is not a signed attestation and does
  not protect against a fully compromised host.
- Scanner binaries are dependencies with their own supply-chain risk; record executable
  identity where available and never execute remediation.
- Large or unusual packages may exceed traversal/runtime limits and remain incomplete.
- Windows path aliases, case behavior, spaces, junctions, reparse points, and file-change
  races receive explicit fixtures and fail-closed handling.
- Authorization is an external capability, never a boolean inside repository-controlled
  JSON.
- The plugin manifest currently advertises only `Read`. Shipping F5A in the same plugin
  requires honest disclosure of the explicit process/data-write surface. If the product
  must remain globally read-only at package level, F5A must instead ship as a separately
  named companion plugin; decide this before F5A4.

## Decisions relative to G2

F5 implementation does not block G2. One ownership decision should be ratified before G2
to prevent duplicate scheduling logic:

1. F5B topology is advisory and upstream only.
2. `keep-local` means no Builder run.
3. G2 consumes only validated Builder `run-spec.v2`/`pipeline-plan.v2`; it never consumes
   or re-evaluates the Plugin Compass topology directly.
4. Compass Builder remains the only scheduler and maps topology only at its validated
   pre-plan input boundary.

The exact authorization-provider interface and same-plugin versus companion-plugin choice
do not block G2, but must be resolved before F5B3 and F5A4 respectively.

## Estimate and approval boundary

The preliminary 12–22 hour range is too small after repository inspection. The required
immutable storage, adversarial filesystem handling, schema migrations, two-plugin
handoff, and full compatibility matrix produce this planning estimate:

- F5A: 12–21 engineering hours.
- F5B: 10–17 engineering hours.
- Closure: 3–5 engineering hours.
- Combined: 25–43 engineering hours, excluding approval latency, live scanner setup,
  installation/restart, and unexpected defects.

This is not a commitment. No slice is authorized by this plan. Each slice needs separate
approval, and F5A and F5B may progress independently only while their file ownership is
disjoint and the commander preserves G1.
