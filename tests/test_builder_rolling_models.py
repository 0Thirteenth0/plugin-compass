from __future__ import annotations

import copy
import json
import re
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "compass-builder"
SCHEMAS = PLUGIN_ROOT / "schemas"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "compass_builder" / "rolling"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from compass_builder import models as rolling_models  # noqa: E402
from compass_builder.models import ContractValidationError  # noqa: E402


CONTRACTS = {
    "run-spec-v2": ("compass-builder.run-spec.v2", "run-spec.v2.schema.json"),
    "pipeline-plan": ("compass-builder.pipeline-plan.v2", "pipeline-plan.schema.json"),
    "pipeline-state": ("compass-builder.pipeline-state.v2", "pipeline-state.schema.json"),
    "pipeline-event": ("compass-builder.pipeline-event.v2", "pipeline-event.schema.json"),
    "execution-bundle-v2": (
        "compass-builder.execution-bundle.v2",
        "execution-bundle.v2.schema.json",
    ),
    "dispatch-record": (
        "compass-builder.dispatch-record.v2",
        "dispatch-record.schema.json",
    ),
}


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.valid.json").read_text(encoding="utf-8"))


class BuilderRollingModelTests(unittest.TestCase):
    def require_g1(self) -> None:
        missing = [
            contract
            for contract, (version, schema_name) in CONTRACTS.items()
            if rolling_models.SCHEMA_VERSIONS.get(contract) != version
            or contract not in rolling_models.VALIDATORS
            or not (SCHEMAS / schema_name).is_file()
            or not (FIXTURES / f"{contract}.valid.json").is_file()
        ]
        self.assertEqual([], missing, f"G1 contracts are not registered: {missing}")
        for name in (
            "validate_rolling_plan_bindings",
            "validate_rolling_state_bindings",
            "validate_pipeline_event_chain",
            "validate_dispatch_record_bindings",
            "validate_rolling_execution_bundle",
        ):
            self.assertTrue(hasattr(rolling_models, name), f"missing public semantic validator {name}")

    def assert_invalid(self, contract: str, value: dict, field: str) -> None:
        with self.assertRaises(ContractValidationError) as raised:
            rolling_models.normalize_contract(contract, value)
        self.assertIn(field, str(raised.exception))
        self.assertIn("corrective direction:", str(raised.exception))

    def test_contracts_are_registered_and_fixtures_round_trip_canonically(self):
        self.require_g1()
        for contract, (version, schema_name) in CONTRACTS.items():
            with self.subTest(contract=contract):
                path = FIXTURES / f"{contract}.valid.json"
                value = fixture(contract)
                schema = json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))
                self.assertEqual(version, value["schemaVersion"])
                self.assertEqual(version, schema["properties"]["schemaVersion"]["const"])
                self.assertEqual(value, rolling_models.normalize_contract(contract, value))
                self.assertEqual(path.read_bytes(), rolling_models.canonical_json(value, contract))

    def test_every_rolling_schema_is_closed_bounded_and_matches_fixture_shape(self):
        self.require_g1()

        def walk(node, path):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertIs(node.get("additionalProperties"), False, path)
                    self.assertEqual(
                        set(node.get("required", [])),
                        set(node.get("properties", {})),
                        path,
                    )
                if node.get("type") == "array":
                    self.assertIn("maxItems", node, path)
                for key, child in node.items():
                    walk(child, f"{path}.{key}")
            elif isinstance(node, list):
                for index, child in enumerate(node):
                    walk(child, f"{path}[{index}]")

        for contract, (_, schema_name) in CONTRACTS.items():
            with self.subTest(contract=contract):
                schema = json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))
                self.assertEqual("https://json-schema.org/draft/2020-12/schema", schema["$schema"])
                self.assertEqual(set(fixture(contract)), set(schema["required"]))
                walk(schema, contract)

    def test_every_rolling_schema_reference_resolves_within_the_schema_root(self):
        self.require_g1()
        schema_root = SCHEMAS.resolve()

        def references(node):
            if isinstance(node, dict):
                if "$ref" in node:
                    yield node["$ref"]
                for child in node.values():
                    yield from references(child)
            elif isinstance(node, list):
                for child in node:
                    yield from references(child)

        pending = [SCHEMAS / schema_name for _, schema_name in CONTRACTS.values()]
        visited: set[Path] = set()
        while pending:
            schema_path = pending.pop().resolve()
            self.assertEqual(schema_root, schema_path.parent)
            if schema_path in visited:
                continue
            visited.add(schema_path)
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            for reference in references(schema):
                file_part, separator, fragment = reference.partition("#")
                target_path = (
                    schema_path if not file_part else (schema_path.parent / file_part).resolve()
                )
                self.assertEqual(schema_root, target_path.parent, reference)
                self.assertTrue(target_path.is_file(), reference)
                target = json.loads(target_path.read_text(encoding="utf-8"))
                if separator and fragment:
                    self.assertTrue(fragment.startswith("/"), reference)
                    for token in fragment[1:].split("/"):
                        token = token.replace("~1", "/").replace("~0", "~")
                        self.assertIn(token, target, reference)
                        target = target[token]
                if target_path not in visited:
                    pending.append(target_path)

    def test_run_spec_v2_is_closed_topological_and_requires_explicit_rolling_authorization(self):
        self.require_g1()
        valid = fixture("run-spec-v2")
        self.assertEqual(valid, rolling_models.normalize_contract("run-spec-v2", valid))
        mutations = {
            "unknown field": lambda value: value.update(extra=True),
            "$.dispatchStrategy": lambda value: value.update(dispatchStrategy="automatic"),
            "$.experimentalRollingAuthorized": lambda value: value.update(
                experimentalRollingAuthorized=False
            ),
            "$.exactModel": lambda value: value.update(exactModel="inherit"),
            "$.stories[0].requiredOutcomeGateIds": lambda value: value["stories"][0].update(
                requiredOutcomeGateIds=["gate-alpha", "gate-alpha"]
            ),
            "$.stories[1].dependsOn": lambda value: value["stories"][1].update(
                dependsOn=["missing"]
            ),
            "$.stories[0].writeScopes": lambda value: value["stories"][0].update(
                writeScopes=["C:/escape"]
            ),
        }
        for field, mutate in mutations.items():
            with self.subTest(field=field):
                candidate = copy.deepcopy(valid)
                mutate(candidate)
                self.assert_invalid("run-spec-v2", candidate, field)

    def test_pipeline_plan_binds_spec_order_dependencies_scopes_gates_and_ceiling(self):
        self.require_g1()
        spec = fixture("run-spec-v2")
        plan = fixture("pipeline-plan")
        validate = rolling_models.validate_rolling_plan_bindings
        normalized_spec, normalized_plan = validate(spec, plan)
        self.assertEqual(spec, normalized_spec)
        self.assertEqual(plan, normalized_plan)
        mutations = {
            "normalizedInputDigest": lambda value: value.update(
                normalizedInputDigest="sha256:" + "0" * 64
            ),
            "initialReadyStoryIds": lambda value: value.update(
                initialReadyStoryIds=["alpha", "beta"]
            ),
            "integrationOrdinal": lambda value: value["stories"][1].update(
                integrationOrdinal=1
            ),
            "dependsOn": lambda value: (
                value["stories"][1].update(dependsOn=[]),
                value.update(initialReadyStoryIds=["alpha", "beta"]),
            ),
            "writeScopes": lambda value: value["stories"][0].update(
                writeScopes=["src/wrong"]
            ),
            "concurrency": lambda value: value.update(concurrency=3),
        }
        for field, mutate in mutations.items():
            with self.subTest(field=field):
                candidate = copy.deepcopy(plan)
                mutate(candidate)
                with self.assertRaisesRegex(ContractValidationError, field):
                    validate(spec, candidate)

    def test_pipeline_plan_rejects_branch_collisions_and_parallel_shared_mutation(self):
        self.require_g1()
        spec = fixture("run-spec-v2")
        plan = fixture("pipeline-plan")
        bad_plan = copy.deepcopy(plan)
        bad_plan["stories"][1]["branch"] = "CB/cb-rolling-1111111111111111/alpha"
        with self.assertRaisesRegex(ContractValidationError, "branch"):
            rolling_models.validate_rolling_plan_bindings(spec, bad_plan)
        bad_spec = copy.deepcopy(spec)
        bad_spec["stories"][0]["sharedState"]["mode"] = "mutates"
        bound_plan = copy.deepcopy(plan)
        bound_plan["normalizedInputDigest"] = rolling_models.canonical_digest(bad_spec)
        with self.assertRaisesRegex(ContractValidationError, "sharedState"):
            rolling_models.validate_rolling_plan_bindings(bad_spec, bound_plan)

    def test_pipeline_state_binds_plan_and_rejects_incoherent_ownership_queue_or_terminal_state(self):
        self.require_g1()
        plan = fixture("pipeline-plan")
        state = fixture("pipeline-state")
        validate = rolling_models.validate_rolling_state_bindings
        normalized_plan, normalized_state = validate(plan, state)
        self.assertEqual(plan, normalized_plan)
        self.assertEqual(state, normalized_state)
        mutations = {
            "activeOwners": lambda value: value.update(activeOwners=[]),
            "integrationQueue": lambda value: value.update(integrationQueue=["alpha"]),
            "stories": lambda value: value["stories"][0].update(lifecycle="completed"),
            "state": lambda value: value.update(state="completed"),
            "planDigest": lambda value: value.update(planDigest="sha256:" + "0" * 64),
        }
        for field, mutate in mutations.items():
            with self.subTest(field=field):
                candidate = copy.deepcopy(state)
                mutate(candidate)
                with self.assertRaisesRegex(ContractValidationError, field):
                    validate(plan, candidate)

    def test_pipeline_state_rejects_run_state_drift_and_launch_before_dependency_integration(self):
        self.require_g1()
        plan = fixture("pipeline-plan")
        state = fixture("pipeline-state")
        bad = copy.deepcopy(state)
        bad["state"] = "planned"
        with self.assertRaisesRegex(ContractValidationError, "state"):
            rolling_models.validate_rolling_state_bindings(plan, bad)
        bad = copy.deepcopy(state)
        bad["previousState"] = "completed"
        with self.assertRaisesRegex(ContractValidationError, "previousState"):
            rolling_models.validate_rolling_state_bindings(plan, bad)
        bad = copy.deepcopy(state)
        beta = bad["stories"][1]
        beta.update(
            {
                "attempt": 1,
                "lifecycle": "running",
                "workerStartSha": "a" * 40,
                "registeredCloneDigest": "sha256:" + "8" * 64,
            }
        )
        bad["activeOwners"].append(
            {
                "storyId": "beta",
                "ownerId": "worker-beta-1",
                "writeScopes": ["src/beta"],
                "workerStartSha": "a" * 40,
                "registeredCloneDigest": "sha256:" + "8" * 64,
            }
        )
        with self.assertRaisesRegex(ContractValidationError, "dependsOn"):
            rolling_models.validate_rolling_state_bindings(plan, bad)

    def test_pipeline_state_rejects_premature_evidence_blocker_drift_and_unbound_shas(self):
        self.require_g1()
        plan = fixture("pipeline-plan")
        state = fixture("pipeline-state")
        for field, value in (
            ("integrationSha", "b" * 40),
            ("postCheckEvidenceDigest", "sha256:" + "f" * 64),
            ("gateEvidenceDigests", ["sha256:" + "f" * 64]),
        ):
            with self.subTest(field=field):
                bad = copy.deepcopy(state)
                bad["stories"][0][field] = value
                with self.assertRaisesRegex(ContractValidationError, field):
                    rolling_models.validate_rolling_state_bindings(plan, bad)
        bad = copy.deepcopy(state)
        bad["stories"][0].update(
            {"lifecycle": "blocked", "blockedFromLifecycle": "running"}
        )
        bad["activeOwners"] = []
        with self.assertRaisesRegex(ContractValidationError, "state"):
            rolling_models.validate_rolling_state_bindings(plan, bad)
        for field in ("currentIntegrationSha", "lastVerifiedIntegrationSha"):
            with self.subTest(field=field):
                bad = copy.deepcopy(state)
                bad[field] = "b" * 40
                with self.assertRaisesRegex(ContractValidationError, field):
                    rolling_models.validate_rolling_state_bindings(plan, bad)

    def test_pipeline_events_enforce_transition_semantics_and_append_only_chain(self):
        self.require_g1()
        event = fixture("pipeline-event")
        self.assertEqual(event, rolling_models.normalize_contract("pipeline-event", event))
        bad = copy.deepcopy(event)
        bad["stateAfter"] = "integration-verified"
        self.assert_invalid("pipeline-event", bad, "stateAfter")
        second = copy.deepcopy(event)
        second.update(
            {
                "eventId": "event-alpha-complete",
                "sequence": 2,
                "previousEventDigest": rolling_models.canonical_digest(event),
                "eventType": "completion",
                "stateBefore": "running",
                "stateAfter": "worker-complete-unverified",
            }
        )
        self.assertEqual(
            [event, second], rolling_models.validate_pipeline_event_chain([event, second])
        )
        broken = copy.deepcopy(second)
        broken["previousEventDigest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ContractValidationError, "previousEventDigest"):
            rolling_models.validate_pipeline_event_chain([event, broken])

    def test_pipeline_event_chain_rejects_missing_story_origin_and_terminal_block(self):
        self.require_g1()
        event = fixture("pipeline-event")
        completion = copy.deepcopy(event)
        completion.update(
            {
                "eventId": "event-alpha-complete",
                "eventType": "completion",
                "stateBefore": "running",
                "stateAfter": "worker-complete-unverified",
            }
        )
        with self.assertRaisesRegex(ContractValidationError, "stateBefore"):
            rolling_models.validate_pipeline_event_chain([completion])
        terminal_block = copy.deepcopy(event)
        terminal_block.update(
            {
                "eventId": "event-alpha-block",
                "eventType": "block",
                "stateBefore": "integration-verified",
                "stateAfter": "blocked",
            }
        )
        with self.assertRaisesRegex(ContractValidationError, "stateAfter"):
            rolling_models.normalize_contract("pipeline-event", terminal_block)

    def test_dispatch_record_binds_exact_story_start_sha_effort_scopes_and_prerequisites(self):
        self.require_g1()
        spec = fixture("run-spec-v2")
        plan = fixture("pipeline-plan")
        state = fixture("pipeline-state")
        record = fixture("dispatch-record")
        validate = rolling_models.validate_dispatch_record_bindings
        self.assertEqual(record, validate(spec, plan, state, record)[-1])
        mutations = {
            "workerStartSha": lambda value: value.update(workerStartSha="b" * 40),
            "exactModel": lambda value: value.update(exactModel="different-model"),
            "recommendedEffort": lambda value: value.update(recommendedEffort="low"),
            "writeScopes": lambda value: value.update(writeScopes=["src/wrong"]),
            "prerequisites": lambda value: value.update(prerequisites=[{"storyId": "beta", "workerReceiptDigest": "sha256:" + "1" * 64, "integrationEvidenceDigest": "sha256:" + "2" * 64, "gateEvidenceDigests": []}]),
        }
        for field, mutate in mutations.items():
            with self.subTest(field=field):
                candidate = copy.deepcopy(record)
                mutate(candidate)
                with self.assertRaisesRegex(ContractValidationError, field):
                    validate(spec, plan, state, candidate)
        for field, mutate in (
            ("attempt", lambda value: value.update(attempt=99)),
            (
                "registeredClone",
                lambda value: value["registeredClone"].update(cloneId="clone-other-1"),
            ),
            (
                "registeredClone",
                lambda value: value["registeredClone"].update(
                    gitCommonDirDigest="sha256:" + "f" * 64
                ),
            ),
        ):
            with self.subTest(field=field):
                candidate = copy.deepcopy(record)
                mutate(candidate)
                with self.assertRaisesRegex(ContractValidationError, field):
                    validate(spec, plan, state, candidate)

    def test_dispatch_record_binds_prerequisite_receipt_integration_and_gate_evidence(self):
        self.require_g1()
        spec = fixture("run-spec-v2")
        plan = fixture("pipeline-plan")
        state = fixture("pipeline-state")
        alpha, beta = state["stories"]
        alpha.update(
            {
                "lifecycle": "integration-verified",
                "workerReceiptDigest": "sha256:" + "1" * 64,
                "verificationEvidenceDigest": "sha256:" + "2" * 64,
                "importEvidenceDigest": "sha256:" + "3" * 64,
                "mergeIntentDigest": "sha256:" + "4" * 64,
                "integrationSha": "b" * 40,
                "postCheckEvidenceDigest": "sha256:" + "5" * 64,
                "gateEvidenceDigests": ["sha256:" + "6" * 64],
            }
        )
        beta.update(
            {
                "attempt": 1,
                "lifecycle": "running",
                "workerStartSha": "b" * 40,
                "registeredCloneDigest": "sha256:" + "8" * 64,
            }
        )
        state.update(
            {
                "activeOwners": [
                    {
                        "storyId": "beta",
                        "ownerId": "worker-beta-1",
                        "writeScopes": ["src/beta"],
                        "workerStartSha": "b" * 40,
                        "registeredCloneDigest": "sha256:" + "8" * 64,
                    }
                ],
                "currentIntegrationSha": "b" * 40,
                "lastVerifiedIntegrationSha": "b" * 40,
            }
        )
        record = fixture("dispatch-record")
        record.update(
            {
                "dispatchId": "dispatch-beta-1",
                "storyId": "beta",
                "workerStartSha": "b" * 40,
                "recommendedEffort": "medium",
                "writeScopes": ["src/beta"],
                "requiredOutcomeGateIds": ["gate-beta"],
                "handoffDigest": "sha256:" + "6" * 64,
                "prerequisites": [
                    {
                        "storyId": "alpha",
                        "workerReceiptDigest": "sha256:" + "1" * 64,
                        "integrationEvidenceDigest": "sha256:" + "5" * 64,
                        "gateEvidenceDigests": ["sha256:" + "6" * 64],
                    }
                ],
                "registeredClone": {
                    "cloneId": "clone-beta-1",
                    "repositoryRootDigest": "sha256:" + "8" * 64,
                    "gitCommonDirDigest": "sha256:" + "d" * 64,
                    "branch": "cb/cb-rolling-1111111111111111/beta",
                },
            }
        )
        clone_digest = rolling_models.canonical_digest(record["registeredClone"])
        state["stories"][1]["registeredCloneDigest"] = clone_digest
        state["activeOwners"][0]["registeredCloneDigest"] = clone_digest
        rolling_models.validate_dispatch_record_bindings(spec, plan, state, record)
        missing_gate_state = copy.deepcopy(state)
        missing_gate_state["stories"][0]["gateEvidenceDigests"] = []
        missing_gate_record = copy.deepcopy(record)
        missing_gate_record["prerequisites"][0]["gateEvidenceDigests"] = []
        with self.assertRaisesRegex(ContractValidationError, "requiredOutcomeGateIds"):
            rolling_models.validate_dispatch_record_bindings(
                spec, plan, missing_gate_state, missing_gate_record
            )
        record["prerequisites"][0]["workerReceiptDigest"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(ContractValidationError, "workerReceiptDigest"):
            rolling_models.validate_dispatch_record_bindings(spec, plan, state, record)

    def test_historical_dispatch_remains_valid_after_an_earlier_ordinal_integrates(self):
        self.require_g1()
        spec = fixture("run-spec-v2")
        spec["stories"][1]["dependsOn"] = []
        plan = fixture("pipeline-plan")
        plan["stories"][1]["dependsOn"] = []
        plan["initialReadyStoryIds"] = ["alpha", "beta"]
        plan["normalizedInputDigest"] = rolling_models.canonical_digest(spec)
        state = fixture("pipeline-state")
        plan_digest = rolling_models.canonical_digest(plan)
        state["planDigest"] = plan_digest
        alpha, beta = state["stories"]
        alpha.update(
            {
                "lifecycle": "integration-verified",
                "workerReceiptDigest": "sha256:" + "1" * 64,
                "verificationEvidenceDigest": "sha256:" + "2" * 64,
                "importEvidenceDigest": "sha256:" + "3" * 64,
                "mergeIntentDigest": "sha256:" + "4" * 64,
                "integrationSha": "b" * 40,
                "postCheckEvidenceDigest": "sha256:" + "5" * 64,
                "gateEvidenceDigests": ["sha256:" + "6" * 64],
            }
        )
        beta.update(
            {
                "attempt": 1,
                "lifecycle": "running",
                "workerStartSha": "a" * 40,
                "registeredCloneDigest": "sha256:" + "8" * 64,
            }
        )
        state.update(
            {
                "activeOwners": [
                    {
                        "storyId": "beta",
                        "ownerId": "worker-beta-1",
                        "writeScopes": ["src/beta"],
                        "workerStartSha": "a" * 40,
                        "registeredCloneDigest": "sha256:" + "8" * 64,
                    }
                ],
                "currentIntegrationSha": "b" * 40,
                "lastVerifiedIntegrationSha": "b" * 40,
            }
        )
        record = fixture("dispatch-record")
        record.update(
            {
                "dispatchId": "dispatch-beta-1",
                "storyId": "beta",
                "planDigest": plan_digest,
                "workerStartSha": "a" * 40,
                "recommendedEffort": "medium",
                "writeScopes": ["src/beta"],
                "requiredOutcomeGateIds": ["gate-beta"],
                "handoffDigest": "sha256:" + "6" * 64,
                "registeredClone": {
                    "cloneId": "clone-beta-1",
                    "repositoryRootDigest": "sha256:" + "8" * 64,
                    "gitCommonDirDigest": "sha256:" + "d" * 64,
                    "branch": "cb/cb-rolling-1111111111111111/beta",
                },
            }
        )
        clone_digest = rolling_models.canonical_digest(record["registeredClone"])
        state["stories"][1]["registeredCloneDigest"] = clone_digest
        state["activeOwners"][0]["registeredCloneDigest"] = clone_digest
        rolling_models.validate_dispatch_record_bindings(spec, plan, state, record)

    def test_execution_bundle_binds_fresh_host_spec_and_plan_without_authorizing_execution(self):
        self.require_g1()
        bundle = fixture("execution-bundle-v2")
        normalized = rolling_models.validate_rolling_execution_bundle(bundle)
        self.assertEqual(bundle, normalized)
        mutations = {
            "hostEvidenceDigest": lambda value: value["pipelinePlan"].update(
                hostEvidenceDigest="sha256:" + "0" * 64
            ),
            "selectedModel": lambda value: (
                value["hostCapabilities"].update(selectedModel="different-model"),
                value["pipelinePlan"].update(
                    hostEvidenceDigest=rolling_models.canonical_digest(
                        value["hostCapabilities"]
                    )
                ),
            ),
            "planningTimestamp": lambda value: value.update(
                planningTimestamp="2026-09-01T12:06:00Z"
            ),
        }
        for field, mutate in mutations.items():
            with self.subTest(field=field):
                candidate = copy.deepcopy(bundle)
                mutate(candidate)
                with self.assertRaisesRegex(ContractValidationError, field):
                    rolling_models.validate_rolling_execution_bundle(candidate)

    def test_public_bundle_normalizer_recursively_closes_nested_contracts(self):
        self.require_g1()
        bundle = fixture("execution-bundle-v2")
        bundle["runSpec"]["unexpected"] = True
        self.assert_invalid("execution-bundle-v2", bundle, "runSpec")

    def test_timestamp_schemas_match_python_rfc3339_rejection(self):
        self.require_g1()
        for schema_name, field in (
            ("pipeline-event.schema.json", "occurredAt"),
            ("execution-bundle.v2.schema.json", "planningTimestamp"),
        ):
            with self.subTest(schema=schema_name):
                schema = json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))
                rule = schema["properties"][field]
                self.assertIn("pattern", rule)
                self.assertIsNone(re.fullmatch(rule["pattern"], "not-a-date"))

    def test_parallel_execution_bundle_requires_all_native_isolation_controls(self):
        self.require_g1()
        bundle = fixture("execution-bundle-v2")
        bundle["hostCapabilities"]["supports"]["multiAgentDisable"] = False
        bundle["pipelinePlan"]["hostEvidenceDigest"] = rolling_models.canonical_digest(
            bundle["hostCapabilities"]
        )
        with self.assertRaisesRegex(ContractValidationError, "supports"):
            rolling_models.validate_rolling_execution_bundle(bundle)

    def test_v1_contract_versions_remain_frozen(self):
        self.require_g1()
        self.assertEqual("compass-builder.run-spec.v1", rolling_models.SCHEMA_VERSIONS["run-spec"])
        self.assertEqual("compass-builder.wave-plan.v1", rolling_models.SCHEMA_VERSIONS["wave-plan"])
        self.assertEqual("compass-builder.run-state.v1", rolling_models.SCHEMA_VERSIONS["run-state"])


if __name__ == "__main__":
    unittest.main()
