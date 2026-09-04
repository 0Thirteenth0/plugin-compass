# Plugin Compass Workstream F4 evidence

Date: 2026-09-03
Scope: adversarial repository release evidence for standalone-skill discovery
Baseline: `docs/aegis/baseline/2026-09-03-workstreams-f3-d3-baseline.md`

## Boundary

This work adds test and documentation pressure to F1-F3. It does not add discovery
authority, install or invoke skills, infer plugins from caches, change packaged-plugin
identity, or add execution/synchronization machinery. Plugin identity and installed or
enabled state remain owned only by `codex plugin list --json` or an explicit saved output
from that command.

## Requirement-to-test map

| Requirement | Evidence |
| --- | --- |
| Packaged discovery unchanged; no fabricated plugins | `test_f4_release_closure.F4ReleaseClosureTests.test_plugin_inventory_and_packaged_skills_are_unchanged_by_standalone_roots`; `test_adapters.CodexAdapterTests.test_live_nonempty_inventory_still_uses_the_official_cli` |
| Explicit user/project/system roots, including spaces | `test_f3_cli.F3CliTests.test_explicit_repeatable_roots_reach_every_json_surface`; `test_standalone_skill_discovery.StandaloneSkillDiscoveryTests.test_discovers_configured_roots_and_preserves_duplicate_names`; F4 packaged-compatibility and determinism cases |
| Missing/unreadable roots and files remain truthful | `test_standalone_skill_discovery` missing-root, unreadable-skill, directory-enumeration, and root-resolution-error cases; `test_f3_cli` missing-root JSON/Markdown cases |
| Same-name records remain distinct | `test_standalone_skill_discovery...test_discovers_configured_roots_and_preserves_duplicate_names`; `test_source_neutral_skill_models...test_qualified_identity_disambiguates_duplicate_names_without_absolute_roots` |
| Ambiguous bare identities fail closed | `test_skill_decision...test_ambiguous_bare_name_selects_none_and_names_sorted_candidates`; `test_f3_cli...test_qualified_selection_is_exact_and_ambiguous_bare_name_selects_none` |
| Incomplete frontmatter stays visible/degraded but cannot be selected | `test_skill_decision.SkillDecisionTests.test_incomplete_frontmatter_records_are_degraded_and_never_selectable` |
| Strict frontmatter subset and bounded malformed input | `test_standalone_skill_discovery` strict-subset, malformed, invalid-encoding/duplicate-key, oversized, and file-growth cases |
| Traversal, root/entry/skill/reference count, and runtime limits | `test_standalone_skill_discovery` depth, root, directory-entry, skill, readiness-reference, and runtime cases; `test_skill_decision` candidate/state bound cases |
| Symlink/reparse/path escape and use-time identity-swap rejection | `test_standalone_skill_discovery` Windows reparse classification, configured/nested reparse, traversal reference, drive/rooted reference, and metadata/readiness identity-swap cases |
| Deterministic inventory/recommend/prompt output | `test_f4_release_closure...test_inventory_recommend_and_prompt_json_are_root_order_deterministic`; ordering, source-neutral hash-seed, and decision input-order cases |
| No instruction execution or writes | `test_f4_release_closure...test_skill_documents_are_inert_and_discovery_opens_files_read_only` |
| Closed trust model/decision/schema allowlists | `test_source_neutral_skill_models...test_skill_trust_is_closed_in_models_decisions_and_public_schemas` |
| Closed immutable assessment dimensions and recursive schema/model parity | `test_source_neutral_skill_models...test_skill_assessment_dimensions_are_exactly_the_closed_public_shape`; `test_f4_release_closure...test_inventory_plan_and_prompt_models_match_closed_public_schemas` |

## Strict RED evidence

The missing invalid-frontmatter test was added before any production edit.

```powershell
python -m unittest tests.test_standalone_skill_discovery.StandaloneSkillDiscoveryTests.test_invalid_encoding_and_duplicate_frontmatter_keys_fail_closed -v
```

Result: `Ran 1 test`; `FAILED (failures=1)`. Invalid UTF-8 frontmatter was accepted as
complete after replacement decoding, and a duplicate `name` key silently overwrote the
first value. The observed values were `invalid-encoding` and `overwritten-name`, rather
than fail-closed directory-name fallbacks.

Smallest correction: decode bounded bytes as strict UTF-8, emit
`skill-invalid-encoding`, and reject duplicate frontmatter keys as malformed. No other
production owner changed.

An independent specification review then identified that incomplete frontmatter records
were preserved with `metadata_status: partial` but treated as recommendation-eligible.
The focused test was added before the correction:

```powershell
python -m unittest tests.test_skill_decision.SkillDecisionTests.test_incomplete_frontmatter_records_are_degraded_and_never_selectable -v
```

Result: `Ran 1 test`; `FAILED (failures=1)`. Both name-only and description-only records
were auto-recommended; the failure displayed two `SkillRecommendation` records with
`metadata_status='partial'`.

Smallest correction: preserve both source-neutral records and `partial` status, emit a
`skill-metadata-incomplete` diagnostic, and remove `partial` from usable recommendation
metadata. No field was fabricated and no record was dropped.

The quality/security review identified four additional families. Each received its own
test-only RED before its production correction:

```powershell
python -m unittest tests.test_standalone_skill_discovery.StandaloneSkillDiscoveryTests.test_strict_frontmatter_subset_rejects_yaml_complexity_and_non_strings -v
```

Result: `Ran 1 test`; `FAILED (failures=1)`. Quoted/complex keys, block and collection
scalars, null, and stray syntax were not consistently malformed. Correction: a
dependency-free flat simple-key/string-scalar subset with canonical duplicate detection.

```powershell
python -m unittest tests.test_source_neutral_skill_models.SourceNeutralSkillModelTests.test_skill_trust_is_closed_in_models_decisions_and_public_schemas -v
```

Result: `Ran 1 test`; `FAILED (failures=1)`. A forged `untrusted ` record was selected.
Correction: six exact model/schema statuses and a narrower `not_assessed`/`trusted`
recommendation allowlist.

```powershell
python -m unittest tests.test_standalone_skill_discovery.StandaloneSkillDiscoveryTests.test_identity_swaps_at_skill_read_and_readiness_use_fail_closed -v
```

Result: `Ran 1 test`; `FAILED (failures=1)`. A post-validation identity substitution
caused external `escaped` metadata to be consumed. Correction: bounded read-only
descriptors with no-follow flags where available, regular-file and named/opened identity
checks, plus post-use containment revalidation for metadata and readiness probes.

```powershell
python -m unittest tests.test_source_neutral_skill_models.SourceNeutralSkillModelTests.test_skill_assessment_dimensions_are_exactly_the_closed_public_shape -v
```

Result: `Ran 1 test`; `FAILED (failures=5)`. Empty, invented, missing, and additional
dimension shapes were accepted by the model, and the schema was open. Correction: exact
four-field validation in both owners and recursive closed-object parity coverage.

A final specification review found that YAML base-prefixed and underscore-form numeric
scalars still passed as strings. The focused cases were added before the production edit:

```powershell
python -m unittest tests.test_standalone_skill_discovery.StandaloneSkillDiscoveryTests.test_strict_frontmatter_subset_rejects_yaml_complexity_and_non_strings -v
```

Result: `Ran 1 test in 0.066s`; `FAILED (failures=1)`. Unquoted `0x10`, `0b10`,
`0o10`, and `1_000` values produced complete metadata instead of degraded malformed
records. Correction: extend only the strict non-string scalar matcher for those YAML
numeric forms; ordinary plain text and balanced-quoted numeric-looking strings remain
valid strings.

A subsequent review supplied three more independently witnessed RED families before any
associated production edit:

```powershell
python -m unittest tests.test_standalone_skill_discovery.StandaloneSkillDiscoveryTests.test_strict_frontmatter_subset_rejects_yaml_complexity_and_non_strings -v
```

Result: `Ran 1 test in 0.082s`; `FAILED (failures=1)`. The parser still accepted one or
more of the unquoted `1:20`, `123 # comment`, `0x10 # comment`, `2026-09-03`, `@oops`,
`]`, and `foo: bar` description values. Correction: one explicit narrow plain-string
grammar, preserving ordinary strings and balanced-quoted strings.

```powershell
python -m unittest tests.test_standalone_skill_discovery.StandaloneSkillDiscoveryTests.test_containment_is_revalidated_after_open_before_first_byte_read -v
```

Result: `Ran 1 test in 0.048s`; `FAILED (failures=1)`. The deterministic containment
change recorded two `os.read` calls before the post-read rejection. Correction: validate
opened identity/current containment during secure open and again immediately before the
first byte, while retaining post-read/use defense in depth for metadata and readiness.

```powershell
python -m unittest tests.test_source_neutral_skill_models.SourceNeutralSkillModelTests.test_skill_assessment_dimensions_are_exactly_the_closed_public_shape -v
```

Result: `Ran 1 test in 0.001s`; `FAILED (failures=1)`. Post-construction mutation did not
raise. Correction: store a read-only mapping proxy and serialize a fresh exact-key dict.

## GREEN evidence

Focused correction:

```powershell
python -m unittest tests.test_standalone_skill_discovery.StandaloneSkillDiscoveryTests.test_invalid_encoding_and_duplicate_frontmatter_keys_fail_closed -v
```

Result: `Ran 1 test in 0.054s`; `OK`.

Incomplete-frontmatter review correction:

```powershell
python -m unittest tests.test_skill_decision.SkillDecisionTests.test_incomplete_frontmatter_records_are_degraded_and_never_selectable -v
```

Result: `Ran 1 test in 0.009s`; `OK`.

Quality/security focused corrections:

```powershell
python -m unittest tests.test_standalone_skill_discovery.StandaloneSkillDiscoveryTests.test_strict_frontmatter_subset_rejects_yaml_complexity_and_non_strings -v
python -m unittest tests.test_source_neutral_skill_models.SourceNeutralSkillModelTests.test_skill_trust_is_closed_in_models_decisions_and_public_schemas -v
python -m unittest tests.test_standalone_skill_discovery.StandaloneSkillDiscoveryTests.test_identity_swaps_at_skill_read_and_readiness_use_fail_closed -v
python -m unittest tests.test_source_neutral_skill_models.SourceNeutralSkillModelTests.test_skill_assessment_dimensions_are_exactly_the_closed_public_shape -v
```

Results: each ran one test and passed. Recorded durations were `0.074s`, `0.001s`,
`0.083s`, and `0.001s`, respectively.

A final combined run of those exact four cases passed `Ran 4 tests in 0.033s`; `OK`.

Final numeric-scalar correction:

```powershell
python -m unittest tests.test_standalone_skill_discovery.StandaloneSkillDiscoveryTests.test_strict_frontmatter_subset_rejects_yaml_complexity_and_non_strings -v
```

Result after adding valid plain and balanced-quoted preservation assertions:
`Ran 1 test in 0.071s`; `OK`.

Latest review corrections passed independently:

```powershell
python -m unittest tests.test_standalone_skill_discovery.StandaloneSkillDiscoveryTests.test_strict_frontmatter_subset_rejects_yaml_complexity_and_non_strings -v
python -m unittest tests.test_standalone_skill_discovery.StandaloneSkillDiscoveryTests.test_containment_is_revalidated_after_open_before_first_byte_read -v
python -m unittest tests.test_source_neutral_skill_models.SourceNeutralSkillModelTests.test_skill_assessment_dimensions_are_exactly_the_closed_public_shape -v
```

Results: the final scalar case, including per-record automatic/exact ineligibility and
quoted preservation, ran `1 test in 0.089s`; `OK`. The pre-read case ran
`1 test in 0.058s`; `OK`; and the immutable-dimensions case ran
`1 test in 0.001s`; `OK`.

F4 plus adjacent F1-F3/CLI/adapter regression lane:

```powershell
python -m unittest tests.test_f4_release_closure tests.test_source_neutral_skill_models tests.test_standalone_skill_discovery tests.test_standalone_skill_ordering tests.test_skill_decision tests.test_f3_cli tests.test_cli tests.test_adapters -v
```

Fresh result after the plain-scalar, pre-read containment, and immutable-dimensions
corrections: `Ran 99 tests in 2.310s`; `OK`.

Syntax and patch hygiene:

```powershell
python -m py_compile plugins/plugin-compass/plugin_compass/adapters/standalone.py plugins/plugin-compass/plugin_compass/skill_models.py plugins/plugin-compass/plugin_compass/skill_decision.py tests/test_source_neutral_skill_models.py tests/test_standalone_skill_discovery.py tests/test_f4_release_closure.py
git diff --check
```

Result: both exited `0` with no output.

Documentation profile:

```powershell
python scripts/check_repo_harness.py --profile docs --format json
```

Result: `status: passed`, eight checks passed, `validationLevel: focused`, and
`fullValidation: false`.

## Limitations and safe stop

- Tests use saved plugin inventory and temporary standalone roots; they do not inspect
  live standalone directories or run a live `codex plugin list --json` command.
- No installed-copy validation, plugin installation, publication, hook enablement, or
  cache mutation was authorized or performed. The installed-copy release gate remains
  open.
- The F4 focused and adjacent lane is green. This report does not claim the combined
  F4/E1 slice, whole repository, or live installed feature complete.
