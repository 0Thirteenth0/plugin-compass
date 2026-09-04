# Workstreams F3 and D3 baseline

## Facts

- `codex plugin list --json` remains the only authority for plugin identity and
  installed/enabled state.
- Plugin Compass discovers standalone skills only from explicit bounded user, project,
  and system root pairs. It does not infer roots from plugin caches, home directories,
  environment variables, or arbitrary filesystem traversal.
- Packaged and standalone skills use source-neutral records with deterministic qualified
  identities, provenance, trust, metadata, readiness, assessment, ambiguity, and
  recommendation fields. Standalone skills are never represented as plugins.
- Plugin Compass plan v5, inventory v3, and prompt v3 expose those records while retaining
  the existing plugin-keyed compatibility fields. Discovery remains read-only and never
  invokes, installs, copies, edits, or synchronizes skills.
- Automatic mixed-source selection computes an exact bounded minimum-cardinality cover;
  an ambiguous bare identity or a search beyond the explicit bound selects nothing and
  requests exact qualification.
- Compass Builder `plan-bundle.v1` behavior is retained. Outcome gates are opt-in through
  the separate closed `plan-bundle.v2` contract and require a trusted in-process
  `OperatorGateProvider`; the standalone CLI cannot broker that authority.
- V2 story verification and gates precede branch import. Existing post-merge checks
  precede root gates, and exact authenticated coverage precedes verified-state and
  completion advancement.
- Gate receipts are provider-sealed, hash-chained, exact-target evidence backed by a
  provider-held monotonic checkpoint with explicit genesis initialization. Approved
  commands are provider-reserved before launch under scope-stable execution and unique
  attempt keys; unresolved attempts cannot replay, while authenticated non-met attempts
  require fresh authority. Receipt seals are authenticated before publication and
  detached command audits use recorded-platform validation. Unauthenticated, truncated,
  wrong-phase, wrong-target, path-escaping, or incomplete evidence fails closed.
- Plugin Compass remains the read-only recommender. Compass Builder remains the sole
  scheduler, worker-clone owner, verifier, importer, integrator, and completion authority.

## Decisions and trade-offs

- Explicit standalone roots are used because Codex exposes no authoritative standalone
  enumeration in the plugin listing. This favors truthful bounded coverage over inferred
  convenience.
- New public schema versions preserve compatibility instead of forcing standalone skills
  into `PluginRecord` or changing existing output semantics in place.
- Outcome-gate enforcement is isolated behind v2 so existing v1 runs do not acquire a new
  approval dependency.
- The provider, rather than repository-controlled state, owns receipt authentication,
  initialization state, per-attempt command reservations, and the monotonic checkpoint.
  This adds host integration work but prevents ledger files from self-authorizing,
  replaying an uncertain command, or concealing deletion.

## Assumptions to validate

- A future Codex host integration can implement the provider interface with durable,
  atomic checkpoint and per-attempt reservation storage plus protected sealing material.
- Explicit root configuration remains acceptable until Codex exposes an authoritative
  standalone-skill enumeration API.
- The exact-cover bounds are sufficient for intended installations; larger candidate
  sets can use explicit qualified selection rather than approximation.

## Unknowns and residual evidence

- F4 adversarial release closure and an authorized installed-copy validation are not part
  of this slice.
- A production UI/operator broker and live repository gate execution remain unimplemented
  and unauthorized.
- Four POSIX-only gate/process cases cannot execute on the Windows validation host and
  still require Linux runtime evidence.
- Token telemetry, rolling scheduling, higher concurrency, and live performance
  graduation remain later Workstreams E and G.

## Authority and evidence

- `feature_planning.md`, third approved implementation-slice decision
- `docs/PRODUCT_CONTRACT.md`
- `docs/TECHNICAL_DESIGN.md`
- `docs/COMPASS_BUILDER_CONTRACT.md`
- `docs/aegis/plans/2026-09-02-workstreams-f3-d3.md`
- Independent F3 specification and quality/security approvals
- Independent D3 specification and quality/security approvals
- Repository audit required by the implementation plan before slice closeout

## Baseline commands

```powershell
python -m unittest tests.test_source_neutral_skill_models tests.test_standalone_skill_discovery tests.test_standalone_skill_ordering tests.test_skill_decision tests.test_f3_cli tests.test_cli tests.test_adapters -v
python -m unittest tests.test_builder_d3_gates tests.test_builder_gate_runner tests.test_builder_outcome_gates tests.test_builder_controller tests.test_builder_models tests.test_builder_state -v
python -m unittest tests.test_builder_verifier tests.test_builder_integrator tests.integration.test_builder_worktrees -v
python scripts/check_repo_harness.py --profile audit --format json
git diff --check
```
