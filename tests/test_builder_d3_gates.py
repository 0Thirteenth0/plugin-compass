from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "plugins" / "compass-builder"
if str(BUILDER) not in sys.path:
    sys.path.insert(0, str(BUILDER))

from compass_builder._validation import canonical_digest  # noqa: E402
from compass_builder.controller import ControllerError, execute_run  # noqa: E402
from compass_builder.durable_artifacts import ArtifactJournal  # noqa: E402
import compass_builder.execution_bundle as execution_bundle_module  # noqa: E402
from compass_builder.execution_bundle import build_execution_bundle, validate_execution_bundle  # noqa: E402
from compass_builder.models import canonical_json  # noqa: E402
from compass_builder.state import StateError, StateStore  # noqa: E402
try:  # RED harness: missing D3 owners must be an assertion failure, not import noise.
    from compass_builder.execution_bundle import build_gated_execution_bundle  # noqa: E402
    from compass_builder.gate_enforcement import (  # noqa: E402
        GateEnforcementError,
        OperatorGateProvider,
        enforce_scope_gates,
        require_gate_evidence_coverage,
    )
    from compass_builder.gate_evidence import GateEvidenceJournal, fold_gate_evidence  # noqa: E402
    from compass_builder._gate_evidence_models import validate_gate_evidence_receipt  # noqa: E402
    D3_API_AVAILABLE = True
except ImportError:
    build_gated_execution_bundle = None
    GateEnforcementError = Exception
    OperatorGateProvider = object
    enforce_scope_gates = None
    require_gate_evidence_coverage = None
    GateEvidenceJournal = None
    fold_gate_evidence = None
    D3_API_AVAILABLE = False
from compass_builder.gate_runner import (  # noqa: E402
    current_platform_identity,
    environment_digest,
    run_approved_gates,
)
from tests.test_builder_gate_runner import (  # noqa: E402
    approval,
    command_gate,
    manual_gate,
    trust,
    trusted_approval,
)
from tests.test_builder_state import fixture  # noqa: E402


NOW = "2026-09-02T20:01:00Z"


def pristine_ledger(run_id: str, *gates: dict) -> dict:
    return {
        "schemaVersion": "compass-builder.outcome-gate-ledger.v1",
        "controller": "compass-builder",
        "runId": run_id,
        "requiredRequirementIds": ["R100"],
        "requiredAcceptanceIds": ["A100"],
        "gates": list(gates),
    }


def git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments], check=True, capture_output=True,
        text=True, encoding="utf-8", shell=False,
    ).stdout.strip()


class _Operator(OperatorGateProvider):
    def __init__(
        self, gates: dict[str, dict], *, approved: bool = True,
        prefix: str = "decision", evidence_key: bytes = b"builder-test-provider-key",
        checkpoints: dict[str, dict] | None = None,
        initialized_runs: set[str] | None = None,
        command_reservations: dict[str, dict] | None = None,
        consumed_command_ids: set[str] | None = None,
    ):
        self.gates = gates
        self.approved = approved
        self.prefix = prefix
        self.evidence_key = evidence_key
        self.checkpoints = checkpoints if checkpoints is not None else {}
        self.initialized_runs = (
            initialized_runs if initialized_runs is not None else set(self.checkpoints)
        )
        self.command_reservations = (
            command_reservations if command_reservations is not None else {}
        )
        self.consumed_command_ids = (
            consumed_command_ids if consumed_command_ids is not None else set()
        )
        self.requests: list[dict] = []
        self.checkpoint_reads = 0
        self.checkpoint_advances = 0
        self.checkpoint_history: list[dict] = []
        self.reservation_calls = 0
        self.completion_calls = 0

    def decide(self, request):
        self.requests.append(copy.deepcopy(dict(request)))
        gate = self.gates[request["gateId"]]
        command_capability = None
        if self.approved and gate["verificationType"] == "command":
            command_record = approval(gate)
            command_record["approvalId"] = f"command-{self.prefix}-{request['gateId']}"
            command_capability = trust(command_record)
        return {
            "approvalId": f"{self.prefix}-{request['gateId']}",
            "approvedBy": "operator:test",
            "approvedAt": NOW,
            "decisionState": "approved" if self.approved else "denied",
            "commandApproval": command_capability,
        }

    def seal_receipt(self, receipt):
        return "hmac-sha256:" + hmac.new(
            self.evidence_key, canonical_json(receipt), hashlib.sha256
        ).hexdigest()

    def authenticate_receipt(self, receipt):
        candidate = copy.deepcopy(dict(receipt))
        provided = candidate.pop("providerSeal", None)
        return isinstance(provided, str) and hmac.compare_digest(
            provided, self.seal_receipt(candidate)
        )

    def read_evidence_checkpoint(self, run_id):
        self.checkpoint_reads += 1
        value = self.checkpoints.get(run_id)
        return None if value is None else copy.deepcopy(value)

    def initialize_evidence_checkpoint(self, current):
        run_id = current["runId"]
        if run_id in self.initialized_runs or run_id in self.checkpoints:
            raise RuntimeError("provider lifecycle was already initialized")
        self.initialized_runs.add(run_id)
        self.checkpoints[run_id] = copy.deepcopy(dict(current))

    def reserve_command_execution(self, reservation):
        self.reservation_calls += 1
        value = copy.deepcopy(dict(reservation))
        scope = value["executionKey"]
        attempt = value["attemptKey"]
        active_scope = any(
            record["state"] == "reserved"
            and record["reservation"]["executionKey"] == scope
            for record in self.command_reservations.values()
        )
        if (
            attempt in self.command_reservations
            or active_scope
            or value["operatorApprovalId"] in self.consumed_command_ids
            or value["commandApprovalId"] in self.consumed_command_ids
        ):
            raise RuntimeError("command execution was already durably reserved")
        self.consumed_command_ids.update({
            value["operatorApprovalId"], value["commandApprovalId"],
        })
        self.command_reservations[attempt] = {
            "state": "reserved", "reservation": value,
        }

    def complete_command_execution(self, reservation, receipt):
        self.completion_calls += 1
        value = copy.deepcopy(dict(reservation))
        existing = self.command_reservations.get(value["attemptKey"])
        if existing is None or existing["reservation"] != value:
            raise RuntimeError("command execution reservation is unavailable")
        if not self.authenticate_receipt(receipt):
            raise RuntimeError("command execution receipt is not authenticated")
        receipt_digest = canonical_digest(receipt)
        if existing["state"] == "evidenced":
            if existing.get("receiptDigest") != receipt_digest:
                raise RuntimeError("command execution completion conflicts with history")
            return
        existing["state"] = "evidenced"
        existing["receiptDigest"] = receipt_digest

    def advance_evidence_checkpoint(self, previous, current):
        self.checkpoint_advances += 1
        run_id = current["runId"]
        observed = self.checkpoints.get(run_id)
        expected = None if previous is None else dict(previous)
        if observed != expected:
            raise RuntimeError("provider checkpoint compare-and-swap failed")
        if run_id not in self.initialized_runs:
            if previous is not None:
                raise RuntimeError("provider lifecycle is not initialized")
            self.initialized_runs.add(run_id)
        self.checkpoints[run_id] = copy.deepcopy(dict(current))
        self.checkpoint_history.append(copy.deepcopy(dict(current)))


class _DecisionStateOperator(_Operator):
    def __init__(self, gates: dict[str, dict], decision_state: str):
        super().__init__(gates, approved=False, prefix=decision_state)
        self.decision_state = decision_state

    def decide(self, request):
        self.requests.append(copy.deepcopy(dict(request)))
        return {
            "approvalId": f"{self.prefix}-{request['gateId']}",
            "approvedBy": "operator:test",
            "approvedAt": NOW,
            "decisionState": self.decision_state,
            "commandApproval": None,
        }


class BuilderD3GateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.repo = self.base / "repo"
        self.spec, self.plan, self.initial, _second, _third = fixture(self.repo)
        self.host = json.loads((
            ROOT / "tests" / "fixtures" / "compass_builder" / "host-capabilities.valid.json"
        ).read_text(encoding="utf-8"))
        self.plan["hostEvidenceDigest"] = canonical_digest(self.host)
        self.environment = dict(os.environ)
        self.environment["PYTHONIOENCODING"] = "utf-8"
        self.environment_identity = environment_digest(
            self.environment, current_platform_identity()
        )

    def tearDown(self):
        self.temporary.cleanup()

    def story_gate(self, *, required: bool = True) -> dict:
        gate = command_gate(
            "story-outcome",
            subprocess.list2cmdline([sys.executable, "-c", "print('ok', end='')"])
            if os.name == "nt"
            else f"{sys.executable} -c \"print('ok', end='')\"",
            "stdout-exact:ok",
            self.environment_identity,
        )
        gate.update(gateScope="story", storyId="alpha", required=required)
        return gate

    def test_v1_is_unchanged_and_v2_requires_one_pristine_bound_ledger(self):
        self.assertTrue(
            D3_API_AVAILABLE
            and hasattr(execution_bundle_module, "build_gated_execution_bundle"),
            "D3 gated bundle and enforcement APIs are not implemented",
        )
        v1 = build_execution_bundle(
            self.spec, self.plan, self.host, "2026-09-01T12:01:00Z", self.repo
        )
        self.assertEqual("compass-builder.plan-bundle.v1", v1["schemaVersion"])
        self.assertNotIn("outcomeGateLedger", v1)

        gate = self.story_gate()
        ledger = pristine_ledger(self.spec["runId"], gate)
        v2 = build_gated_execution_bundle(
            self.spec, self.plan, self.host, "2026-09-01T12:01:00Z", self.repo, ledger
        )
        self.assertEqual("compass-builder.plan-bundle.v2", v2["schemaVersion"])
        self.assertEqual(ledger, v2["outcomeGateLedger"])
        self.assertEqual(v2, validate_execution_bundle(v2, self.repo))

        for mutation in ("non-pristine", "wrong-run", "unknown-story", "extra-field"):
            bad = copy.deepcopy(v2)
            if mutation == "non-pristine":
                bad["outcomeGateLedger"]["gates"][0].update(
                    state="met", evidenceDigest="sha256:" + "a" * 64,
                    validatedAt=NOW, verificationRunId=self.spec["runId"],
                )
            elif mutation == "wrong-run":
                bad["outcomeGateLedger"]["runId"] = "cb-wrong-0123456789abcdef"
            elif mutation == "unknown-story":
                bad["outcomeGateLedger"]["gates"][0]["storyId"] = "unknown"
            else:
                bad["unexpected"] = True
            with self.subTest(mutation=mutation), self.assertRaisesRegex(
                Exception, "pristine|run|story|closed"
            ):
                validate_execution_bundle(bad, self.repo)

    def test_v2_bundle_and_gate_evidence_schemas_are_closed_and_versioned(self):
        bundle_path = BUILDER / "schemas" / "plan-bundle.v2.schema.json"
        evidence_path = BUILDER / "schemas" / "gate-evidence.schema.json"
        self.assertTrue(bundle_path.is_file(), "v2 bundle schema is missing")
        self.assertTrue(evidence_path.is_file(), "gate-evidence schema is missing")
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertFalse(bundle["additionalProperties"])
        self.assertEqual(
            {
                "schemaVersion", "runSpec", "wavePlan", "hostCapabilities",
                "planningTimestamp", "repositoryIdentity", "outcomeGateLedger",
            },
            set(bundle["required"]),
        )
        self.assertEqual(
            "compass-builder.plan-bundle.v2",
            bundle["properties"]["schemaVersion"]["const"],
        )
        self.assertFalse(evidence["additionalProperties"])
        self.assertEqual(
            "compass-builder.gate-evidence.v1",
            evidence["properties"]["schemaVersion"]["const"],
        )
        self.assertIn("providerSeal", evidence["required"])
        self.assertEqual(set(evidence["required"]), set(evidence["properties"]))

    def test_gate_evidence_json_schema_matches_python_state_and_type_semantics(self):
        review = self.repo / "docs" / "review.md"
        review.parent.mkdir()
        review.write_text("approved review\n", encoding="utf-8")
        git(self.repo, "add", "docs/review.md")
        git(self.repo, "commit", "-m", "review evidence")
        target_sha = git(self.repo, "rev-parse", "HEAD")
        command = self.story_gate()
        manual = manual_gate("story-schema-manual", self.environment_identity)
        manual.update(gateScope="story", storyId="alpha")
        receipts = []
        for index, (gate, provider) in enumerate((
            (command, _Operator({command["id"]: command}, prefix="schema-command")),
            (manual, _Operator({manual["id"]: manual}, prefix="schema-manual")),
            (command, _DecisionStateOperator({command["id"]: command}, "pending")),
        )):
            run_root = self.base / f"schema-parity-{index}"
            run_root.mkdir()
            kwargs = {}
            if gate["verificationType"] == "command" and not isinstance(
                provider, _DecisionStateOperator
            ):
                kwargs["process_runner"] = (
                    lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "ok", "")
                )
            receipts.append(enforce_scope_gates(
                pristine_ledger(self.spec["runId"], gate), gate_scope="story",
                story_id="alpha", workspace=self.repo, target_sha=target_sha,
                environment=self.environment, provider=provider,
                journal=GateEvidenceJournal(run_root, self.base),
                verified_at=lambda: NOW, **kwargs,
            ).receipts[0])
        executed, manual_receipt, pending = receipts
        manual_unmet = {
            **manual_receipt,
            "approvalId": "schema-manual-unmet",
            "state": "unmet",
            "reason": "reviewed artifact does not meet the criterion",
        }
        manual_unavailable = {
            **manual_receipt,
            "approvalId": "schema-manual-unavailable",
            "state": "unavailable",
            "reviewArtifactDigest": None,
            "reason": "review artifact is unavailable",
        }
        detached_missing_executable = copy.deepcopy(executed)
        missing_execution = detached_missing_executable["commandApprovalAudit"]["execution"]
        missing_execution["executable"]["path"] = str(
            (self.base / "detached-audit-does-not-exist.exe").resolve()
        )
        detached_missing_executable["commandApprovalAudit"][
            "executionIdentityDigest"
        ] = canonical_digest(missing_execution)
        detached_missing_executable["executionIdentityDigest"] = canonical_digest(
            missing_execution
        )

        def rebind_command_audit(receipt):
            candidate = copy.deepcopy(receipt)
            candidate_execution = candidate["commandApprovalAudit"]["execution"]
            execution_digest = canonical_digest(candidate_execution)
            candidate["commandApprovalAudit"][
                "executionIdentityDigest"
            ] = execution_digest
            candidate["executionIdentityDigest"] = execution_digest
            return candidate

        detached_posix_audit = copy.deepcopy(executed)
        posix_execution = detached_posix_audit["commandApprovalAudit"]["execution"]
        posix_execution["platform"] = "linux-x86_64"
        posix_execution["isolation"] = {
            "mode": "posix-staged-copy-v1",
            "sourceExecutableMode": 0o755,
            "stagedExecutableMode": 0o555,
        }
        posix_execution["executable"]["path"] = "/opt/compass/bin/gate-check"
        detached_posix_audit["livePlatform"] = posix_execution["platform"]
        detached_posix_audit["liveEnvironmentDigest"] = posix_execution[
            "environmentDigest"
        ]
        detached_posix_audit = rebind_command_audit(detached_posix_audit)

        stdout_with_artifact = copy.deepcopy(executed)
        stdout_with_artifact["commandApprovalAudit"]["execution"][
            "artifactPath"
        ] = "out/result.txt"
        stdout_with_artifact = rebind_command_audit(stdout_with_artifact)

        artifact_without_path = copy.deepcopy(executed)
        artifact_execution = artifact_without_path["commandApprovalAudit"][
            "execution"
        ]
        artifact_execution["successMarker"] = "artifact-sha256:" + "0" * 64
        artifact_execution["artifactPath"] = None
        artifact_without_path = rebind_command_audit(artifact_without_path)

        posix_with_windows_path = copy.deepcopy(detached_posix_audit)
        posix_with_windows_path["commandApprovalAudit"]["execution"]["executable"][
            "path"
        ] = "C:\\tools\\gate-check.exe"
        posix_with_windows_path = rebind_command_audit(posix_with_windows_path)

        windows_isolation_for_posix = copy.deepcopy(detached_posix_audit)
        windows_isolation_for_posix["commandApprovalAudit"]["execution"][
            "isolation"
        ] = {
            "mode": "windows-locked-original-v1",
            "sourceExecutableMode": None,
            "stagedExecutableMode": None,
        }
        windows_isolation_for_posix = rebind_command_audit(
            windows_isolation_for_posix
        )

        posix_root_executable = copy.deepcopy(detached_posix_audit)
        posix_root_executable["commandApprovalAudit"]["execution"]["executable"][
            "path"
        ] = "/"
        posix_root_executable = rebind_command_audit(posix_root_executable)

        windows_root_executable = copy.deepcopy(executed)
        windows_execution = windows_root_executable["commandApprovalAudit"][
            "execution"
        ]
        windows_execution["platform"] = "windows-amd64"
        windows_execution["isolation"] = {
            "mode": "windows-locked-original-v1",
            "sourceExecutableMode": None,
            "stagedExecutableMode": None,
        }
        windows_execution["executable"]["path"] = "C:\\"
        windows_root_executable["livePlatform"] = windows_execution["platform"]
        windows_root_executable["liveEnvironmentDigest"] = windows_execution[
            "environmentDigest"
        ]
        windows_root_executable = rebind_command_audit(windows_root_executable)

        posix_without_execute_bit = copy.deepcopy(detached_posix_audit)
        posix_without_execute_bit["commandApprovalAudit"]["execution"][
            "isolation"
        ] = {
            "mode": "posix-staged-copy-v1",
            "sourceExecutableMode": 0o644,
            "stagedExecutableMode": 0o444,
        }
        posix_without_execute_bit = rebind_command_audit(posix_without_execute_bit)

        posix_with_wrong_staged_mode = copy.deepcopy(detached_posix_audit)
        posix_with_wrong_staged_mode["commandApprovalAudit"]["execution"][
            "isolation"
        ]["stagedExecutableMode"] = 0o554
        posix_with_wrong_staged_mode = rebind_command_audit(
            posix_with_wrong_staged_mode
        )
        cases = {
            "executed-command": (executed, True),
            "manual": (manual_receipt, True),
            "manual-unmet-with-artifact": (manual_unmet, True),
            "manual-unavailable-without-artifact": (manual_unavailable, True),
            "pending-command": (pending, True),
            "detached-audit-does-not-require-live-executable": (
                detached_missing_executable, True,
            ),
            "detached-posix-audit-is-valid-on-windows": (
                detached_posix_audit, True,
            ),
            "stdout-exact-forbids-artifact-path": (stdout_with_artifact, False),
            "artifact-sha256-requires-artifact-path": (
                artifact_without_path, False,
            ),
            "posix-platform-forbids-windows-executable-path": (
                posix_with_windows_path, False,
            ),
            "posix-platform-requires-posix-isolation": (
                windows_isolation_for_posix, False,
            ),
            "posix-root-is-not-an-executable-path": (
                posix_root_executable, False,
            ),
            "windows-drive-root-is-not-an-executable-path": (
                windows_root_executable, False,
            ),
            "posix-source-mode-requires-execute-bit": (
                posix_without_execute_bit, False,
            ),
            "posix-staged-mode-must-remove-only-write-bits": (
                posix_with_wrong_staged_mode, False,
            ),
            "executed-command-with-review": ({
                **executed, "reviewArtifactDigest": "sha256:" + "1" * 64,
            }, False),
            "pending-command-with-audit": ({
                **pending,
                "commandApprovalAudit": executed["commandApprovalAudit"],
                "executionIdentityDigest": executed["executionIdentityDigest"],
            }, False),
            "met-command-without-audit": ({
                **executed, "commandApprovalAudit": None,
                "executionIdentityDigest": None,
            }, False),
            "manual-with-command-audit": ({
                **manual_receipt,
                "commandApprovalAudit": executed["commandApprovalAudit"],
                "executionIdentityDigest": executed["executionIdentityDigest"],
            }, False),
            "manual-met-without-artifact": ({
                **manual_receipt, "reviewArtifactDigest": None,
            }, False),
            "manual-unmet-without-artifact": ({
                **manual_unmet, "reviewArtifactDigest": None,
            }, False),
            "command-unmet-without-execution": ({
                **pending, "state": "unmet",
            }, False),
            "empty-command-audit": ({
                **executed, "commandApprovalAudit": {},
            }, False),
            "command-audit-extra-field": ({
                **executed,
                "commandApprovalAudit": {
                    **executed["commandApprovalAudit"], "unexpected": True,
                },
            }, False),
            "command-audit-missing-field": ({
                **executed,
                "commandApprovalAudit": {
                    key: value for key, value in executed["commandApprovalAudit"].items()
                    if key != "approvedBy"
                },
            }, False),
            "command-audit-wrong-field": ({
                **executed,
                "commandApprovalAudit": {
                    **executed["commandApprovalAudit"],
                    "approvalKind": "repository-self-approved",
                },
            }, False),
        }
        for field in (
            "approvalId", "approvedBy", "livePlatform", "reason", "providerSeal",
        ):
            cases[f"{field}-leading-space"] = ({
                **manual_receipt, field: " " + manual_receipt[field],
            }, False)
            cases[f"{field}-trailing-space"] = ({
                **manual_receipt, field: manual_receipt[field] + " ",
            }, False)
        for control_name, control in (("nul", "\x00"), ("tab", "\t"), ("newline", "\n"), ("del", "\x7f")):
            cases[f"workspace-{control_name}"] = ({
                **manual_receipt, "workspace": manual_receipt["workspace"] + control + "tail",
            }, False)
        schema = json.loads((
            BUILDER / "schemas" / "gate-evidence.schema.json"
        ).read_text(encoding="utf-8"))
        definitions = schema["$defs"]
        command_execution_schema = definitions["commandExecution"]
        posix_path = re.compile(definitions["posixAbsolutePath"]["pattern"])
        windows_path = re.compile(definitions["windowsAbsolutePath"]["pattern"])
        self.assertIsNotNone(posix_path.fullmatch("/opt/compass/bin/gate-check"))
        self.assertIsNone(posix_path.fullmatch("/"))
        self.assertIsNone(posix_path.fullmatch("C:\\tools\\gate-check.exe"))
        self.assertIsNotNone(windows_path.fullmatch("C:\\tools\\gate-check.exe"))
        self.assertIsNone(windows_path.fullmatch("C:\\"))
        self.assertIsNone(windows_path.fullmatch("/opt/compass/bin/gate-check"))

        conditions = command_execution_schema["allOf"]
        stdout_condition = next(
            item for item in conditions
            if item["if"]["properties"].get("successMarker", {}).get("pattern")
            == "^stdout-exact:"
        )
        artifact_condition = next(
            item for item in conditions
            if item["if"]["properties"].get("successMarker", {}).get("pattern")
            == "^artifact-sha256:"
        )
        self.assertEqual(
            {"type": "null"},
            stdout_condition["then"]["properties"]["artifactPath"],
        )
        self.assertEqual(
            {"$ref": "#/$defs/repositoryPath"},
            artifact_condition["then"]["properties"]["artifactPath"],
        )

        platform_condition = next(
            item for item in conditions
            if "platform" in item["if"]["properties"]
        )
        self.assertEqual(
            {"$ref": "#/$defs/windowsIsolation"},
            platform_condition["then"]["properties"]["isolation"],
        )
        self.assertEqual(
            {"$ref": "#/$defs/posixIsolation"},
            platform_condition["else"]["properties"]["isolation"],
        )
        windows_path_rules = platform_condition["then"]["properties"][
            "executable"
        ]["properties"]["path"]["allOf"]
        self.assertIn({"$ref": "#/$defs/windowsAbsolutePath"}, windows_path_rules)
        shell_script_exclusion = next(
            item["not"]["pattern"] for item in windows_path_rules if "not" in item
        )
        self.assertIsNotNone(re.search(shell_script_exclusion, "C:\\tools\\run.CMD"))
        self.assertIsNotNone(re.search(shell_script_exclusion, "C:\\tools\\run.bat"))
        self.assertIsNone(re.search(shell_script_exclusion, "C:\\tools\\run.exe"))
        self.assertEqual(
            {"$ref": "#/$defs/posixAbsolutePath"},
            platform_condition["else"]["properties"]["executable"][
                "properties"
            ]["path"],
        )
        self.assertEqual(
            {"#/$defs/windowsIsolation", "#/$defs/posixIsolation"},
            {
                item["$ref"]
                for item in command_execution_schema["properties"]["isolation"][
                    "oneOf"
                ]
            },
        )

        marker_pattern = re.compile(
            command_execution_schema["properties"]["successMarker"]["pattern"]
        )
        self.assertIsNotNone(marker_pattern.fullmatch("stdout-exact:ok"))
        self.assertIsNone(marker_pattern.fullmatch("stdout-exact:ok "))
        self.assertIsNotNone(marker_pattern.fullmatch(
            "artifact-sha256:" + "0" * 64
        ))

        posix_mode_branches = definitions["posixIsolation"]["oneOf"]
        encoded_mode_pairs = {
            (source_mode, branch["properties"]["stagedExecutableMode"]["const"])
            for branch in posix_mode_branches
            for source_mode in branch["properties"]["sourceExecutableMode"]["enum"]
        }
        expected_mode_pairs = {
            (source_mode, source_mode & ~0o222)
            for source_mode in range(0o1000)
            if source_mode & 0o111
        }
        self.assertEqual(56, len(posix_mode_branches))
        self.assertEqual(expected_mode_pairs, encoded_mode_pairs)
        for name, (candidate, expected) in cases.items():
            with self.subTest(name=name):
                try:
                    validate_gate_evidence_receipt(copy.deepcopy(candidate))
                    python_accepts = True
                except ValueError:
                    python_accepts = False
                self.assertEqual(expected, python_accepts)

    def test_v1_durable_artifact_allowlist_does_not_admit_gate_evidence(self):
        bundle = build_execution_bundle(
            self.spec, self.plan, self.host, "2026-09-01T12:01:00Z", self.repo
        )
        store = StateStore(self.repo, self.spec, self.plan)
        store.create(store.initial_state(), execution_bundle=bundle)
        for name in ("gate-evidence", "gate-execution-intents"):
            with self.subTest(name=name):
                directory = store.run_root / name
                directory.mkdir()
                with self.assertRaisesRegex(StateError, "v2|gate|artifact"):
                    execution_bundle_module.load_run_bundle(
                        self.repo, self.spec["runId"]
                    )
                with self.assertRaisesRegex(StateError, "v2|gate|artifact"):
                    store.load_durable_state()
                directory.rmdir()

    @unittest.skipUnless(D3_API_AVAILABLE, "D3 API is the current RED boundary")
    def test_d2_command_capability_is_single_use(self):
        gate = self.story_gate()
        capability = trusted_approval(gate)
        ledger = pristine_ledger(self.spec["runId"], gate)
        first = run_approved_gates(
            ledger, [capability], repository_root=self.repo,
            environment=self.environment,
            process_runner=lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "ok", ""),
            verified_at=lambda: NOW,
        )
        self.assertEqual("met", first[0].state)
        with self.assertRaisesRegex(Exception, "consumed|single-use"):
            run_approved_gates(
                ledger, [capability], repository_root=self.repo,
                environment=self.environment,
            )

    def test_v2_missing_provider_fails_before_worker_dispatch(self):
        gate = self.story_gate()
        ledger = pristine_ledger(self.spec["runId"], gate)
        bundle = build_gated_execution_bundle(
            self.spec, self.plan, self.host, "2026-09-01T12:01:00Z", self.repo, ledger
        )
        launches: list[object] = []

        def must_not_launch(*args):
            launches.append(args)
            raise AssertionError("worker launched without an operator provider")

        with self.assertRaisesRegex(ControllerError, "OperatorGateProvider|operator provider"):
            execute_run(self.repo, bundle, worker_transport=must_not_launch)
        self.assertEqual([], launches)

    @unittest.skipUnless(D3_API_AVAILABLE, "D3 API is the current RED boundary")
    def test_manual_decision_is_exact_scoped_single_use_evidence_and_crash_adopts_met(self):
        review = self.repo / "docs" / "review.md"
        review.parent.mkdir()
        review.write_text("approved review\n", encoding="utf-8")
        git(self.repo, "add", "docs/review.md")
        git(self.repo, "commit", "-m", "review evidence")
        target_sha = git(self.repo, "rev-parse", "HEAD")
        gate = manual_gate("story-manual", self.environment_identity)
        gate.update(gateScope="story", storyId="alpha")
        ledger = pristine_ledger(self.spec["runId"], gate)
        run_root = self.base / "run"
        run_root.mkdir()
        journal = GateEvidenceJournal(run_root, self.base)
        checkpoints: dict[str, dict] = {}
        provider = _Operator({gate["id"]: gate}, checkpoints=checkpoints)

        outcome = enforce_scope_gates(
            ledger, gate_scope="story", story_id="alpha",
            workspace=self.repo, target_sha=target_sha, environment=self.environment,
            provider=provider, journal=journal, verified_at=lambda: NOW,
        )
        self.assertTrue(outcome.required_met)
        self.assertEqual("met", outcome.receipts[0]["state"])
        request = provider.requests[0]
        self.assertEqual("story", request["gateScope"])
        self.assertEqual("alpha", request["storyId"])
        self.assertEqual(str(self.repo), request["workspace"])
        self.assertEqual(target_sha, request["targetSha"])
        self.assertEqual(canonical_digest(gate), request["gateDefinitionDigest"])
        self.assertIn("gateDefinition", request)
        self.assertEqual(gate, request["gateDefinition"])
        for field in (
            "command", "successMarker", "workingDirectory", "shell", "platform",
            "environmentDigest", "independentReviewPath", "observableOutcome", "risk",
            "coveredRequirementIds", "coveredAcceptanceIds",
        ):
            self.assertEqual(gate[field], request["gateDefinition"][field])
        self.assertTrue(request["reviewArtifactDigest"].startswith("sha256:"))
        self.assertIn("providerSeal", outcome.receipts[0])
        self.assertTrue(outcome.receipts[0]["providerSeal"].startswith("hmac-sha256:"))

        fail_if_called = _Operator(
            {gate["id"]: gate}, approved=False, checkpoints=checkpoints
        )
        adopted = enforce_scope_gates(
            ledger, gate_scope="story", story_id="alpha",
            workspace=self.repo, target_sha=target_sha, environment=self.environment,
            provider=fail_if_called, journal=journal, verified_at=lambda: NOW,
        )
        self.assertTrue(adopted.required_met)
        self.assertTrue(adopted.adopted)
        self.assertEqual([], fail_if_called.requests)
        self.assertEqual(1, len(journal.read()))
        self.assertTrue(fold_gate_evidence(ledger, journal.read()).valid_chain)

    @unittest.skipUnless(D3_API_AVAILABLE, "D3 API is the current RED boundary")
    def test_unauthenticated_tail_after_provider_checkpoint_fails_before_decision_or_advance(self):
        review = self.repo / "docs" / "review.md"
        review.parent.mkdir()
        review.write_text("approved review\n", encoding="utf-8")
        git(self.repo, "add", "docs/review.md")
        git(self.repo, "commit", "-m", "review evidence")
        target_sha = git(self.repo, "rev-parse", "HEAD")
        gate = manual_gate("story-forged", self.environment_identity)
        gate.update(gateScope="story", storyId="alpha")
        ledger = pristine_ledger(self.spec["runId"], gate)
        run_root = self.base / "forged-run"
        run_root.mkdir()
        journal = GateEvidenceJournal(run_root, self.base)
        checkpoints: dict[str, dict] = {}
        denied_provider = _Operator(
            {gate["id"]: gate}, approved=False, prefix="denied", checkpoints=checkpoints
        )
        denied = enforce_scope_gates(
            ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
            target_sha=target_sha, environment=self.environment,
            provider=denied_provider, journal=journal, verified_at=lambda: NOW,
        ).receipts[0]

        forged = copy.deepcopy(denied)
        forged.update(
            sequence=2, priorReceiptDigest=canonical_digest(denied),
            approvalId="repository-forged-approval", approvedBy="repository:attacker",
            operatorDecisionDigest=canonical_digest({"forged": True}),
            state="met", evidenceDigest=canonical_digest({"forgedEvidence": True}),
            reason="repository-forged met receipt",
            providerSeal="hmac-sha256:" + "0" * 64,
        )
        ArtifactJournal(run_root, self.base).record("gate-evidence", forged)

        rejecting_provider = _Operator(
            {gate["id"]: gate}, prefix="fresh", checkpoints=checkpoints
        )
        with self.assertRaisesRegex(GateEnforcementError, "unauthenticated.*tail"):
            enforce_scope_gates(
                ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
                target_sha=target_sha, environment=self.environment,
                provider=rejecting_provider, journal=journal, verified_at=lambda: NOW,
            )
        self.assertEqual([], rejecting_provider.requests)
        self.assertEqual(0, rejecting_provider.checkpoint_advances)
        self.assertEqual(2, len(journal.read()))

    @unittest.skipUnless(D3_API_AVAILABLE, "D3 API is the current RED boundary")
    def test_unauthenticated_nonempty_chain_without_checkpoint_fails_before_decision(self):
        review = self.repo / "docs" / "review.md"
        review.parent.mkdir()
        review.write_text("approved review\n", encoding="utf-8")
        git(self.repo, "add", "docs/review.md")
        git(self.repo, "commit", "-m", "review evidence")
        target_sha = git(self.repo, "rev-parse", "HEAD")
        gate = manual_gate("story-preseeded", self.environment_identity)
        gate.update(gateScope="story", storyId="alpha")
        ledger = pristine_ledger(self.spec["runId"], gate)
        source_root = self.base / "source-run"
        source_root.mkdir()
        source = GateEvidenceJournal(source_root, self.base)
        source_receipt = enforce_scope_gates(
            ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
            target_sha=target_sha, environment=self.environment,
            provider=_Operator({gate["id"]: gate}, approved=False), journal=source,
            verified_at=lambda: NOW,
        ).receipts[0]
        forged = copy.deepcopy(source_receipt)
        forged.update(
            providerSeal="hmac-sha256:" + "0" * 64,
            approvalId="repository-preseeded-approval",
            operatorDecisionDigest=canonical_digest({"preseeded": True}),
        )
        run_root = self.base / "preseeded-run"
        run_root.mkdir()
        ArtifactJournal(run_root, self.base).record("gate-evidence", forged)
        journal = GateEvidenceJournal(run_root, self.base)
        provider = _Operator({gate["id"]: gate}, prefix="must-not-decide")
        with self.assertRaisesRegex(GateEnforcementError, "checkpoint.*missing"):
            enforce_scope_gates(
                ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
                target_sha=target_sha, environment=self.environment,
                provider=provider, journal=journal, verified_at=lambda: NOW,
            )
        self.assertEqual([], provider.requests)
        self.assertEqual(0, provider.checkpoint_advances)

    @unittest.skipUnless(D3_API_AVAILABLE, "D3 API is the current RED boundary")
    def test_authenticated_crash_tail_advances_checkpoint_and_is_adopted_after_restart(self):
        review = self.repo / "docs" / "review.md"
        review.parent.mkdir()
        review.write_text("approved review\n", encoding="utf-8")
        git(self.repo, "add", "docs/review.md")
        git(self.repo, "commit", "-m", "review evidence")
        target_sha = git(self.repo, "rev-parse", "HEAD")
        gates = []
        for gate_id in ("crash-first", "crash-tail"):
            gate = manual_gate(gate_id, self.environment_identity)
            gate.update(gateScope="story", storyId="alpha")
            gates.append(gate)
        ledger = pristine_ledger(self.spec["runId"], *gates)
        run_root = self.base / "crash-tail-run"
        run_root.mkdir()
        journal = GateEvidenceJournal(run_root, self.base)
        checkpoints: dict[str, dict] = {}
        provider = _Operator(
            {gate["id"]: gate for gate in gates}, checkpoints=checkpoints
        )
        enforce_scope_gates(
            ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
            target_sha=target_sha, environment=self.environment,
            provider=provider, journal=journal, verified_at=lambda: NOW,
        )
        self.assertEqual(2, len(provider.checkpoint_history))
        checkpoints[self.spec["runId"]] = copy.deepcopy(provider.checkpoint_history[0])
        restarted = _Operator(
            {gate["id"]: gate for gate in gates}, approved=False,
            prefix="must-not-decide", checkpoints=checkpoints,
        )
        outcome = enforce_scope_gates(
            ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
            target_sha=target_sha, environment=self.environment,
            provider=restarted, journal=journal, verified_at=lambda: NOW,
        )
        self.assertTrue(outcome.adopted)
        self.assertEqual([], restarted.requests)
        self.assertEqual(1, restarted.checkpoint_advances)
        self.assertEqual(2, checkpoints[self.spec["runId"]]["receiptCount"])

    @unittest.skipUnless(D3_API_AVAILABLE, "D3 API is the current RED boundary")
    def test_missing_checkpoint_never_reinitializes_existing_or_tail_deleted_history(self):
        review = self.repo / "docs" / "review.md"
        review.parent.mkdir()
        review.write_text("approved review\n", encoding="utf-8")
        git(self.repo, "add", "docs/review.md")
        git(self.repo, "commit", "-m", "review evidence")
        target_sha = git(self.repo, "rev-parse", "HEAD")
        for delete_tail in (False, True):
            with self.subTest(delete_tail=delete_tail):
                gate = manual_gate(
                    f"checkpoint-{'deleted' if delete_tail else 'retained'}",
                    self.environment_identity,
                )
                gate.update(gateScope="story", storyId="alpha")
                ledger = pristine_ledger(self.spec["runId"], gate)
                run_root = self.base / f"lost-checkpoint-{delete_tail}"
                run_root.mkdir()
                journal = GateEvidenceJournal(run_root, self.base)
                checkpoints: dict[str, dict] = {}
                initialized_runs: set[str] = set()
                provider = _Operator(
                    {gate["id"]: gate}, checkpoints=checkpoints,
                    initialized_runs=initialized_runs,
                )
                enforce_scope_gates(
                    ledger, gate_scope="story", story_id="alpha",
                    workspace=self.repo, target_sha=target_sha,
                    environment=self.environment, provider=provider,
                    journal=journal, verified_at=lambda: NOW,
                )
                checkpoints.pop(self.spec["runId"])
                if delete_tail:
                    for path in (run_root / "gate-evidence").glob("*.json"):
                        path.unlink()
                restarted = _Operator(
                    {gate["id"]: gate}, approved=False, prefix="must-not-decide",
                    checkpoints=checkpoints, initialized_runs=initialized_runs,
                )
                with self.assertRaisesRegex(
                    GateEnforcementError, "checkpoint|lifecycle|initialized|missing",
                ):
                    enforce_scope_gates(
                        ledger, gate_scope="story", story_id="alpha",
                        workspace=self.repo, target_sha=target_sha,
                        environment=self.environment, provider=restarted,
                        journal=journal, verified_at=lambda: NOW,
                    )
                self.assertEqual([], restarted.requests)

    @unittest.skipUnless(D3_API_AVAILABLE, "D3 API is the current RED boundary")
    def test_append_failure_after_command_execution_cannot_execute_again_on_retry(self):
        gate = self.story_gate()
        ledger = pristine_ledger(self.spec["runId"], gate)
        run_root = self.base / "command-append-failure"
        run_root.mkdir()
        journal = GateEvidenceJournal(run_root, self.base)
        checkpoints: dict[str, dict] = {}
        initialized_runs: set[str] = set()
        command_reservations: dict[str, dict] = {}
        consumed_command_ids: set[str] = set()
        provider = _Operator(
            {gate["id"]: gate}, prefix="first-command", checkpoints=checkpoints,
            initialized_runs=initialized_runs,
            command_reservations=command_reservations,
            consumed_command_ids=consumed_command_ids,
        )
        executions: list[tuple[str, ...]] = []

        def execute_once(argv, **_kwargs):
            executions.append(tuple(argv))
            return subprocess.CompletedProcess(argv, 0, "ok", "")

        with patch.object(
            journal, "append", side_effect=OSError("synthetic receipt append failure"),
        ), self.assertRaisesRegex(OSError, "append failure"):
            enforce_scope_gates(
                ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
                target_sha=self.initial, environment=self.environment,
                provider=provider, journal=journal, process_runner=execute_once,
                verified_at=lambda: NOW,
            )
        self.assertEqual(1, len(executions))
        self.assertEqual(1, len(journal.read_command_execution_intents()))

        for path in (run_root / "gate-execution-intents").glob("*.json"):
            path.unlink()
        restarted = _Operator(
            {gate["id"]: gate}, prefix="retry-command", checkpoints=checkpoints,
            initialized_runs=initialized_runs,
            command_reservations=command_reservations,
            consumed_command_ids=consumed_command_ids,
        )
        with self.assertRaisesRegex(GateEnforcementError, "reserved|consumed|execution"):
            enforce_scope_gates(
                ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
                target_sha=self.initial, environment=self.environment,
                provider=restarted, journal=journal, process_runner=execute_once,
                verified_at=lambda: NOW,
            )
        self.assertEqual(1, len(executions))
        self.assertEqual(1, provider.reservation_calls)
        self.assertEqual(1, restarted.reservation_calls)

    @unittest.skipUnless(D3_API_AVAILABLE, "D3 API is the current RED boundary")
    def test_authenticated_nonmet_command_completes_reservation_and_can_run_fresh_attempt(self):
        gate = self.story_gate()
        ledger = pristine_ledger(self.spec["runId"], gate)
        run_root = self.base / "command-nonmet-retry"
        run_root.mkdir()
        journal = GateEvidenceJournal(run_root, self.base)
        checkpoints: dict[str, dict] = {}
        initialized_runs: set[str] = set()
        command_reservations: dict[str, dict] = {}
        consumed_command_ids: set[str] = set()
        executions: list[str] = []

        first = _Operator(
            {gate["id"]: gate}, prefix="nonmet-first", checkpoints=checkpoints,
            initialized_runs=initialized_runs,
            command_reservations=command_reservations,
            consumed_command_ids=consumed_command_ids,
        )
        first_outcome = enforce_scope_gates(
            ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
            target_sha=self.initial, environment=self.environment, provider=first,
            journal=journal,
            process_runner=lambda argv, **_kwargs: (
                executions.append("unmet")
                or subprocess.CompletedProcess(argv, 0, "not-ok", "")
            ),
            verified_at=lambda: NOW,
        )
        self.assertFalse(first_outcome.required_met)
        self.assertEqual("unmet", first_outcome.receipts[0]["state"])
        self.assertEqual(
            ["evidenced"],
            [record["state"] for record in command_reservations.values()],
        )

        second = _Operator(
            {gate["id"]: gate}, prefix="nonmet-second", checkpoints=checkpoints,
            initialized_runs=initialized_runs,
            command_reservations=command_reservations,
            consumed_command_ids=consumed_command_ids,
        )
        second_outcome = enforce_scope_gates(
            ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
            target_sha=self.initial, environment=self.environment, provider=second,
            journal=journal,
            process_runner=lambda argv, **_kwargs: (
                executions.append("met")
                or subprocess.CompletedProcess(argv, 0, "ok", "")
            ),
            verified_at=lambda: NOW,
        )
        self.assertTrue(second_outcome.required_met)
        self.assertEqual(["unmet", "met"], executions)
        self.assertEqual(2, len(journal.read()))
        self.assertEqual(1, second.reservation_calls)

        covered = require_gate_evidence_coverage(
            ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
            target_sha=self.initial, environment=self.environment, provider=second,
            journal=journal,
        )
        self.assertEqual(["met"], [receipt["state"] for receipt in covered])
        self.assertEqual(
            ["evidenced", "evidenced"],
            sorted(record["state"] for record in command_reservations.values()),
        )
        reservations = [
            record["reservation"] for record in command_reservations.values()
        ]
        self.assertEqual(2, len({item["attemptKey"] for item in reservations}))
        self.assertEqual(1, len({item["executionKey"] for item in reservations}))

        restarted = _Operator(
            {gate["id"]: gate}, prefix="nonmet-restarted",
            checkpoints=checkpoints, initialized_runs=initialized_runs,
            command_reservations=command_reservations,
            consumed_command_ids=consumed_command_ids,
        )
        restarted_coverage = require_gate_evidence_coverage(
            ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
            target_sha=self.initial, environment=self.environment,
            provider=restarted,
            journal=GateEvidenceJournal(run_root, self.base),
        )
        self.assertEqual(["met"], [
            receipt["state"] for receipt in restarted_coverage
        ])
        self.assertEqual([], restarted.requests)

    @unittest.skipUnless(D3_API_AVAILABLE, "D3 API is the current RED boundary")
    def test_restart_provider_with_same_trust_adopts_authenticated_met(self):
        review = self.repo / "docs" / "review.md"
        review.parent.mkdir()
        review.write_text("approved review\n", encoding="utf-8")
        git(self.repo, "add", "docs/review.md")
        git(self.repo, "commit", "-m", "review evidence")
        target_sha = git(self.repo, "rev-parse", "HEAD")
        gate = manual_gate("story-restart", self.environment_identity)
        gate.update(gateScope="story", storyId="alpha")
        ledger = pristine_ledger(self.spec["runId"], gate)
        run_root = self.base / "restart-run"
        run_root.mkdir()
        journal = GateEvidenceJournal(run_root, self.base)
        checkpoints: dict[str, dict] = {}
        enforce_scope_gates(
            ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
            target_sha=target_sha, environment=self.environment,
            provider=_Operator({gate["id"]: gate}, checkpoints=checkpoints),
            journal=journal, verified_at=lambda: NOW,
        )
        restarted = _Operator(
            {gate["id"]: gate}, approved=False, prefix="unused", checkpoints=checkpoints
        )
        adopted = enforce_scope_gates(
            ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
            target_sha=target_sha, environment=self.environment,
            provider=restarted, journal=journal, verified_at=lambda: NOW,
        )
        self.assertTrue(adopted.adopted)
        self.assertEqual([], restarted.requests)

    @unittest.skipUnless(D3_API_AVAILABLE, "D3 API is the current RED boundary")
    def test_manual_gate_definition_must_match_live_platform_and_environment_before_provider(self):
        review = self.repo / "docs" / "review.md"
        review.parent.mkdir()
        review.write_text("approved review\n", encoding="utf-8")
        git(self.repo, "add", "docs/review.md")
        git(self.repo, "commit", "-m", "review evidence")
        target_sha = git(self.repo, "rev-parse", "HEAD")
        gate = manual_gate("story-environment", self.environment_identity)
        gate.update(gateScope="story", storyId="alpha")
        ledger = pristine_ledger(self.spec["runId"], gate)
        run_root = self.base / "environment-run"
        run_root.mkdir()
        journal = GateEvidenceJournal(run_root, self.base)
        checkpoints: dict[str, dict] = {}
        first = _Operator(
            {gate["id"]: gate}, prefix="environment-one", checkpoints=checkpoints
        )
        initial = enforce_scope_gates(
            ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
            target_sha=target_sha, environment=self.environment,
            provider=first, journal=journal, verified_at=lambda: NOW,
        )
        self.assertIn("liveEnvironmentDigest", initial.receipts[0])
        self.assertIn("livePlatform", initial.receipts[0])
        self.assertEqual(
            environment_digest(self.environment, current_platform_identity()),
            initial.receipts[0]["liveEnvironmentDigest"],
        )
        self.assertEqual(current_platform_identity(), initial.receipts[0]["livePlatform"])
        self.assertEqual(
            initial.receipts[0]["liveEnvironmentDigest"],
            first.requests[0]["liveEnvironmentDigest"],
        )

        changed_environment = dict(self.environment)
        changed_environment["COMPASS_D3_ENVIRONMENT_CHANGE"] = "changed"
        changed_provider = _Operator(
            {gate["id"]: gate}, prefix="environment-unused", checkpoints=checkpoints
        )
        with self.assertRaisesRegex(GateEnforcementError, "live.*identity|platform|environment"):
            enforce_scope_gates(
                ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
                target_sha=target_sha, environment=changed_environment,
                provider=changed_provider, journal=journal, verified_at=lambda: NOW,
            )
        self.assertEqual([], changed_provider.requests)
        self.assertEqual(0, changed_provider.checkpoint_reads)

        wrong_platform_gate = copy.deepcopy(gate)
        wrong_platform_gate["platform"] = "wrong-platform"
        wrong_platform_gate["required"] = False
        required_root = manual_gate("required-platform-root", self.environment_identity)
        provider = _Operator(
            {wrong_platform_gate["id"]: wrong_platform_gate}, prefix="platform-unused"
        )
        platform_root = self.base / "wrong-platform"
        platform_root.mkdir()
        with self.assertRaisesRegex(GateEnforcementError, "live.*identity|platform|environment"):
            enforce_scope_gates(
                pristine_ledger(self.spec["runId"], wrong_platform_gate, required_root),
                gate_scope="story", story_id="alpha", workspace=self.repo,
                target_sha=target_sha, environment=self.environment, provider=provider,
                journal=GateEvidenceJournal(platform_root, self.base),
                verified_at=lambda: NOW,
            )
        self.assertEqual([], provider.requests)
        self.assertEqual(0, provider.checkpoint_reads)

    @unittest.skipUnless(D3_API_AVAILABLE, "D3 API is the current RED boundary")
    def test_provider_pending_abandoned_unavailable_blocked_and_manual_unmet_are_truthful_states(self):
        review = self.repo / "docs" / "review.md"
        review.parent.mkdir()
        review.write_text("approved review\n", encoding="utf-8")
        git(self.repo, "add", "docs/review.md")
        git(self.repo, "commit", "-m", "review evidence")
        target_sha = git(self.repo, "rev-parse", "HEAD")
        for required in (True, False):
            for decision_state in ("pending", "abandoned", "unavailable", "blocked", "unmet"):
                with self.subTest(required=required, decision_state=decision_state):
                    gate = manual_gate(
                        f"{decision_state}-{'required' if required else 'optional'}",
                        self.environment_identity,
                    )
                    gate.update(
                        gateScope="story", storyId="alpha", required=required
                    )
                    gates = [gate]
                    if not required:
                        gates.append(manual_gate(
                            f"required-root-{decision_state}", self.environment_identity
                        ))
                    ledger = pristine_ledger(self.spec["runId"], *gates)
                    run_root = self.base / f"{decision_state}-{required}"
                    run_root.mkdir()
                    journal = GateEvidenceJournal(run_root, self.base)
                    try:
                        outcome = enforce_scope_gates(
                            ledger, gate_scope="story", story_id="alpha",
                            workspace=self.repo, target_sha=target_sha,
                            environment=self.environment,
                            provider=_DecisionStateOperator({gate["id"]: gate}, decision_state),
                            journal=journal, verified_at=lambda: NOW,
                        )
                    except GateEnforcementError as exc:
                        self.fail(f"trusted {decision_state} decision was not recorded: {exc}")
                    self.assertEqual(decision_state, outcome.receipts[0]["state"])
                    self.assertEqual(not required, outcome.required_met)
                    self.assertIsNone(outcome.receipts[0]["commandApprovalAudit"])
                    self.assertTrue(outcome.receipts[0]["providerSeal"])

    @unittest.skipUnless(D3_API_AVAILABLE, "D3 API is the current RED boundary")
    def test_missing_manual_artifact_requires_truthful_unavailable_decision(self):
        target_sha = git(self.repo, "rev-parse", "HEAD")
        for required in (True, False):
            with self.subTest(required=required):
                gate = manual_gate(
                    f"missing-{'required' if required else 'optional'}",
                    self.environment_identity,
                )
                gate.update(gateScope="story", storyId="alpha", required=required)
                gates = [gate]
                if not required:
                    gates.append(manual_gate(
                        "missing-required-root", self.environment_identity
                    ))
                ledger = pristine_ledger(self.spec["runId"], *gates)
                run_root = self.base / f"missing-{required}"
                run_root.mkdir()
                provider = _DecisionStateOperator({gate["id"]: gate}, "unavailable")
                outcome = enforce_scope_gates(
                    ledger, gate_scope="story", story_id="alpha",
                    workspace=self.repo, target_sha=target_sha,
                    environment=self.environment, provider=provider,
                    journal=GateEvidenceJournal(run_root, self.base),
                    verified_at=lambda: NOW,
                )
                self.assertEqual(1, len(provider.requests))
                self.assertIsNone(provider.requests[0]["reviewArtifactDigest"])
                self.assertEqual("unavailable", outcome.receipts[0]["state"])
                self.assertIsNone(outcome.receipts[0]["reviewArtifactDigest"])
                self.assertEqual(not required, outcome.required_met)

        gate = manual_gate("missing-invalid-response", self.environment_identity)
        gate.update(gateScope="story", storyId="alpha")
        ledger = pristine_ledger(self.spec["runId"], gate)
        for invalid_state in ("approved", "unmet", "pending"):
            with self.subTest(invalid_state=invalid_state):
                run_root = self.base / f"missing-invalid-{invalid_state}"
                run_root.mkdir()
                provider = _DecisionStateOperator({gate["id"]: gate}, invalid_state)
                with self.assertRaisesRegex(GateEnforcementError, "artifact.*unavailable|unavailable.*artifact"):
                    enforce_scope_gates(
                        ledger, gate_scope="story", story_id="alpha",
                        workspace=self.repo, target_sha=target_sha,
                        environment=self.environment, provider=provider,
                        journal=GateEvidenceJournal(run_root, self.base),
                        verified_at=lambda: NOW,
                    )

    @unittest.skipUnless(D3_API_AVAILABLE, "D3 API is the current RED boundary")
    def test_manual_review_path_cannot_escape_through_symlink_parent(self):
        external = self.base / "external-review"
        external.mkdir()
        (external / "review.md").write_text("external approval\n", encoding="utf-8")
        exclude = self.repo / ".git" / "info" / "exclude"
        exclude.write_text(
            exclude.read_text(encoding="utf-8") + "\ndocs\n", encoding="utf-8"
        )
        link = self.repo / "docs"
        if os.name == "nt":
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(external)],
                check=False, capture_output=True, text=True, shell=False,
            )
            if created.returncode:
                self.skipTest("directory junction creation is unavailable")
        else:
            try:
                os.symlink(external, link, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlink creation is unavailable: {exc}")
        gate = manual_gate("escaped-manual", self.environment_identity)
        gate.update(gateScope="story", storyId="alpha", required=False)
        root_gate = manual_gate("escaped-required-root", self.environment_identity)
        provider = _DecisionStateOperator({gate["id"]: gate}, "unavailable")
        run_root = self.base / "escaped-run"
        run_root.mkdir()
        with self.assertRaisesRegex(GateEnforcementError, "escape|symlink|reparse|contain"):
            enforce_scope_gates(
                pristine_ledger(self.spec["runId"], gate, root_gate),
                gate_scope="story", story_id="alpha", workspace=self.repo,
                target_sha=self.initial, environment=self.environment,
                provider=provider, journal=GateEvidenceJournal(run_root, self.base),
                verified_at=lambda: NOW,
            )
        self.assertEqual([], provider.requests)

    @unittest.skipUnless(D3_API_AVAILABLE, "D3 API is the current RED boundary")
    def test_unmet_provider_decision_is_rejected_for_command_gate(self):
        gate = self.story_gate()
        provider = _DecisionStateOperator({gate["id"]: gate}, "unmet")
        run_root = self.base / "command-unmet"
        run_root.mkdir()
        with self.assertRaisesRegex(GateEnforcementError, "unmet.*manual|command.*unmet"):
            enforce_scope_gates(
                pristine_ledger(self.spec["runId"], gate), gate_scope="story",
                story_id="alpha", workspace=self.repo, target_sha=self.initial,
                environment=self.environment, provider=provider,
                journal=GateEvidenceJournal(run_root, self.base),
                verified_at=lambda: NOW,
            )

    @unittest.skipUnless(D3_API_AVAILABLE, "D3 API is the current RED boundary")
    def test_authenticated_receipt_cannot_be_adopted_without_provider(self):
        gate = self.story_gate()
        ledger = pristine_ledger(self.spec["runId"], gate)
        run_root = self.base / "no-provider-run"
        run_root.mkdir()
        journal = GateEvidenceJournal(run_root, self.base)
        provider = _Operator({gate["id"]: gate})
        enforce_scope_gates(
            ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
            target_sha=self.initial, environment=self.environment,
            provider=provider, journal=journal, verified_at=lambda: NOW,
            process_runner=lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "ok", ""),
        )
        with self.assertRaisesRegex(GateEnforcementError, "trusted.*provider"):
            enforce_scope_gates(
                ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
                target_sha=self.initial, environment=self.environment,
                provider=None, journal=journal,
            )

    @unittest.skipUnless(D3_API_AVAILABLE, "D3 API is the current RED boundary")
    def test_provider_checkpoint_rejects_all_or_tail_receipt_deletion(self):
        review = self.repo / "docs" / "review.md"
        review.parent.mkdir()
        review.write_text("approved review\n", encoding="utf-8")
        git(self.repo, "add", "docs/review.md")
        git(self.repo, "commit", "-m", "review evidence")
        target_sha = git(self.repo, "rev-parse", "HEAD")
        for deletion in ("all", "tail"):
            with self.subTest(deletion=deletion):
                first = manual_gate(f"{deletion}-first", self.environment_identity)
                first.update(gateScope="story", storyId="alpha")
                gates = [first]
                if deletion == "tail":
                    second = manual_gate("tail-second", self.environment_identity)
                    second.update(gateScope="story", storyId="alpha")
                    gates.append(second)
                ledger = pristine_ledger(self.spec["runId"], *gates)
                run_root = self.base / f"{deletion}-run"
                run_root.mkdir()
                journal = GateEvidenceJournal(run_root, self.base)
                checkpoints: dict[str, dict] = {}
                provider = _Operator(
                    {gate["id"]: gate for gate in gates}, checkpoints=checkpoints
                )
                enforce_scope_gates(
                    ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
                    target_sha=target_sha, environment=self.environment,
                    provider=provider, journal=journal, verified_at=lambda: NOW,
                )
                paths = list((run_root / "gate-evidence").glob("*.json"))
                if deletion == "all":
                    for path in paths:
                        path.unlink()
                else:
                    for path in paths:
                        value = json.loads(path.read_text(encoding="utf-8"))
                        if value["sequence"] == 2:
                            path.unlink()
                restarted = _Operator(
                    {gate["id"]: gate for gate in gates}, prefix="restart",
                    checkpoints=checkpoints,
                )
                with self.assertRaisesRegex(GateEnforcementError, "checkpoint|truncat"):
                    enforce_scope_gates(
                        ledger, gate_scope="story", story_id="alpha",
                        workspace=self.repo, target_sha=target_sha,
                        environment=self.environment, provider=restarted, journal=journal,
                        verified_at=lambda: NOW,
                    )

    @unittest.skipUnless(D3_API_AVAILABLE, "D3 API is the current RED boundary")
    def test_python_receipt_validator_binds_scope_to_semantic_phase(self):
        review = self.repo / "docs" / "review.md"
        review.parent.mkdir()
        review.write_text("approved review\n", encoding="utf-8")
        git(self.repo, "add", "docs/review.md")
        git(self.repo, "commit", "-m", "review evidence")
        gate = manual_gate("story-phase", self.environment_identity)
        gate.update(gateScope="story", storyId="alpha")
        ledger = pristine_ledger(self.spec["runId"], gate)
        run_root = self.base / "phase-run"
        run_root.mkdir()
        journal = GateEvidenceJournal(run_root, self.base)
        receipt = enforce_scope_gates(
            ledger, gate_scope="story", story_id="alpha", workspace=self.repo,
            target_sha=git(self.repo, "rev-parse", "HEAD"), environment=self.environment,
            provider=_Operator({gate["id"]: gate}), journal=journal,
            verified_at=lambda: NOW,
        ).receipts[0]
        receipt["phase"] = "post-merge-check"
        with self.assertRaisesRegex(ValueError, "phase"):
            validate_gate_evidence_receipt(receipt)

    @unittest.skipUnless(D3_API_AVAILABLE, "D3 API is the current RED boundary")
    def test_raw_provider_cannot_approve_and_optional_unmet_is_truthful_nonblocking(self):
        gate = self.story_gate(required=False)
        required_root = manual_gate("required-root", self.environment_identity)
        ledger = pristine_ledger(self.spec["runId"], gate, required_root)
        run_root = self.base / "run"
        run_root.mkdir()
        journal = GateEvidenceJournal(run_root, self.base)
        with self.assertRaisesRegex(GateEnforcementError, "trusted.*provider"):
            enforce_scope_gates(
                ledger, gate_scope="story", story_id="alpha",
                workspace=self.repo, target_sha=self.initial, environment=self.environment,
                provider={"approved": True}, journal=journal,
            )
        self.assertEqual((), journal.read())

        provider = _Operator({gate["id"]: gate})
        outcome = enforce_scope_gates(
            ledger, gate_scope="story", story_id="alpha",
            workspace=self.repo, target_sha=self.initial, environment=self.environment,
            provider=provider, journal=journal, verified_at=lambda: NOW,
            process_runner=lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "wrong", ""),
        )
        self.assertTrue(outcome.required_met)
        self.assertEqual("unmet", outcome.receipts[0]["state"])
        self.assertFalse(outcome.receipts[0]["required"])
        self.assertIn("gateDefinition", provider.requests[0])
        definition = provider.requests[0]["gateDefinition"]
        self.assertEqual(gate, definition)
        for field in (
            "command", "successMarker", "workingDirectory", "shell", "platform",
            "environmentDigest", "observableOutcome", "risk",
            "coveredRequirementIds", "coveredAcceptanceIds",
        ):
            self.assertEqual(gate[field], definition[field])

    @unittest.skipUnless(D3_API_AVAILABLE, "D3 API is the current RED boundary")
    def test_non_met_requires_fresh_decision_and_approval_ids_never_reuse(self):
        gate = self.story_gate()
        ledger = pristine_ledger(self.spec["runId"], gate)
        run_root = self.base / "run"
        run_root.mkdir()
        journal = GateEvidenceJournal(run_root, self.base)
        checkpoints: dict[str, dict] = {}
        initialized_runs: set[str] = set()
        first_provider = _Operator(
            {gate["id"]: gate}, approved=False, prefix="same",
            checkpoints=checkpoints, initialized_runs=initialized_runs,
        )
        first = enforce_scope_gates(
            ledger, gate_scope="story", story_id="alpha",
            workspace=self.repo, target_sha=self.initial, environment=self.environment,
            provider=first_provider, journal=journal, verified_at=lambda: NOW,
        )
        self.assertFalse(first.required_met)
        self.assertEqual("denied", first.receipts[0]["state"])

        reused_provider = _Operator(
            {gate["id"]: gate}, prefix="same", checkpoints=checkpoints,
            initialized_runs=initialized_runs,
        )
        with self.assertRaisesRegex(GateEnforcementError, "approvalId.*reused"):
            enforce_scope_gates(
                ledger, gate_scope="story", story_id="alpha",
                workspace=self.repo, target_sha=self.initial, environment=self.environment,
                provider=reused_provider, journal=journal, verified_at=lambda: NOW,
            )
        self.assertEqual(1, len(reused_provider.requests))
        self.assertEqual(1, len(journal.read()))


if __name__ == "__main__":
    unittest.main()
