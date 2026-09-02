from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests.helpers.builder_models import schema_allows_string

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "plugins" / "compass-builder"
SCHEMAS = BUILDER / "schemas"
if str(BUILDER) not in sys.path:
    sys.path.insert(0, str(BUILDER))

from compass_builder.git_environment import (  # noqa: E402
    GitEnvironmentError, prepare_git_environment, validate_git_environment,
)
from compass_builder.launcher import (  # noqa: E402
    EXPECTED_WORKER_SCHEMA_DIGEST, FailureEvidence, LaunchError,
    REASONING_CONFIG_KEY, classify_failure, prepare_launch,
    prepare_retry_launch, validate_launch_record, validate_worker_output,
)


DIGEST = "sha256:" + "e" * 64


def load(name: str) -> dict:
    path = ROOT / "tests" / "fixtures" / "compass_builder" / f"{name}.valid.json"
    return json.loads(path.read_text(encoding="utf-8"))


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.worktree = self.base / "worker & literal path"
        self.worktree.mkdir()
        self.git_environment = prepare_git_environment(
            self.base / "controller-git",
            base_environment={
                "HOME": "C:/Users/example",
                "PATH": "C:/safe/bin",
                "GIT_DIR": "C:/attacker/repo",
                "git_config_global": "C:/attacker/config",
                "GIT_CONFIG_COUNT": "99",
            },
        )
        self.spec = load("run-spec")
        self.plan = load("wave-plan")
        self.host = load("host-capabilities")
        self.reasoning_digest = self.host["reasoningConfig"]["evidenceDigest"]

    def tearDown(self):
        self.temporary.cleanup()

    def first_launch(self):
        return prepare_launch(
            self.spec, self.plan, self.host,
            planning_timestamp="2026-09-01T12:01:00Z",
            story_id="alpha", worktree=self.worktree,
            worker_schema=SCHEMAS / "worker-output.schema.json",
            reasoning_config_key=REASONING_CONFIG_KEY,
            reasoning_config_evidence_digest=self.reasoning_digest,
            git_environment=self.git_environment,
            worker_start_sha=self.spec["baseSha"],
        )

    def test_prepares_exact_no_shell_argv_and_bounded_stdin_without_starting_worker(self):
        with patch("subprocess.run", side_effect=AssertionError("must not launch")):
            launch = self.first_launch()
        self.assertEqual(
            (
                "codex", "exec", "-C", str(self.worktree),
                "-m", self.spec["exactModel"],
                "-c", 'model_reasoning_effort="low"',
                "--disable", "multi_agent", "--ephemeral",
                "-s", "workspace-write", "--approve-for-me", "--json",
                "--output-schema", str(SCHEMAS / "worker-output.schema.json"), "-",
            ),
            launch.argv,
        )
        self.assertEqual(1, launch.argv.count(str(self.worktree)))
        self.assertNotIn("shell", launch.argv)
        self.assertNotIn("--add-dir", launch.argv)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", launch.argv)
        self.assertIn("Do not launch child workers or agents", launch.stdin)
        self.assertNotIn(launch.stdin, launch.argv)
        self.assertEqual("low", launch.record["effort"])
        self.assertEqual(self.spec["baseSha"], launch.record["workerStartSha"])
        self.assertEqual(self.plan["stories"][0]["handoffDigest"], launch.record["handoffDigest"])
        self.assertEqual(self.plan["hostEvidenceDigest"], launch.record["hostEvidenceDigest"])
        self.assertEqual(dict(launch.record), validate_launch_record(launch.record))

    def test_sanitized_git_environment_discards_caller_git_state_without_repurposing_home(self):
        environment = self.git_environment.environment
        self.assertEqual("C:/Users/example", environment["HOME"])
        self.assertEqual("C:/safe/bin", environment["PATH"])
        self.assertNotIn("GIT_DIR", environment)
        self.assertNotIn("git_config_global", environment)
        self.assertEqual("1", environment["GIT_CONFIG_NOSYSTEM"])
        self.assertEqual("1", environment["GIT_ATTR_NOSYSTEM"])
        self.assertEqual(str(self.git_environment.global_config), environment["GIT_CONFIG_GLOBAL"])
        self.assertNotIn("GIT_ATTR_GLOBAL", environment)
        self.assertEqual(b"", self.git_environment.global_config.read_bytes())
        self.assertEqual(b"", self.git_environment.global_attributes.read_bytes())
        self.assertEqual([], list(self.git_environment.template_directory.iterdir()))
        self.assertEqual([], list(self.git_environment.hooks_directory.iterdir()))
        pairs = {
            environment[f"GIT_CONFIG_KEY_{index}"]: environment[f"GIT_CONFIG_VALUE_{index}"]
            for index in range(int(environment["GIT_CONFIG_COUNT"]))
        }
        self.assertEqual("Compass Builder Worker", pairs["user.name"])
        self.assertEqual("compass-builder@localhost.invalid", pairs["user.email"])
        self.assertEqual("false", pairs["commit.gpgSign"])
        self.assertEqual("false", pairs["tag.gpgSign"])
        self.assertEqual("false", pairs["core.autocrlf"])
        self.assertEqual("lf", pairs["core.eol"])
        self.assertEqual("true", pairs["core.safecrlf"])
        self.assertEqual(
            str(self.git_environment.global_attributes), pairs["core.attributesFile"]
        )
        altered_environment = dict(environment)
        altered_environment["GIT_DIR"] = "C:/attacker/repo"
        altered = replace(self.git_environment, environment=altered_environment)
        with self.assertRaisesRegex(LaunchError, "altered or extended"):
            prepare_launch(
                self.spec, self.plan, self.host,
                planning_timestamp="2026-09-01T12:01:00Z",
                story_id="alpha", worktree=self.worktree,
                worker_schema=SCHEMAS / "worker-output.schema.json",
                reasoning_config_key=REASONING_CONFIG_KEY,
                reasoning_config_evidence_digest=self.reasoning_digest,
                git_environment=altered,
                worker_start_sha=self.spec["baseSha"],
            )

    def test_git_environment_fails_closed_on_nonempty_registered_surfaces(self):
        root = self.base / "unsafe-git"
        root.mkdir()
        (root / "empty-global.gitconfig").write_text("[user]\nname=caller\n", encoding="utf-8")
        with self.assertRaisesRegex(GitEnvironmentError, "not empty"):
            prepare_git_environment(root, base_environment={"HOME": "unchanged"})

    def test_git_environment_validation_never_repairs_deleted_isolation_files(self):
        self.git_environment.global_attributes.unlink()
        with self.assertRaisesRegex(GitEnvironmentError, "missing or unreadable"):
            validate_git_environment(self.git_environment)
        self.assertFalse(self.git_environment.global_attributes.exists())
        with self.assertRaisesRegex(LaunchError, "missing or unreadable"):
            self.first_launch()
        self.assertFalse(self.git_environment.global_attributes.exists())

    def test_invalid_host_model_config_key_handoff_or_argv_yields_no_launch_material(self):
        cases = []
        host = copy.deepcopy(self.host)
        host["selectedModel"] = "different-model"
        cases.append((self.spec, self.plan, host, REASONING_CONFIG_KEY, "execution bindings"))
        host = copy.deepcopy(self.host)
        host["supports"]["multiAgentDisable"] = False
        cases.append((self.spec, self.plan, host, REASONING_CONFIG_KEY, "execution bindings"))
        plan = copy.deepcopy(self.plan)
        plan["stories"][0]["handoffDigest"] = "not-a-digest"
        cases.append((self.spec, plan, self.host, REASONING_CONFIG_KEY, "execution bindings"))
        cases.append((self.spec, self.plan, self.host, "unverified.key", "config key"))
        for spec, plan, host, key, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(LaunchError, message):
                prepare_launch(
                    spec, plan, host,
                    planning_timestamp="2026-09-01T12:01:00Z",
                    story_id="alpha", worktree=self.worktree,
                    worker_schema=SCHEMAS / "worker-output.schema.json",
                    reasoning_config_key=key,
                    reasoning_config_evidence_digest=self.reasoning_digest,
                    git_environment=self.git_environment,
                    worker_start_sha=self.spec["baseSha"],
                )
        tampered = dict(self.first_launch().record)
        tampered["argv"] = list(tampered["argv"])
        tampered["argv"].insert(-1, "--add-dir")
        with self.assertRaisesRegex(LaunchError, "exact bounded"):
            validate_launch_record(tampered)
        with self.assertRaisesRegex(LaunchError, "native host proof"):
            prepare_launch(
                self.spec, self.plan, self.host,
                planning_timestamp="2026-09-01T12:01:00Z",
                story_id="alpha", worktree=self.worktree,
                worker_schema=SCHEMAS / "worker-output.schema.json",
                reasoning_config_key=REASONING_CONFIG_KEY,
                reasoning_config_evidence_digest=DIGEST,
                git_environment=self.git_environment,
                worker_start_sha=self.spec["baseSha"],
            )

    def test_worker_schema_is_pinned_to_bundled_canonical_closed_contract(self):
        arbitrary = self.base / "arbitrary-worker-schema.json"
        arbitrary.write_text(json.dumps({
            "properties": {
                "schemaVersion": {"const": "compass-builder.worker-output.v1"}
            }
        }), encoding="utf-8")
        with self.assertRaisesRegex(LaunchError, "bundled"):
            prepare_launch(
                self.spec, self.plan, self.host,
                planning_timestamp="2026-09-01T12:01:00Z",
                story_id="alpha", worktree=self.worktree,
                worker_schema=arbitrary,
                reasoning_config_key=REASONING_CONFIG_KEY,
                reasoning_config_evidence_digest=self.reasoning_digest,
                git_environment=self.git_environment,
                worker_start_sha=self.spec["baseSha"],
            )
        record = dict(self.first_launch().record)
        record["workerOutputSchemaPath"] = str(arbitrary)
        record["argv"] = list(record["argv"])
        record["argv"][-2] = str(arbitrary)
        with self.assertRaisesRegex(LaunchError, "bundled"):
            validate_launch_record(record)

    def test_launch_record_replay_rejects_worker_schema_tamper_after_prepare(self):
        record = dict(self.first_launch().record)
        schema_path = SCHEMAS / "worker-output.schema.json"
        original = schema_path.read_bytes()
        try:
            schema_path.write_text(json.dumps({
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {
                    "schemaVersion": {"const": "compass-builder.worker-output.v1"}
                },
            }), encoding="utf-8")
            with self.assertRaisesRegex(LaunchError, "closed shape|canonical digest"):
                validate_launch_record(record)
        finally:
            schema_path.write_bytes(original)
        self.assertEqual(record, validate_launch_record(record))

    def test_launch_record_schema_and_python_reject_the_same_local_string_hazards(self):
        schema = json.loads(
            (SCHEMAS / "launch-record.schema.json").read_text(encoding="utf-8")
        )
        record = dict(self.first_launch().record)
        cases = {
            "runId": "run-weak",
            "storyId": "UPPER",
            "branch": "bad branch",
            "exactModel": "bad model",
            "worktree": "relative/worktree",
            "workerOutputSchemaPath": "relative/schema.json",
        }
        for field, invalid in cases.items():
            with self.subTest(field=field):
                self.assertFalse(
                    schema_allows_string(schema, schema["properties"][field], invalid)
                )
                bad = copy.deepcopy(record)
                bad[field] = invalid
                with self.assertRaises(LaunchError):
                    validate_launch_record(bad)
        self.assertIn("strictly higher second-attempt effort", schema["$comment"])
        first_with_retry_claim = copy.deepcopy(record)
        first_with_retry_claim["previousLaunchDigest"] = DIGEST
        first_with_retry_claim["retryEvidenceDigest"] = DIGEST
        with self.assertRaisesRegex(LaunchError, "first attempt"):
            validate_launch_record(first_with_retry_claim)

    def test_launch_schema_enforces_first_effort_and_strict_retry_order(self):
        schema = json.loads(
            (SCHEMAS / "launch-record.schema.json").read_text(encoding="utf-8")
        )
        first = dict(self.first_launch().record)
        properties = schema["properties"]
        self.assertEqual("object", schema["type"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(first), set(schema["required"]))
        self.assertEqual(set(first), set(properties))
        self.assertEqual(
            {"const": "compass-builder.launch-record.v1"},
            properties["schemaVersion"],
        )
        self.assertEqual(first["schemaVersion"], properties["schemaVersion"]["const"])
        self.assertEqual({"const": REASONING_CONFIG_KEY}, properties["reasoningConfigKey"])
        self.assertEqual(REASONING_CONFIG_KEY, first["reasoningConfigKey"])
        self.assertEqual(
            {"const": EXPECTED_WORKER_SCHEMA_DIGEST},
            properties["workerOutputSchemaDigest"],
        )
        self.assertEqual(EXPECTED_WORKER_SCHEMA_DIGEST, first["workerOutputSchemaDigest"])

        def schema_nodes(value):
            if isinstance(value, dict):
                yield value
                for child in value.values():
                    yield from schema_nodes(child)
            elif isinstance(value, list):
                for child in value:
                    yield from schema_nodes(child)

        local_references = []
        for node in schema_nodes(schema):
            if "pattern" in node:
                self.assertIsNotNone(re.compile(node["pattern"]))
            reference = node.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/"):
                local_references.append(reference)
                resolved = schema
                for part in reference[2:].split("/"):
                    self.assertIn(part, resolved)
                    resolved = resolved[part]
                self.assertIsInstance(resolved, dict)
        self.assertTrue(local_references)

        argv = properties["argv"]
        self.assertEqual("array", argv["type"])
        self.assertEqual(18, argv["minItems"])
        self.assertEqual(18, argv["maxItems"])
        self.assertEqual(
            {"type": "string", "minLength": 1, "maxLength": 2048},
            argv["items"],
        )
        self.assertEqual(18, len(first["argv"]))

        attempts = {
            branch["properties"]["attempt"]["const"]: branch
            for branch in schema["oneOf"]
        }
        self.assertEqual(2, len(schema["oneOf"]))
        self.assertEqual({1, 2}, set(attempts))
        self.assertEqual(
            {"attempt", "previousLaunchDigest", "retryEvidenceDigest"},
            set(attempts[1]["properties"]),
        )
        self.assertEqual({"const": 1}, attempts[1]["properties"]["attempt"])
        self.assertEqual({"type": "null"}, attempts[1]["properties"]["previousLaunchDigest"])
        self.assertEqual({"type": "null"}, attempts[1]["properties"]["retryEvidenceDigest"])
        self.assertEqual(
            {"attempt", "previousLaunchDigest", "retryEvidenceDigest"},
            set(attempts[2]["properties"]),
        )
        self.assertEqual({"const": 2}, attempts[2]["properties"]["attempt"])
        self.assertEqual(
            {"$ref": "#/$defs/digest"},
            attempts[2]["properties"]["previousLaunchDigest"],
        )
        self.assertEqual(
            {"$ref": "#/$defs/digest"},
            attempts[2]["properties"]["retryEvidenceDigest"],
        )
        efforts = ("low", "medium", "high", "xhigh", "max", "ultra")
        first_rows = attempts[1]["allOf"][0]["oneOf"]
        self.assertEqual(len(efforts), len(first_rows))
        self.assertTrue(all(
            set(row) == {"properties"}
            and set(row["properties"]) == {"initialRecommendedEffort", "effort"}
            for row in first_rows
        ))
        first_order = {
            row["properties"]["initialRecommendedEffort"]["const"]:
                row["properties"]["effort"]
            for row in first_rows
        }
        self.assertEqual({effort: {"const": effort} for effort in efforts}, first_order)

        retry_rows = attempts[2]["allOf"][0]["oneOf"]
        self.assertEqual(len(efforts) - 1, len(retry_rows))
        self.assertTrue(all(
            set(row) == {"properties"}
            and set(row["properties"]) == {"initialRecommendedEffort", "effort"}
            for row in retry_rows
        ))
        retry_order = {
            row["properties"]["initialRecommendedEffort"]["const"]:
                row["properties"]["effort"]
            for row in retry_rows
        }
        expected_retry_order = {
            effort: {"enum": list(efforts[index + 1:])}
            for index, effort in enumerate(efforts[:-2])
        }
        expected_retry_order["max"] = {"const": "ultra"}
        self.assertEqual(expected_retry_order, retry_order)

        self.assertEqual(first, validate_launch_record(first))

        first_drift = copy.deepcopy(first)
        first_drift["effort"] = "medium"
        with self.assertRaisesRegex(LaunchError, "first attempt"):
            validate_launch_record(first_drift)

        retry = copy.deepcopy(first)
        retry.update({
            "attempt": 2,
            "effort": "medium",
            "previousLaunchDigest": DIGEST,
            "retryEvidenceDigest": DIGEST,
        })
        retry["argv"][7] = 'model_reasoning_effort="medium"'
        self.assertEqual(retry, validate_launch_record(retry))

        equal_retry = copy.deepcopy(retry)
        equal_retry["effort"] = "low"
        with self.assertRaisesRegex(LaunchError, "higher reasoning effort"):
            validate_launch_record(equal_retry)
        lower_retry = copy.deepcopy(retry)
        lower_retry.update({"initialRecommendedEffort": "medium", "effort": "low"})
        with self.assertRaisesRegex(LaunchError, "higher reasoning effort"):
            validate_launch_record(lower_retry)
        impossible_ultra_retry = copy.deepcopy(retry)
        impossible_ultra_retry.update({
            "initialRecommendedEffort": "ultra", "effort": "ultra"
        })
        with self.assertRaisesRegex(LaunchError, "higher reasoning effort"):
            validate_launch_record(impossible_ultra_retry)

    def test_only_controller_evidenced_reasoning_failure_gets_one_same_model_higher_effort_retry(self):
        first = self.first_launch()
        for kind in ("startup", "model", "config", "tool", "permission"):
            with self.subTest(kind=kind):
                decision = classify_failure(
                    first.record, FailureEvidence(kind, DIGEST), self.host["supportedEfforts"]
                )
                self.assertEqual("blocked", decision.status)
                self.assertIsNone(decision.retry_effort)
        untrusted = classify_failure(
            first.record, FailureEvidence("reasoning", DIGEST, source="worker"),
            self.host["supportedEfforts"],
        )
        self.assertEqual("blocked", untrusted.status)

        retry = prepare_retry_launch(
            self.spec, self.plan, self.host,
            planning_timestamp="2026-09-01T12:01:00Z",
            story_id="alpha", worktree=self.worktree,
            worker_schema=SCHEMAS / "worker-output.schema.json",
            reasoning_config_key=REASONING_CONFIG_KEY,
            reasoning_config_evidence_digest=self.reasoning_digest,
            git_environment=self.git_environment,
            previous_launch=first.record,
            failure_evidence=FailureEvidence("reasoning", DIGEST),
        )
        self.assertEqual(2, retry.record["attempt"])
        self.assertEqual("medium", retry.record["effort"])
        self.assertEqual(first.record["exactModel"], retry.record["exactModel"])
        self.assertEqual(first.record["handoffDigest"], retry.record["handoffDigest"])
        self.assertEqual('model_reasoning_effort="medium"', retry.argv[7])
        consumed = classify_failure(
            retry.record, FailureEvidence("reasoning", DIGEST), self.host["supportedEfforts"]
        )
        self.assertEqual("blocked", consumed.status)
        with self.assertRaisesRegex(LaunchError, "blocked"):
            prepare_retry_launch(
                self.spec, self.plan, self.host,
                planning_timestamp="2026-09-01T12:01:00Z",
                story_id="alpha", worktree=self.worktree,
                worker_schema=SCHEMAS / "worker-output.schema.json",
                reasoning_config_key=REASONING_CONFIG_KEY,
                reasoning_config_evidence_digest=self.reasoning_digest,
                git_environment=self.git_environment,
                previous_launch=retry.record,
                failure_evidence=FailureEvidence("reasoning", DIGEST),
            )
        drifted = dict(first.record)
        drifted["hostEvidenceDigest"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(LaunchError, "immutable first-attempt"):
            prepare_retry_launch(
                self.spec, self.plan, self.host,
                planning_timestamp="2026-09-01T12:01:00Z",
                story_id="alpha", worktree=self.worktree,
                worker_schema=SCHEMAS / "worker-output.schema.json",
                reasoning_config_key=REASONING_CONFIG_KEY,
                reasoning_config_evidence_digest=self.reasoning_digest,
                git_environment=self.git_environment,
                previous_launch=drifted,
                failure_evidence=FailureEvidence("reasoning", DIGEST),
            )

    def test_worker_output_and_both_new_schemas_are_closed_and_bounded(self):
        output = {
            "schemaVersion": "compass-builder.worker-output.v1",
            "status": "succeeded",
            "summary": "Implemented alpha and ran its focused check.",
            "acceptanceChecks": [{
                "check": "Alpha unit tests pass.", "status": "passed",
                "evidence": "python -m unittest tests.test_alpha -v exited 0.",
            }],
            "blocker": None,
        }
        self.assertEqual(output, validate_worker_output(output))
        bad = copy.deepcopy(output)
        bad["acceptanceChecks"][0]["status"] = "not-run"
        with self.assertRaisesRegex(LaunchError, "only passed"):
            validate_worker_output(bad)
        bad = copy.deepcopy(output)
        bad["reasoningFailure"] = True
        with self.assertRaisesRegex(LaunchError, "closed"):
            validate_worker_output(bad)

        for name in ("launch-record", "worker-output"):
            schema = json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))
            self.assertIs(schema["additionalProperties"], False)
            self.assertEqual(set(schema["required"]), set(schema["properties"]))
            for node in schema["properties"].values():
                if node.get("type") == "array":
                    self.assertIn("maxItems", node)


if __name__ == "__main__":
    unittest.main()
