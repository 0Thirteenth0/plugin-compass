from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

from tests.helpers.builder_models import (
    passing_resume, pending_state_entry, resolved_schema_node, schema_allows_string,
    state_entry, state_snapshot, three_branch_advance,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "compass-builder"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "compass_builder"
SCHEMAS = PLUGIN_ROOT / "schemas"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from compass_builder.models import (  # noqa: E402
    ContractValidationError,
    canonical_json,
    normalize_contract,
    run_binding_digest,
    validate_benchmark_aggregate_receipts,
    validate_benchmark_pair,
    validate_host_capabilities_at,
    validate_run_bindings,
    validate_run_structure_bindings,
    validate_run_state_transition,
)
CONTRACTS = (
    "run-spec",
    "wave-plan",
    "run-state",
    "host-capabilities",
    "worker-receipt",
    "benchmark-receipt",
    "benchmark-workloads",
    "benchmark-aggregate",
)
def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.valid.json").read_text(encoding="utf-8"))
def digest_contract(value: dict, contract: str) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value, contract)).hexdigest()
def digest_value(value) -> str:
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
def bound_aggregate_and_receipts() -> tuple[dict, list[dict]]:
    aggregate = fixture("benchmark-aggregate")
    template = fixture("benchmark-receipt")
    receipts = []
    controls = aggregate["workloadControls"][0]["controls"]
    for attempt in aggregate["attempts"]:
        receipt = copy.deepcopy(template)
        receipt["workloadId"] = attempt["workloadId"]
        receipt["attemptId"] = attempt["attemptId"]
        receipt["arm"] = attempt["arm"]
        receipt["pairNumber"] = attempt["pairNumber"]
        receipt["trialNumber"] = attempt["pairNumber"]
        receipt["warmup"] = attempt["pairNumber"] == 0
        receipt["terminalStatus"] = attempt["terminalStatus"]
        receipt["controls"] = copy.deepcopy(controls)
        attempt["receiptDigest"] = digest_contract(receipt, "benchmark-receipt")
        receipts.append(receipt)
    return aggregate, receipts

class BuilderModelTests(unittest.TestCase):
    def assert_invalid(self, contract: str, value: dict, field: str) -> None:
        with self.assertRaises(ContractValidationError) as raised:
            normalize_contract(contract, value)
        message = str(raised.exception)
        self.assertIn(field, message)
        self.assertIn("corrective direction:", message)
    def test_all_valid_fixtures_round_trip_to_identical_canonical_bytes(self):
        for contract in CONTRACTS:
            with self.subTest(contract=contract):
                path = FIXTURES / f"{contract}.valid.json"
                original = path.read_bytes()
                value = json.loads(original)
                normalized = normalize_contract(contract, value)
                self.assertEqual(value, normalized)
                self.assertEqual(original, canonical_json(normalized, contract))
    def test_every_schema_is_versioned_closed_and_matches_fixture_shape(self):
        for contract in CONTRACTS:
            with self.subTest(contract=contract):
                schema = json.loads((SCHEMAS / f"{contract}.schema.json").read_text(encoding="utf-8"))
                value = fixture(contract)
                self.assertEqual(value["schemaVersion"], schema["properties"]["schemaVersion"]["const"])
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(set(value), set(schema["required"]))
    def test_closed_shape_and_version_fail_with_actionable_paths(self):
        value = fixture("run-spec")
        value["unexpected"] = True
        self.assert_invalid("run-spec", value, "$")
        value = fixture("run-spec")
        value["schemaVersion"] = "compass-builder.run-spec.v0"
        self.assert_invalid("run-spec", value, "$.schemaVersion")
    def test_run_spec_rejects_bad_ids_dependencies_cycles_and_scopes(self):
        cases = []
        bad = fixture("run-spec"); bad["runId"] = "run-weak"; cases.append((bad, "$.runId"))
        bad = fixture("run-spec"); bad["stories"][1]["id"] = "alpha"; cases.append((bad, "$.stories"))
        bad = fixture("run-spec"); bad["stories"][0]["dependsOn"] = ["missing"]; cases.append((bad, "dependsOn"))
        bad = fixture("run-spec"); bad["stories"][0]["dependsOn"] = ["beta"]; bad["stories"][1]["dependsOn"] = ["alpha"]; cases.append((bad, "$.stories"))
        bad = fixture("run-spec"); bad["stories"][0]["dependsOn"] = ["beta"]; cases.append((bad, "$.stories[0].dependsOn"))
        for scope in (
            "/absolute", "../escape", "C:/drive", "\\\\server\\share", "src\\windows",
            "src/name:stream", "src/bad<name", 'src/bad"name', "src/bad|name",
            "src/bad?name", "src/bad*name", "src/trailing.", "src/trailing ",
            "src/CON", "src/prn.txt", "src/Com9.log", "src/LPT1", "src/control\x7f",
            " src/leading",
        ):
            bad = fixture("run-spec"); bad["stories"][0]["writeScopes"] = [scope]; cases.append((bad, "writeScopes"))
        for value, path in cases:
            with self.subTest(path=path, value=value):
                self.assert_invalid("run-spec", value, path)
    def test_run_spec_rejects_missing_validation_modes_and_nonexact_model(self):
        bad = fixture("run-spec")
        bad["stories"][0]["validationCommands"] = []
        self.assert_invalid("run-spec", bad, "$.stories[0]")
        for field, value in (("mode", "fast"), ("exactModel", "inherit"), ("hostConcurrencyCeiling", 0)):
            bad = fixture("run-spec"); bad[field] = value
            self.assert_invalid("run-spec", bad, f"$.{field}")
    def test_git_branches_reject_control_windows_and_check_ref_format_hazards(self):
        hazards = (
            "cb/control\x01name", "cb/control\x7fname", "cb/name:stream", "cb/bad<name",
            'cb/bad"name', "cb/bad|name", "cb/bad?name", "cb/bad*name",
            "cb/trailing.", "cb/segment./name", "cb/CON", "cb/prn.txt", "cb/Com1.log",
            "cb/LPT9", "cb/.hidden", "cb/name.lock", "cb/name..next", "cb/name@{next",
            "cb/white\u00a0space",
        )
        for branch_name in hazards:
            with self.subTest(branch=repr(branch_name)):
                bad = fixture("run-spec")
                bad["integrationBranch"] = branch_name
                self.assert_invalid("run-spec", bad, "integrationBranch")
        run_schema = json.loads((SCHEMAS / "run-spec.schema.json").read_text(encoding="utf-8"))
        for definition in ("branch", "scope"):
            re.compile(run_schema["$defs"][definition]["pattern"])
        for branch_name in hazards:
            self.assertFalse(schema_allows_string(run_schema, run_schema["$defs"]["branch"], branch_name))
        for path in ("src/name:stream", "src/bad<name", 'src/bad"name', "src/bad|name", "src/bad?name", "src/bad*name", "src/trailing.", "src/CON", "src/prn.txt", "src/control\x7f", " src/leading"):
            self.assertFalse(schema_allows_string(run_schema, run_schema["$defs"]["scope"], path))
    def test_wave_plan_rejects_unsupported_effort_and_broken_accounting_or_sha_chain(self):
        bad = fixture("wave-plan"); bad["stories"][0]["recommendedEffort"] = "guess"
        self.assert_invalid("wave-plan", bad, "recommendedEffort")
        bad = fixture("wave-plan"); bad["waves"][0]["storyIds"].reverse()
        self.assert_invalid("wave-plan", bad, "$.waves")
        bad = fixture("wave-plan"); bad["integrationExpectedSha"] = "not-a-sha"
        self.assert_invalid("wave-plan", bad, "$.integrationExpectedSha")
    def test_run_state_rejects_invalid_transition_and_broken_branch_chain(self):
        bad = fixture("run-state"); bad["previousState"] = "planned"
        self.assert_invalid("run-state", bad, "$.state")
        bad = fixture("run-state"); bad["waves"][0]["branches"][1]["preMergeExpectedSha"] = "a" * 40
        self.assert_invalid("run-state", bad, "preMergeExpectedSha")
        bad = fixture("run-state"); bad["waves"][0]["branches"][0]["controllerCheckDigest"] = None
        self.assert_invalid("run-state", bad, "branches[0]")
        bad = fixture("run-state"); bad["waves"][0]["branches"][1]["integrationState"] = "merged"
        self.assert_invalid("run-state", bad, "branches[1]")
    def test_host_and_worker_receipts_reject_unsupported_or_stale_claims(self):
        bad = fixture("host-capabilities"); bad["supportedEfforts"].append("guess")
        self.assert_invalid("host-capabilities", bad, "supportedEfforts")
        bad = fixture("host-capabilities"); del bad["reasoningConfig"]
        self.assert_invalid("host-capabilities", bad, "reasoningConfig")
        bad = fixture("host-capabilities"); bad["reasoningConfig"]["key"] = "generic.key"
        self.assert_invalid("host-capabilities", bad, "reasoningConfig.key")
        bad = fixture("host-capabilities"); bad["reasoningConfig"]["evidenceDigest"] = "unproven"
        self.assert_invalid("host-capabilities", bad, "reasoningConfig.evidenceDigest")
        bad = fixture("host-capabilities"); bad["capturedAt"] = "2026-09-01"
        self.assert_invalid("host-capabilities", bad, "capturedAt")
        bad = fixture("worker-receipt"); bad["commitSha"] = "c" * 40
        self.assert_invalid("worker-receipt", bad, "$.commitSha")
        bad = fixture("worker-receipt"); bad["elapsedMs"] = 0
        self.assert_invalid("worker-receipt", bad, "$.elapsedMs")
        bad = fixture("worker-receipt"); bad["status"] = "blocked"
        self.assert_invalid("worker-receipt", bad, "$.blocker")
    def test_benchmark_receipt_requires_every_metric_and_valid_terminal_evidence(self):
        bad = fixture("benchmark-receipt"); del bad["metrics"]["timeouts"]
        self.assert_invalid("benchmark-receipt", bad, "$.metrics")
        bad = fixture("benchmark-receipt"); bad["elapsedMs"] = -1
        self.assert_invalid("benchmark-receipt", bad, "$.elapsedMs")
        bad = fixture("benchmark-receipt"); bad["terminalStatus"] = "failed"
        self.assert_invalid("benchmark-receipt", bad, "$.finalGreenSha")
        bad = fixture("benchmark-receipt"); bad["controls"]["initialEfforts"].pop()
        self.assert_invalid("benchmark-receipt", bad, "initialEfforts")
    def test_workload_manifest_rejects_pair_arm_and_attempt_drift(self):
        bad = fixture("benchmark-workloads"); bad["pairCount"] = 4
        self.assert_invalid("benchmark-workloads", bad, "$.pairCount")
        bad = fixture("benchmark-workloads"); bad["workloads"][0]["pairs"].pop()
        self.assert_invalid("benchmark-workloads", bad, "pairs")
        bad = fixture("benchmark-workloads"); bad["workloads"][0]["pairs"][0]["arms"][1]["arm"] = "sequential"
        self.assert_invalid("benchmark-workloads", bad, "arms")
        bad = fixture("benchmark-workloads"); bad["workloads"][0]["pairs"][1]["arms"].reverse()
        self.assert_invalid("benchmark-workloads", bad, "arms")
        bad = fixture("benchmark-workloads"); bad["workloads"][0]["pairs"][0]["arms"][0]["attemptId"] = "sample-warm-seq"
        self.assert_invalid("benchmark-workloads", bad, "attempt")
    def test_aggregate_rejects_missing_extra_duplicate_reordered_and_unbound_accounting(self):
        mutations = []
        bad = fixture("benchmark-aggregate"); bad["attempts"].pop(); mutations.append(bad)
        bad = fixture("benchmark-aggregate"); bad["attempts"].append(copy.deepcopy(bad["attempts"][-1])); mutations.append(bad)
        bad = fixture("benchmark-aggregate"); bad["attempts"][0], bad["attempts"][1] = bad["attempts"][1], bad["attempts"][0]; mutations.append(bad)
        bad = fixture("benchmark-aggregate"); bad["attempts"][0]["attemptId"] = "extra"; mutations.append(bad)
        for bad in mutations:
            self.assert_invalid("benchmark-aggregate", bad, "$.attempts")
        bad = fixture("benchmark-aggregate"); bad["workloadManifestDigest"] = "sha256:" + "0" * 64
        self.assert_invalid("benchmark-aggregate", bad, "$.workloadManifestDigest")
    def test_validation_does_not_mutate_input_and_canonicalization_is_stable(self):
        value = fixture("run-spec")
        before = copy.deepcopy(value)
        first = canonical_json(value, "run-spec")
        second = canonical_json(json.loads(first), "run-spec")
        self.assertEqual(before, value)
        self.assertEqual(first, second)
    def test_non_string_mapping_keys_fail_with_an_actionable_object_path(self):
        bad = fixture("run-spec")
        bad[7] = "not-json"
        with self.assertRaisesRegex(ContractValidationError, r"^\$: contains a non-string.*corrective direction"):
            normalize_contract("run-spec", bad)
        for pairs in ([('schemaVersion', 'compass-builder.run-spec.v1')], (('schemaVersion', 'compass-builder.run-spec.v1'),)):
            with self.assertRaisesRegex(ContractValidationError, r"^\$: must be a JSON object mapping.*corrective direction"):
                normalize_contract("run-spec", pairs)
    def test_versioned_collection_bounds_and_iterative_deep_cycle_validation(self):
        template = fixture("run-spec")["stories"][0]
        too_many = fixture("run-spec")
        too_many["stories"] = []
        for index in range(129):
            story = copy.deepcopy(template)
            story.update({"id": f"s{index:03d}", "title": f"Story {index}", "dependsOn": [], "writeScopes": [f"src/s{index:03d}"]})
            too_many["stories"].append(story)
        self.assert_invalid("run-spec", too_many, "at most 128")
        cyclic = fixture("run-spec")
        cyclic["stories"] = []
        for index in range(128):
            story = copy.deepcopy(template)
            next_id = f"s{(index + 1) % 128:03d}"
            story.update({"id": f"s{index:03d}", "title": f"Story {index}", "dependsOn": [next_id], "writeScopes": [f"src/s{index:03d}"]})
            cyclic["stories"].append(story)
        self.assert_invalid("run-spec", cyclic, "dependency cycle")
        too_many_checks = fixture("worker-receipt")
        too_many_checks["checks"] = [copy.deepcopy(too_many_checks["checks"][0]) for _ in range(257)]
        self.assert_invalid("worker-receipt", too_many_checks, "at most 256")
        too_many_pairs = fixture("benchmark-workloads")
        too_many_pairs["pairCount"] = 101
        self.assert_invalid("benchmark-workloads", too_many_pairs, "<= 100")
    def test_cross_contract_bindings_reject_stale_immutable_identifiers(self):
        spec = fixture("run-spec")
        plan = fixture("wave-plan")
        validate_run_structure_bindings(spec, plan)
        bad = fixture("wave-plan")
        bad["runId"] = "cb-20260901-fedcba9876543210"
        with self.assertRaisesRegex(ContractValidationError, "wavePlan.runId.*corrective direction"):
            validate_run_structure_bindings(spec, bad)
    def test_benchmark_pair_rejects_incomparable_non_mode_controls(self):
        parallel = fixture("benchmark-receipt")
        sequential = copy.deepcopy(parallel)
        sequential["arm"] = "sequential"
        sequential["attemptId"] = "sample-p1-seq"
        validate_benchmark_pair(sequential, parallel)
        sequential["controls"]["exactModel"] = "different-model"
        with self.assertRaisesRegex(ContractValidationError, "controls.*exactModel.*corrective direction"):
            validate_benchmark_pair(sequential, parallel)
    def test_run_bindings_enforce_digest_ceiling_mode_dependencies_host_and_branches(self):
        spec, plan, host, state = (fixture(name) for name in ("run-spec", "wave-plan", "host-capabilities", "run-state"))
        self.assertEqual(run_binding_digest(spec, plan), digest_value({"runSpec": spec, "wavePlan": plan}))
        self.assertEqual(state["runBindingDigest"], run_binding_digest(spec, plan))
        validate_run_bindings(spec, plan, state, host_capabilities=host, planning_timestamp="2026-09-01T12:01:00Z")
        bad = copy.deepcopy(plan); bad["normalizedInputDigest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ContractValidationError, "normalizedInputDigest"):
            validate_run_bindings(spec, bad, host_capabilities=host, planning_timestamp="2026-09-01T12:01:00Z")
        bad = copy.deepcopy(plan); bad["concurrency"] = 3
        with self.assertRaisesRegex(ContractValidationError, "concurrency.*ceiling"):
            validate_run_bindings(spec, bad, host_capabilities=host, planning_timestamp="2026-09-01T12:01:00Z")
        bad = copy.deepcopy(plan); bad["mode"] = "sequential"; bad["concurrency"] = 2
        with self.assertRaisesRegex(ContractValidationError, "sequential mode"):
            validate_run_bindings(spec, bad, host_capabilities=host, planning_timestamp="2026-09-01T12:01:00Z")
        explicit_spec = copy.deepcopy(spec); explicit_spec["mode"] = "parallel"
        bad = copy.deepcopy(plan); bad["mode"] = "sequential"; bad["concurrency"] = 1
        bad["waves"] = [{"waveIndex": 0, "storyIds": ["alpha"]}, {"waveIndex": 1, "storyIds": ["beta"]}]
        bad["normalizedInputDigest"] = digest_contract(explicit_spec, "run-spec")
        with self.assertRaisesRegex(ContractValidationError, "explicit run-spec mode"):
            validate_run_bindings(explicit_spec, bad, host_capabilities=host, planning_timestamp="2026-09-01T12:01:00Z")
        dependent_spec = copy.deepcopy(spec)
        dependent_spec["stories"][1]["dependsOn"] = ["alpha"]
        dependent_plan = copy.deepcopy(plan)
        dependent_plan["normalizedInputDigest"] = digest_contract(dependent_spec, "run-spec")
        with self.assertRaisesRegex(ContractValidationError, "not dependency-ready"):
            validate_run_bindings(dependent_spec, dependent_plan, host_capabilities=host, planning_timestamp="2026-09-01T12:01:00Z")
        bad_host = copy.deepcopy(host); bad_host["selectedModel"] = "other-model"
        bad_plan = copy.deepcopy(plan); bad_plan["hostEvidenceDigest"] = digest_contract(bad_host, "host-capabilities")
        with self.assertRaisesRegex(ContractValidationError, "exactModel"):
            validate_run_bindings(spec, bad_plan, host_capabilities=bad_host, planning_timestamp="2026-09-01T12:01:00Z")
        bad_host = copy.deepcopy(host); bad_host["supportedEfforts"] = ["low"]
        bad_plan = copy.deepcopy(plan); bad_plan["hostEvidenceDigest"] = digest_contract(bad_host, "host-capabilities")
        with self.assertRaisesRegex(ContractValidationError, "unsupported effort"):
            validate_run_bindings(spec, bad_plan, host_capabilities=bad_host, planning_timestamp="2026-09-01T12:01:00Z")
        bad_host = copy.deepcopy(host); bad_host["hostConcurrencyCeiling"] = 3
        bad_plan = copy.deepcopy(plan); bad_plan["hostEvidenceDigest"] = digest_contract(bad_host, "host-capabilities")
        with self.assertRaisesRegex(ContractValidationError, "hostConcurrencyCeiling"):
            validate_run_bindings(spec, bad_plan, host_capabilities=bad_host, planning_timestamp="2026-09-01T12:01:00Z")
        bad_host = copy.deepcopy(host); bad_host["supports"]["structuredOutput"] = False
        bad_plan = copy.deepcopy(plan); bad_plan["hostEvidenceDigest"] = digest_contract(bad_host, "host-capabilities")
        with self.assertRaisesRegex(ContractValidationError, "structuredOutput"):
            validate_run_bindings(spec, bad_plan, host_capabilities=bad_host, planning_timestamp="2026-09-01T12:01:00Z")
        bad_state = copy.deepcopy(state); bad_state["waves"][0]["branches"][0]["branch"] = "cb/run/wrong"
        with self.assertRaisesRegex(ContractValidationError, "planned immutable branch"):
            validate_run_bindings(spec, plan, bad_state, host_capabilities=host, planning_timestamp="2026-09-01T12:01:00Z")
        bad_state = copy.deepcopy(state); bad_state["runBindingDigest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ContractValidationError, "runBindingDigest.*canonical run spec"):
            validate_run_structure_bindings(spec, plan, bad_state)
    def test_parallel_bindings_fail_closed_on_scope_state_validation_and_branch_risks(self):
        host = fixture("host-capabilities")
        def rejected(spec, plan, message):
            plan["normalizedInputDigest"] = digest_contract(spec, "run-spec")
            with self.assertRaisesRegex(ContractValidationError, message):
                validate_run_bindings(spec, plan, host_capabilities=host, planning_timestamp="2026-09-01T12:01:00Z")
        spec, plan = fixture("run-spec"), fixture("wave-plan")
        spec["stories"][1]["writeScopes"] = ["src/alpha"]
        rejected(spec, plan, "parallel scopes")
        spec, plan = fixture("run-spec"), fixture("wave-plan")
        spec["stories"][1]["writeScopes"] = ["SRC/ALPHA/nested"]
        rejected(spec, plan, "Windows normalization")
        spec, plan = fixture("run-spec"), fixture("wave-plan")
        spec["stories"][0]["sharedState"]["mode"] = "mutates"
        rejected(spec, plan, "may not mutate")
        spec, plan = fixture("run-spec"), fixture("wave-plan")
        spec["stories"][0]["validationStrength"] = "partial"
        rejected(spec, plan, "decisive actionable")
        spec, plan = fixture("run-spec"), fixture("wave-plan")
        plan["stories"][1]["branch"] = "CB/RUN/ALPHA"
        rejected(spec, plan, "Windows D/F ref collision")
        spec, plan = fixture("run-spec"), fixture("wave-plan")
        plan["stories"][0]["branch"] = spec["integrationBranch"]
        rejected(spec, plan, "D/F ref collision with integrationBranch")
        spec, plan = fixture("run-spec"), fixture("wave-plan")
        plan["stories"][1]["branch"] = "CB/RUN/ALPHA/nested"
        rejected(spec, plan, "Windows D/F ref collision")
        spec, plan = fixture("run-spec"), fixture("wave-plan")
        plan["stories"][0]["branch"] = "cb/integration/worker"
        rejected(spec, plan, "D/F ref collision with integrationBranch")
        spec, plan = fixture("run-spec"), fixture("wave-plan")
        plan["stories"][0]["branch"] = "cb"
        plan["stories"][1]["branch"] = "other/beta"
        rejected(spec, plan, "D/F ref collision with integrationBranch")
        spec, plan = fixture("run-spec"), fixture("wave-plan")
        spec["mode"] = "sequential"
        spec["stories"][1]["writeScopes"] = ["src/alpha"]
        spec["stories"][0]["sharedState"]["mode"] = "mutates"
        plan.update({"mode": "sequential", "concurrency": 1, "waves": [{"waveIndex": 0, "storyIds": ["alpha"]}, {"waveIndex": 1, "storyIds": ["beta"]}]})
        plan["normalizedInputDigest"] = digest_contract(spec, "run-spec")
        validate_run_bindings(spec, plan, host_capabilities=host, planning_timestamp="2026-09-01T12:01:00Z")
    def test_host_freshness_is_context_bound_not_inferred_from_timestamp_syntax(self):
        host = fixture("host-capabilities")
        validate_host_capabilities_at(host, "2026-09-01T12:05:00Z")
        with self.assertRaisesRegex(ContractValidationError, "stale.*fresh"):
            validate_host_capabilities_at(host, "2026-09-01T12:05:00.001Z")
        with self.assertRaisesRegex(ContractValidationError, "precedes capability capture"):
            validate_host_capabilities_at(host, "2026-09-01T11:59:59Z")
        with self.assertRaisesRegex(ContractValidationError, "hostCapabilities.*required"):
            validate_run_bindings(fixture("run-spec"), fixture("wave-plan"), planning_timestamp="2026-09-01T12:01:00Z")
        with self.assertRaisesRegex(ContractValidationError, "planningTimestamp"):
            validate_run_bindings(fixture("run-spec"), fixture("wave-plan"), host_capabilities=host)
    def test_multi_branch_state_advances_only_immediate_next_premerge_after_verification(self):
        initial = "a" * 40
        first_merge = "c" * 40
        second_merge = "e" * 40
        base = fixture("run-state")
        merging_first = state_snapshot(base,
            "wave-workers-complete", "wave-merging",
            [state_entry("alpha", "cb/run/alpha", "worker-verified", initial),
             state_entry("beta", "cb/run/beta", "worker-verified", initial)], initial, initial,
        )
        first_unverified = state_snapshot(base,
            "wave-merging", "wave-integrated-unverified",
            [state_entry("alpha", "cb/run/alpha", "merged", initial, first_merge),
             state_entry("beta", "cb/run/beta", "worker-verified", initial)], first_merge, initial,
        )
        merging_second = state_snapshot(base,
            "wave-integrated-unverified", "wave-merging",
            [state_entry("alpha", "cb/run/alpha", "integration-verified", initial, first_merge, True),
             state_entry("beta", "cb/run/beta", "worker-verified", first_merge)], first_merge, first_merge,
        )
        second_unverified = state_snapshot(base,
            "wave-merging", "wave-integrated-unverified",
            [state_entry("alpha", "cb/run/alpha", "integration-verified", initial, first_merge, True),
             state_entry("beta", "cb/run/beta", "merged", first_merge, second_merge)], second_merge, first_merge,
        )
        verified = state_snapshot(base,
            "wave-integrated-unverified", "wave-verified",
            [state_entry("alpha", "cb/run/alpha", "integration-verified", initial, first_merge, True),
             state_entry("beta", "cb/run/beta", "integration-verified", first_merge, second_merge, True)], second_merge, second_merge,
        )
        for value in (merging_first, first_unverified, merging_second, second_unverified, verified):
            with self.subTest(state=value["state"], previous=value["previousState"]):
                normalize_contract("run-state", value)
        validate_run_state_transition(merging_second, second_unverified)
        validate_run_state_transition(first_unverified, merging_second)
        validate_run_state_transition(second_unverified, verified)
        three_before, three_after = three_branch_advance(base)
        normalize_contract("run-state", three_before); normalize_contract("run-state", three_after)
        validate_run_state_transition(three_before, three_after)
        self.assertEqual(three_before["waves"][0]["branches"][0], three_after["waves"][0]["branches"][0])
        self.assertNotEqual(three_before["waves"][0]["branches"][2]["preMergeExpectedSha"], three_after["waves"][0]["branches"][2]["preMergeExpectedSha"])
        rewritten_merge = copy.deepcopy(merging_second)
        rewritten_merge["waves"][0]["branches"][0].update({"mergeSha": "f" * 40, "postCheckExpectedSha": "f" * 40})
        rewritten_merge["waves"][0]["branches"][1]["preMergeExpectedSha"] = "f" * 40
        rewritten_merge.update({"expectedIntegrationSha": "f" * 40, "lastVerifiedIntegrationSha": "f" * 40})
        normalize_contract("run-state", rewritten_merge)
        with self.assertRaisesRegex(ContractValidationError, "mergeSha.*already-recorded"):
            validate_run_state_transition(first_unverified, rewritten_merge)
        rewritten_history = copy.deepcopy(second_unverified)
        rewritten_history["waves"][0]["branches"][0].update({"mergeSha": "d" * 40, "postCheckExpectedSha": "d" * 40})
        rewritten_history["waves"][0]["branches"][1]["preMergeExpectedSha"] = "d" * 40
        rewritten_history["lastVerifiedIntegrationSha"] = "d" * 40
        normalize_contract("run-state", rewritten_history)
        with self.assertRaisesRegex(ContractValidationError, "already-recorded evidence"):
            validate_run_state_transition(merging_second, rewritten_history)
        bad = copy.deepcopy(first_unverified)
        bad["waves"][0]["branches"][1] = state_entry("beta", "cb/run/beta", "merged", initial, second_merge)
        self.assert_invalid("run-state", bad, "integrationState")
        bad = copy.deepcopy(second_unverified)
        bad["waves"][0]["branches"][0] = state_entry("alpha", "cb/run/alpha", "worker-verified", initial)
        bad["waves"][0]["branches"][1]["preMergeExpectedSha"] = initial
        bad["expectedIntegrationSha"] = initial
        bad["lastVerifiedIntegrationSha"] = initial
        self.assert_invalid("run-state", bad, "integrationState")
        completed = copy.deepcopy(verified)
        completed.update({"previousState": "wave-verified", "state": "completed"})
        validate_run_state_transition(verified, completed)
        verified_append = copy.deepcopy(completed)
        verified_append.update({"currentWaveIndex": 1, "expectedIntegrationSha": "f" * 40, "lastVerifiedIntegrationSha": "f" * 40})
        verified_append["waves"].append({"waveIndex": 1, "startExpectedSha": second_merge, "branches": [state_entry("gamma", "cb/run/gamma", "integration-verified", second_merge, "f" * 40, True)]})
        normalize_contract("run-state", verified_append)
        with self.assertRaisesRegex(ContractValidationError, "changes wave count/current index"):
            validate_run_state_transition(verified, verified_append)
        next_dispatch = copy.deepcopy(verified)
        next_dispatch.update({"previousState": "wave-verified", "state": "dispatching", "currentWaveIndex": 1})
        next_dispatch["waves"].append({"waveIndex": 1, "startExpectedSha": second_merge, "branches": [pending_state_entry("gamma", "cb/run/gamma", second_merge)]})
        validate_run_state_transition(verified, next_dispatch)
        synthetic = copy.deepcopy(next_dispatch)
        synthetic["waves"][1] = {"waveIndex": 1, "startExpectedSha": second_merge, "branches": [state_entry("gamma", "cb/run/gamma", "integration-verified", second_merge, "f" * 40, True)]}
        synthetic["waves"].append({"waveIndex": 2, "startExpectedSha": "f" * 40, "branches": [pending_state_entry("delta", "cb/run/delta", "f" * 40)]})
        synthetic.update({"currentWaveIndex": 2, "expectedIntegrationSha": "f" * 40, "lastVerifiedIntegrationSha": "f" * 40})
        normalize_contract("run-state", synthetic)
        with self.assertRaisesRegex(ContractValidationError, "changes wave count/current index"):
            validate_run_state_transition(verified, synthetic)
    def test_state_rejects_incomplete_prior_waves_incoherent_entries_and_dirty_post_checks(self):
        bad = fixture("run-state")
        bad["waves"][0]["branches"][0]["workerState"] = "running"
        self.assert_invalid("run-state", bad, "incoherent")
        bad = fixture("run-state")
        bad["waves"][0]["branches"][0]["postCheckExpectedSha"] = "d" * 40
        self.assert_invalid("run-state", bad, "postCheckExpectedSha")
        bad = fixture("run-state")
        final_entry = bad["waves"][0]["branches"][1]
        final_entry.update({"workerState": "blocked", "verificationState": "failed", "integrationState": "blocked", "mergeSha": None, "controllerCheckDigest": None, "postCheckExpectedSha": None})
        bad["expectedIntegrationSha"] = "c" * 40
        bad["lastVerifiedIntegrationSha"] = "c" * 40
        self.assert_invalid("run-state", bad, "$.state")
        two_wave = fixture("run-state")
        two_wave["previousState"] = "wave-verified"
        two_wave["state"] = "dispatching"
        two_wave["currentWaveIndex"] = 1
        two_wave["waves"].append({"waveIndex": 1, "startExpectedSha": "e" * 40, "branches": [{"storyId": "gamma", "branch": "cb/run/gamma", "workerState": "pending", "verificationState": "pending", "integrationState": "pending", "preMergeExpectedSha": "e" * 40, "mergeSha": None, "controllerCheckDigest": None, "postCheckExpectedSha": None}]})
        normalize_contract("run-state", two_wave)
        prior = two_wave["waves"][0]["branches"][1]
        prior.update({"integrationState": "worker-verified", "mergeSha": None, "controllerCheckDigest": None, "postCheckExpectedSha": None})
        two_wave["waves"][1]["startExpectedSha"] = "c" * 40
        two_wave["waves"][1]["branches"][0]["preMergeExpectedSha"] = "c" * 40
        two_wave["expectedIntegrationSha"] = "c" * 40
        two_wave["lastVerifiedIntegrationSha"] = "c" * 40
        self.assert_invalid("run-state", two_wave, "prior wave")
    def test_state_labels_cannot_overclaim_ledger_progress_and_blockers_keep_evidence(self):
        def reset_entry(entry, worker="pending"):
            entry.update({
                "workerState": worker, "verificationState": "pending", "integrationState": "pending",
                "preMergeExpectedSha": "a" * 40, "mergeSha": None,
                "controllerCheckDigest": None, "postCheckExpectedSha": None,
            })
        def blocker(blocker_id, blocked_from, phase, story_id, resume):
            return {
                "blockerId": blocker_id,
                "blockedFromState": blocked_from,
                "phase": phase,
                "storyId": story_id,
                "reason": f"{phase} could not proceed",
                "evidenceDigest": "sha256:" + "f" * 64,
                "resumeState": resume,
            }
        planned = fixture("run-state")
        planned.update({
            "previousState": None, "state": "planned",
            "expectedIntegrationSha": "a" * 40,
            "lastVerifiedIntegrationSha": "a" * 40,
        })
        for entry in planned["waves"][0]["branches"]:
            reset_entry(entry)
        normalize_contract("run-state", planned)
        dispatching = copy.deepcopy(planned)
        dispatching.update({"previousState": "planned", "state": "dispatching"})
        dispatching["waves"][0]["branches"][0]["workerState"] = "running"
        normalize_contract("run-state", dispatching)
        workers_complete = copy.deepcopy(planned)
        workers_complete.update({"previousState": "dispatching", "state": "wave-workers-complete"})
        for entry in workers_complete["waves"][0]["branches"]:
            entry["workerState"] = "complete"
        normalize_contract("run-state", workers_complete)
        validate_run_state_transition(planned, dispatching)
        validate_run_state_transition(dispatching, workers_complete)
        merging = copy.deepcopy(workers_complete)
        merging.update({"previousState": "wave-workers-complete", "state": "wave-merging"})
        for entry in merging["waves"][0]["branches"]:
            entry.update({"verificationState": "verified", "integrationState": "worker-verified"})
        validate_run_state_transition(workers_complete, merging)
        overclaimed = fixture("run-state")
        overclaimed.update({"previousState": "planned", "state": "dispatching"})
        self.assert_invalid("run-state", overclaimed, "dispatching")
        overclaimed = copy.deepcopy(workers_complete)
        overclaimed["waves"][0]["branches"][0].update({"verificationState": "verified", "integrationState": "worker-verified"})
        self.assert_invalid("run-state", overclaimed, "wave-workers-complete")
        worker_blocker = blocker("block-worker-001", "dispatching", "worker", "alpha", "dispatching")
        blocked = copy.deepcopy(planned)
        blocked.update({
            "previousState": "dispatching", "state": "blocked",
            "activeBlocker": copy.deepcopy(worker_blocker),
            "blockerHistory": [copy.deepcopy(worker_blocker)],
        })
        blocked["waves"][0]["branches"][0].update({
            "workerState": "blocked", "integrationState": "blocked",
        })
        normalize_contract("run-state", blocked)
        missing = copy.deepcopy(blocked)
        missing["activeBlocker"] = None
        self.assert_invalid("run-state", missing, "activeBlocker")
        resumed = copy.deepcopy(blocked)
        resumed.update({"previousState": "blocked", "state": "dispatching", "activeBlocker": None})
        reset_entry(resumed["waves"][0]["branches"][0], "running")
        normalize_contract("run-state", resumed)
        validate_run_state_transition(blocked, resumed)
        rewritten = copy.deepcopy(resumed)
        rewritten["blockerHistory"][0]["blockerId"] = "block-worker-rewritten"
        with self.assertRaisesRegex(ContractValidationError, "rewrites prior blocker"):
            validate_run_state_transition(blocked, rewritten)
        wrong_resume = copy.deepcopy(resumed)
        wrong_resume["state"] = "wave-workers-complete"
        self.assert_invalid("run-state", wrong_resume, "resumeState")
        controller = blocker("block-controller-001", "planned", "controller", None, "planned")
        controller_blocked = copy.deepcopy(planned)
        controller_blocked.update({
            "previousState": "planned", "state": "blocked",
            "activeBlocker": copy.deepcopy(controller),
            "blockerHistory": [copy.deepcopy(controller)],
        })
        normalize_contract("run-state", controller_blocked)
        bad_reason = copy.deepcopy(controller_blocked)
        bad_reason["activeBlocker"]["reason"] = " "
        bad_reason["blockerHistory"][-1]["reason"] = " "
        self.assert_invalid("run-state", bad_reason, ".reason")
        mismatched = copy.deepcopy(controller_blocked)
        mismatched["activeBlocker"]["reason"] = "different controller blocker"
        self.assert_invalid("run-state", mismatched, "exact last blockerHistory")
        pre_merge = blocker("block-premerge-001", "wave-merging", "pre-merge", "alpha", "wave-merging")
        pre_merge_blocked = copy.deepcopy(planned)
        pre_merge_blocked.update({
            "previousState": "wave-merging", "state": "blocked",
            "activeBlocker": copy.deepcopy(pre_merge),
            "blockerHistory": [copy.deepcopy(pre_merge)],
        })
        pre_merge_blocked["waves"][0]["branches"][0].update({
            "workerState": "complete", "verificationState": "verified", "integrationState": "blocked",
        })
        pre_merge_blocked["waves"][0]["branches"][1].update({
            "workerState": "complete", "verificationState": "verified", "integrationState": "worker-verified",
        })
        normalize_contract("run-state", pre_merge_blocked)
    def test_post_merge_blocker_retains_actual_head_and_resumes_check_without_remerge(self):
        initial = "a" * 40
        merge_sha = "c" * 40
        check_digest = "sha256:" + "d" * 64
        record = {
            "blockerId": "block-postcheck-001",
            "blockedFromState": "wave-integrated-unverified",
            "phase": "post-merge-check",
            "storyId": "alpha",
            "reason": "controller check failed after merge",
            "evidenceDigest": check_digest,
            "resumeState": "wave-integrated-unverified",
        }
        blocked = fixture("run-state")
        blocked.update({
            "previousState": "wave-integrated-unverified",
            "state": "blocked",
            "expectedIntegrationSha": merge_sha,
            "lastVerifiedIntegrationSha": initial,
            "activeBlocker": copy.deepcopy(record),
            "blockerHistory": [copy.deepcopy(record)],
        })
        first, second = blocked["waves"][0]["branches"]
        first.update({
            "workerState": "complete", "verificationState": "verified", "integrationState": "blocked",
            "preMergeExpectedSha": initial, "mergeSha": merge_sha,
            "controllerCheckDigest": check_digest, "postCheckExpectedSha": None,
        })
        second.update({
            "workerState": "complete", "verificationState": "verified", "integrationState": "worker-verified",
            "preMergeExpectedSha": initial, "mergeSha": None,
            "controllerCheckDigest": None, "postCheckExpectedSha": None,
        })
        normalize_contract("run-state", blocked); self.assertEqual(blocked["blockerHistory"][-1]["evidenceDigest"], check_digest)
        before_block = copy.deepcopy(blocked)
        before_block.update({
            "previousState": "wave-merging", "state": "wave-integrated-unverified",
            "activeBlocker": None, "blockerHistory": [],
        })
        before_block["waves"][0]["branches"][0].update({
            "integrationState": "merged", "controllerCheckDigest": None,
        })
        validate_run_state_transition(before_block, blocked)
        stale_actual = copy.deepcopy(blocked)
        stale_actual["expectedIntegrationSha"] = initial
        self.assert_invalid("run-state", stale_actual, "actual integration CAS HEAD")
        oververified = copy.deepcopy(blocked)
        oververified["lastVerifiedIntegrationSha"] = merge_sha
        self.assert_invalid("run-state", oververified, "last clean controller-verified HEAD")
        resumed = copy.deepcopy(blocked)
        resumed.update({
            "previousState": "blocked",
            "state": "wave-integrated-unverified",
            "activeBlocker": None,
        })
        resumed["waves"][0]["branches"][0]["integrationState"] = "merged"
        normalize_contract("run-state", resumed)
        validate_run_state_transition(blocked, resumed)
        self.assertEqual(resumed["waves"][0]["branches"][0]["controllerCheckDigest"], check_digest)
        self.assert_invalid("run-state", passing_resume(resumed, check_digest), "known failed")
        fresh = passing_resume(resumed, "sha256:" + "e" * 64)
        normalize_contract("run-state", fresh)
        validate_run_state_transition(resumed, fresh)
        self.assertEqual(fresh["blockerHistory"], resumed["blockerHistory"])
        rewritten_resume = copy.deepcopy(resumed)
        rewritten_resume["waves"][0]["branches"][0]["mergeSha"] = "d" * 40
        rewritten_resume["expectedIntegrationSha"] = "d" * 40
        normalize_contract("run-state", rewritten_resume)
        with self.assertRaisesRegex(ContractValidationError, "expectedIntegrationSha.*changed"):
            validate_run_state_transition(blocked, rewritten_resume)
        wrong = copy.deepcopy(resumed)
        wrong["state"] = "wave-merging"
        self.assert_invalid("run-state", wrong, "resumeState")
        controller_record = {
            "blockerId": "block-controller-verified-001", "blockedFromState": "wave-verified",
            "phase": "controller", "storyId": None, "reason": "controller paused",
            "evidenceDigest": "sha256:" + "9" * 64, "resumeState": "wave-verified",
        }
        verified = fixture("run-state")
        verified.update({"previousState": "wave-integrated-unverified", "state": "wave-verified"})
        controller_blocked = copy.deepcopy(verified)
        controller_blocked.update({"previousState": "wave-verified", "state": "blocked", "activeBlocker": copy.deepcopy(controller_record), "blockerHistory": [copy.deepcopy(controller_record)]})
        validate_run_state_transition(verified, controller_blocked)
        controller_resumed = copy.deepcopy(controller_blocked)
        controller_resumed.update({"previousState": "blocked", "state": "wave-verified", "activeBlocker": None})
        first, second = controller_resumed["waves"][0]["branches"]
        first.update({"mergeSha": "d" * 40, "postCheckExpectedSha": "d" * 40})
        second["preMergeExpectedSha"] = "d" * 40
        normalize_contract("run-state", controller_resumed)
        with self.assertRaisesRegex(ContractValidationError, "rewrites durable SHA-chain"):
            validate_run_state_transition(controller_blocked, controller_resumed)
    def test_worker_terminal_status_must_match_all_check_evidence(self):
        bad = fixture("worker-receipt"); bad["checks"][0]["status"] = "failed"
        self.assert_invalid("worker-receipt", bad, "succeeded requires every check")
        for status in ("failed", "blocked", "timed-out"):
            early = fixture("worker-receipt")
            early.update({"status": status, "blocker": f"{status} before evidence", "headSha": None, "commitSha": None, "changedFiles": [], "checks": []})
            normalize_contract("worker-receipt", early)
        bad = fixture("worker-receipt"); bad["headSha"] = None; bad["commitSha"] = None
        self.assert_invalid("worker-receipt", bad, "succeeded requires")
        bad = fixture("worker-receipt"); bad["changedFiles"] = []
        self.assert_invalid("worker-receipt", bad, "changedFiles")
        bad = fixture("worker-receipt"); bad["checks"] = []
        self.assert_invalid("worker-receipt", bad, "checks")
        bad = fixture("worker-receipt"); bad["changedFiles"][0]["sourcePath"] = "src/old.py"
        self.assert_invalid("worker-receipt", bad, "only valid for a rename")
    def test_benchmark_digests_warmups_aggregate_ledger_and_receipt_binding(self):
        bad = fixture("benchmark-receipt"); bad["controls"]["orderedStorySetDigest"] = "sha256:" + "0" * 64
        self.assert_invalid("benchmark-receipt", bad, "orderedStorySetDigest")
        bad = fixture("benchmark-receipt"); bad["controls"]["acceptanceCheckDigest"] = "sha256:" + "0" * 64
        self.assert_invalid("benchmark-receipt", bad, "acceptanceCheckDigest")
        bad = fixture("benchmark-workloads"); bad["workloads"][0]["warmups"].reverse()
        self.assert_invalid("benchmark-workloads", bad, "warmups[0].arm")
        bad = fixture("benchmark-aggregate"); bad["controlsDigest"] = "sha256:" + "0" * 64
        self.assert_invalid("benchmark-aggregate", bad, "controlsDigest")
        bad = fixture("benchmark-aggregate"); del bad["eventLedger"]["terminalHash"]
        self.assert_invalid("benchmark-aggregate", bad, "eventLedger")
        bad = fixture("benchmark-aggregate"); bad["eventLedger"]["lastSequence"] = 0
        self.assert_invalid("benchmark-aggregate", bad, "lastSequence")
        aggregate, receipts = bound_aggregate_and_receipts()
        validate_benchmark_aggregate_receipts(aggregate, receipts)
        mismatched = copy.deepcopy(receipts); mismatched[3]["attemptId"] = "different-attempt"
        with self.assertRaisesRegex(ContractValidationError, r"receipts\[3\]"):
            validate_benchmark_aggregate_receipts(aggregate, mismatched)
        duplicated = copy.deepcopy(receipts); duplicated[1] = copy.deepcopy(duplicated[0])
        with self.assertRaisesRegex(ContractValidationError, "duplicates a receipt"):
            validate_benchmark_aggregate_receipts(aggregate, duplicated)
        stale_aggregate, stale_receipts = bound_aggregate_and_receipts()
        new_spec_digest = "sha256:" + "9" * 64
        stale_aggregate["workloadManifest"]["workloads"][0]["specDigest"] = new_spec_digest
        stale_aggregate["workloadManifestDigest"] = digest_value(stale_aggregate["workloadManifest"])
        stale_aggregate["workloadControls"][0]["controls"]["specDigest"] = new_spec_digest
        stale_aggregate["controlsDigest"] = digest_value(stale_aggregate["workloadControls"])
        with self.assertRaisesRegex(ContractValidationError, r"receipts\[0\]\.controls"):
            validate_benchmark_aggregate_receipts(stale_aggregate, stale_receipts)
    def test_schema_structure_is_closed_and_matches_required_keys(self):
        def walk(node, path):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertIs(node.get("additionalProperties"), False, path)
                    self.assertEqual(set(node.get("required", [])), set(node.get("properties", {})), path)
                if node.get("type") == "array":
                    self.assertIn("maxItems", node, path)
                for key, child in node.items():
                    walk(child, f"{path}.{key}")
            elif isinstance(node, list):
                for index, child in enumerate(node):
                    walk(child, f"{path}[{index}]")
        for contract in CONTRACTS:
            schema = json.loads((SCHEMAS / f"{contract}.schema.json").read_text(encoding="utf-8"))
            walk(schema, contract)

        nested_mutations = []
        bad = fixture("run-spec"); bad["stories"][0]["sharedState"]["extra"] = True; nested_mutations.append(("run-spec", bad, "sharedState"))
        bad = fixture("wave-plan"); del bad["stories"][0]["handoffDigest"]; nested_mutations.append(("wave-plan", bad, "stories[0]"))
        bad = fixture("run-state"); del bad["waves"][0]["branches"][0]["mergeSha"]; nested_mutations.append(("run-state", bad, "branches[0]"))
        bad = fixture("worker-receipt"); bad["checks"][0]["extra"] = True; nested_mutations.append(("worker-receipt", bad, "checks[0]"))
        bad = fixture("benchmark-receipt"); del bad["controls"]["environmentDigest"]; nested_mutations.append(("benchmark-receipt", bad, "controls"))
        bad = fixture("benchmark-workloads"); del bad["workloads"][0]["warmups"][0]["attemptId"]; nested_mutations.append(("benchmark-workloads", bad, "warmups[0]"))
        bad = fixture("benchmark-aggregate"); bad["workloadControls"][0]["extra"] = True; nested_mutations.append(("benchmark-aggregate", bad, "workloadControls[0]"))
        for contract, bad, path in nested_mutations:
            self.assert_invalid(contract, bad, path)

        bad = fixture("run-spec"); bad["stories"][0]["title"] = "x" * 161
        self.assert_invalid("run-spec", bad, ".title")
        bad = fixture("run-spec"); bad["integrationBranch"] = "-unsafe"
        self.assert_invalid("run-spec", bad, "integrationBranch")
        bad = fixture("host-capabilities"); bad["selectedModel"] = "x" * 161
        self.assert_invalid("host-capabilities", bad, "selectedModel")
        bad = fixture("benchmark-receipt"); bad["arm"] = "automatic"
        self.assert_invalid("benchmark-receipt", bad, "$.arm")
    def test_run_state_wire_exposes_head_and_top_level_blocker_history_only(self):
        schema = json.loads((SCHEMAS / "run-state.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(set(schema["required"]), {
            "schemaVersion", "runId", "baseSha", "integrationBranch",
            "initialIntegrationSha", "expectedIntegrationSha",
            "lastVerifiedIntegrationSha", "runBindingDigest", "previousState",
            "state", "currentWaveIndex", "activeBlocker", "blockerHistory", "waves",
        })
        self.assertEqual(set(schema["$defs"]["blocker"]["required"]), {
            "blockerId", "blockedFromState", "phase", "storyId", "reason",
            "evidenceDigest", "resumeState",
        })
        self.assertEqual(set(schema["$defs"]["ledger"]["required"]), {
            "storyId", "branch", "workerState", "verificationState",
            "integrationState", "preMergeExpectedSha", "mergeSha",
            "controllerCheckDigest", "postCheckExpectedSha",
        })
        reason_rule = schema["$defs"]["blocker"]["properties"]["reason"]
        for value in ("", " ", " leading", "trailing ", "x" * 2001):
            self.assertFalse(schema_allows_string(schema, reason_rule, value))
    def test_schema_string_rules_compile_and_reject_the_same_adversarial_values(self):
        run_schema = json.loads((SCHEMAS / "run-spec.schema.json").read_text(encoding="utf-8"))
        worker_schema = json.loads((SCHEMAS / "worker-receipt.schema.json").read_text(encoding="utf-8"))
        story = run_schema["$defs"]["story"]["properties"]
        targets = [
            (run_schema, run_schema["properties"]["validationCommands"]["items"], "run-spec", lambda value, text: value["validationCommands"].__setitem__(0, text)),
            (run_schema, story["title"], "run-spec", lambda value, text: value["stories"][0].__setitem__("title", text)),
            (run_schema, story["description"], "run-spec", lambda value, text: value["stories"][0].__setitem__("description", text)),
            (run_schema, story["acceptanceChecks"]["items"], "run-spec", lambda value, text: value["stories"][0]["acceptanceChecks"].__setitem__(0, text)),
            (run_schema, story["validationCommands"]["items"], "run-spec", lambda value, text: value["stories"][0]["validationCommands"].__setitem__(0, text)),
            (run_schema, story["independentReviewPath"], "run-spec", lambda value, text: value["stories"][0].__setitem__("independentReviewPath", text)),
            (run_schema, story["sharedState"]["properties"]["description"], "run-spec", lambda value, text: value["stories"][0]["sharedState"].__setitem__("description", text)),
            (worker_schema, worker_schema["properties"]["blocker"], "worker-receipt", lambda value, text: value.__setitem__("blocker", text)),
        ]
        for schema, node, contract, mutate in targets:
            rule = resolved_schema_node(schema, node)
            with self.subTest(contract=contract, bound=rule.get("maxLength")):
                self.assertIn("minLength", rule)
                self.assertIn("maxLength", rule)
                re.compile(rule["pattern"])
            candidates = ("", " ", " leading", "trailing ", "trailing\n", "x" * (rule["maxLength"] + 1))
            for candidate in candidates:
                with self.subTest(contract=contract, candidate=repr(candidate[:20])):
                    self.assertFalse(schema_allows_string(schema, node, candidate))
                    value = fixture(contract)
                    if contract == "worker-receipt":
                        value["status"] = "failed"
                        value["checks"][0]["status"] = "failed"
                    mutate(value, candidate)
                    self.assert_invalid(contract, value, "$")

        description = run_schema["properties"]["stories"]["description"].lower()
        self.assertIn("topologically", description)
        self.assertIn("earlier", description)


if __name__ == "__main__":
    unittest.main()
