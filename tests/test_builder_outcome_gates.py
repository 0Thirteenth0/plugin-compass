from __future__ import annotations

import copy
import json
import re
import sys
import unittest
from pathlib import Path

from tests.helpers.builder_models import schema_allows_string


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "compass-builder"
FIXTURE_PATH = (
    REPOSITORY_ROOT
    / "tests"
    / "fixtures"
    / "compass_builder"
    / "outcome-gate-ledger.valid.json"
)
SCHEMA_PATH = PLUGIN_ROOT / "schemas" / "outcome-gate-ledger.schema.json"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from compass_builder import models as model_api  # noqa: E402
from compass_builder.models import (  # noqa: E402
    ContractValidationError,
    SCHEMA_VERSIONS,
    canonical_json,
    normalize_contract,
)


def fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class OutcomeGateContractTests(unittest.TestCase):
    def assert_invalid(self, value: dict, field: str) -> None:
        with self.assertRaises(ContractValidationError) as raised:
            normalize_contract("outcome-gate-ledger", value)
        message = str(raised.exception)
        self.assertIn(field, message)
        self.assertIn("corrective direction:", message)

    def test_accepts_closed_versioned_controller_owned_ledger(self):
        value = fixture()
        self.assertEqual(
            "compass-builder.outcome-gate-ledger.v1",
            SCHEMA_VERSIONS.get("outcome-gate-ledger"),
        )
        self.assertTrue(callable(getattr(model_api, "validate_outcome_gate_ledger", None)))
        self.assertEqual(value, model_api.validate_outcome_gate_ledger(value))
        self.assertEqual(FIXTURE_PATH.read_bytes(), canonical_json(value, "outcome-gate-ledger"))

    def test_rejects_duplicate_gate_ids_and_missing_required_coverage(self):
        duplicate = fixture()
        duplicate["gates"][1]["id"] = duplicate["gates"][0]["id"]
        self.assert_invalid(duplicate, "$.gates")

        missing_requirement = fixture()
        missing_requirement["gates"][2]["coveredRequirementIds"] = []
        self.assert_invalid(missing_requirement, "R029")

        missing_acceptance = fixture()
        missing_acceptance["gates"][0]["coveredAcceptanceIds"] = []
        self.assert_invalid(missing_acceptance, "A020")

        uncovered_gate = fixture()
        uncovered_gate["gates"][0]["coveredRequirementIds"] = []
        uncovered_gate["gates"][0]["coveredAcceptanceIds"] = []
        self.assert_invalid(uncovered_gate, "coveredRequirementIds")

    def test_command_gate_requires_exact_runnable_fields_and_decisive_oracle(self):
        cases = (
            ("command", None, "command"),
            ("independentReviewPath", "docs/review.md", "independentReviewPath"),
            ("shell", None, "shell"),
            ("successMarker", "exit-code:0", "successMarker"),
            ("successMarker", "success", "successMarker"),
            ("validationStrength", "partial", "validationStrength"),
        )
        for field, replacement, expected in cases:
            with self.subTest(field=field):
                value = fixture()
                value["gates"][0][field] = replacement
                self.assert_invalid(value, expected)

        for no_op in ("true", ":", "exit 0", "echo success", "Write-Output success"):
            with self.subTest(command=no_op):
                value = fixture()
                value["gates"][0]["command"] = no_op
                self.assert_invalid(value, "command")

    def test_manual_review_gate_has_an_independent_path_and_no_command_shell(self):
        cases = (
            ("command", "python review.py", "command"),
            ("shell", "pwsh-7", "shell"),
            ("independentReviewPath", None, "independentReviewPath"),
            ("successMarker", "exit-code:0", "successMarker"),
            ("validationStrength", "none", "validationStrength"),
        )
        for field, replacement, expected in cases:
            with self.subTest(field=field):
                value = fixture()
                value["gates"][1][field] = replacement
                self.assert_invalid(value, expected)

        root_review = fixture()
        root_review["gates"][1]["independentReviewPath"] = "."
        self.assert_invalid(root_review, "independentReviewPath")

    def test_scope_and_execution_identity_are_explicit_and_portable(self):
        root_with_story = fixture()
        root_with_story["gates"][1]["storyId"] = "unexpected-story"
        self.assert_invalid(root_with_story, "storyId")

        story_without_story = fixture()
        story_without_story["gates"][0]["storyId"] = None
        self.assert_invalid(story_without_story, "storyId")

        for directory in ("/absolute", "C:/drive", "../escape", "src\\windows"):
            with self.subTest(directory=directory):
                value = fixture()
                value["gates"][0]["workingDirectory"] = directory
                self.assert_invalid(value, "workingDirectory")

        value = fixture()
        value["gates"][0]["environmentDigest"] = "mutable-environment-name"
        self.assert_invalid(value, "environmentDigest")

    def test_state_evidence_and_required_handoff_combinations_are_coherent(self):
        pending_with_evidence = fixture()
        pending = pending_with_evidence["gates"][1]
        pending.update(
            evidenceDigest="sha256:" + "5" * 64,
            validatedAt="2026-09-02T19:00:00Z",
            verificationRunId=pending_with_evidence["runId"],
        )
        self.assert_invalid(pending_with_evidence, "evidenceDigest")

        met_without_evidence = fixture()
        met_without_evidence["gates"][0]["evidenceDigest"] = None
        self.assert_invalid(met_without_evidence, "evidenceDigest")

        partial_evidence = fixture()
        partial_evidence["gates"][2]["evidenceDigest"] = "sha256:" + "5" * 64
        self.assert_invalid(partial_evidence, "validatedAt")

        for state in ("blocked", "abandoned"):
            with self.subTest(state=state):
                value = fixture()
                gate = value["gates"][2]
                gate.update(state=state, handoffReason=None)
                self.assert_invalid(value, "handoffReason")

        met_with_handoff = fixture()
        met_with_handoff["gates"][0]["handoffReason"] = "No handoff is needed."
        self.assert_invalid(met_with_handoff, "handoffReason")

        abandoned_with_evidence = fixture()
        gate = abandoned_with_evidence["gates"][2]
        gate.update(
            state="abandoned",
            evidenceDigest="sha256:" + "5" * 64,
            validatedAt="2026-09-02T19:00:00Z",
            verificationRunId=abandoned_with_evidence["runId"],
        )
        self.assert_invalid(abandoned_with_evidence, "evidenceDigest")

        optional_blocked = fixture()
        optional_blocked["gates"][2].update(
            required=False,
            handoffReason=None,
            coveredRequirementIds=["R031"],
            coveredAcceptanceIds=["A020"],
        )
        optional_blocked["requiredRequirementIds"].remove("R029")
        optional_blocked["requiredAcceptanceIds"].remove("A018")
        normalize_contract("outcome-gate-ledger", optional_blocked)

    def test_schema_and_semantic_validator_share_the_closed_wire_contract(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        value = fixture()
        self.assertEqual(set(schema["required"]), set(value))
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["$defs"]["gate"]["additionalProperties"])
        self.assertEqual(set(schema["$defs"]["gate"]["required"]), set(value["gates"][0]))
        self.assertEqual(schema["properties"]["controller"]["const"], "compass-builder")
        self.assertEqual(
            set(schema["$defs"]["gate"]["properties"]["state"]["enum"]),
            {"pending", "met", "unmet", "blocked", "abandoned"},
        )
        conditionals = json.dumps(schema["$defs"]["gate"]["allOf"], sort_keys=True)
        for token in (
            "command",
            "manual-review",
            "review-decision:approved",
            "evidenceDigest",
            "handoffReason",
        ):
            self.assertIn(token, conditionals)

        def compile_patterns(node):
            if isinstance(node, dict):
                if "pattern" in node:
                    re.compile(node["pattern"])
                for child in node.values():
                    compile_patterns(child)
            elif isinstance(node, list):
                for child in node:
                    compile_patterns(child)

        compile_patterns(schema)
        working_directory = schema["$defs"]["workingDirectory"]
        self.assertIn("repositoryPath", schema["$defs"])
        review_path = schema["$defs"]["repositoryPath"]
        self.assertTrue(schema_allows_string(schema, working_directory, "."))
        self.assertTrue(
            schema_allows_string(schema, working_directory, "plugins/compass-builder")
        )
        for invalid in ("/absolute", "C:/drive", "../escape", "src\\windows"):
            self.assertFalse(schema_allows_string(schema, working_directory, invalid))
        self.assertFalse(schema_allows_string(schema, review_path, "."))
        self.assertTrue(schema_allows_string(schema, review_path, "docs/review.md"))
        self.assertNotIn("exit-code:0", conditionals)

        mutations = []
        bad = fixture(); bad["controller"] = "worker"; mutations.append((bad, "controller"))
        bad = fixture(); bad["gates"][0]["unexpected"] = True; mutations.append((bad, "unexpected"))
        bad = fixture(); bad["gates"][0]["state"] = "complete"; mutations.append((bad, "state"))
        bad = fixture(); bad["gates"][0]["command"] = None; mutations.append((bad, "command"))
        bad = fixture(); bad["gates"][1]["command"] = "python review.py"; mutations.append((bad, "command"))
        bad = fixture(); bad["gates"][1]["evidenceDigest"] = "sha256:" + "5" * 64; mutations.append((bad, "validatedAt"))
        for bad, field in mutations:
            with self.subTest(field=field):
                self.assert_invalid(bad, field)


if __name__ == "__main__":
    unittest.main()
