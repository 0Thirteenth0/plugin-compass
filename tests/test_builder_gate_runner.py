from __future__ import annotations

import copy
import ctypes
import hashlib
import inspect
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Mapping
from contextlib import contextmanager
from unittest.mock import patch
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "compass-builder"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from compass_builder._validation import canonical_digest  # noqa: E402
from compass_builder.gate_approval import (  # noqa: E402
    ApprovalBoundaryError,
    ApprovalDecisionProvider,
    issue_trusted_gate_approval,
)
from compass_builder.gate_runner import (  # noqa: E402
    APPROVAL_SCHEMA_VERSION,
    GateRunnerError,
    current_platform_identity,
    digest_file,
    environment_digest,
    render_direct_command,
    run_approved_gates,
    validate_gate_approval,
)
from compass_builder.process_runner import BoundedProcessError  # noqa: E402


DIRECT_SHELL_IDENTITY = "direct-no-shell-v1"


def command_gate(
    gate_id: str,
    command: str,
    marker: str,
    environment_identity: str,
    *,
    working_directory: str = ".",
    platform_identity: str | None = None,
) -> dict:
    return {
        "id": gate_id,
        "gateScope": "root",
        "storyId": None,
        "observableOutcome": f"{gate_id} observes its declared outcome.",
        "coveredRequirementIds": ["R100"],
        "coveredAcceptanceIds": ["A100"],
        "verificationType": "command",
        "command": command,
        "independentReviewPath": None,
        "successMarker": marker,
        "workingDirectory": working_directory,
        "shell": DIRECT_SHELL_IDENTITY,
        "platform": platform_identity or current_platform_identity(),
        "environmentDigest": environment_identity,
        "risk": "low",
        "validationStrength": "decisive",
        "required": True,
        "state": "pending",
        "evidenceDigest": None,
        "validatedAt": None,
        "verificationRunId": None,
        "handoffReason": None,
    }


def manual_gate(gate_id: str, environment_identity: str) -> dict:
    gate = command_gate(gate_id, "unused", "stdout-exact:unused", environment_identity)
    gate.update(
        verificationType="manual-review",
        command=None,
        independentReviewPath="docs/review.md",
        successMarker="review-decision:approved",
        shell=None,
    )
    return gate


def ledger(*gates: dict) -> dict:
    return {
        "schemaVersion": "compass-builder.outcome-gate-ledger.v1",
        "controller": "compass-builder",
        "runId": "cb-test-0123456789abcdef",
        "requiredRequirementIds": ["R100"],
        "requiredAcceptanceIds": ["A100"],
        "gates": list(gates),
    }


def approval(
    gate: dict,
    *,
    references: tuple[tuple[str, str], ...] = (),
    reference_bindings: tuple[tuple[str, int], ...] = (),
    artifact_path: str | None = None,
    timeout_ms: int = 5_000,
    max_output_bytes: int = 16_384,
) -> dict:
    executable = Path(sys.executable).resolve()
    source_mode = None
    staged_mode = None
    isolation_mode = "windows-locked-original-v1"
    if os.name != "nt":
        source_mode = stat.S_IMODE(executable.stat(follow_symlinks=False).st_mode)
        staged_mode = source_mode & ~0o222
        isolation_mode = "posix-staged-copy-v1"
    execution = {
        "launchMode": "direct",
        "isolation": {
            "mode": isolation_mode,
            "sourceExecutableMode": source_mode,
            "stagedExecutableMode": staged_mode,
        },
        "command": gate["command"],
        "successMarker": gate["successMarker"],
        "workingDirectory": gate["workingDirectory"],
        "shell": gate["shell"],
        "platform": gate["platform"],
        "environmentDigest": gate["environmentDigest"],
        "limits": {
            "timeoutMs": timeout_ms,
            "maxOutputBytes": max_output_bytes,
            "maxArtifactBytes": 1_048_576,
            "maxReferenceBytes": 1_048_576,
            "maxExecutableBytes": 134_217_728,
        },
        "executable": {
            "path": str(executable),
            "digest": digest_file(executable, max_bytes=134_217_728),
        },
        "referencedFiles": [
            {"path": path, "digest": digest} for path, digest in references
        ],
        "referenceBindings": [
            {"path": path, "argvIndex": argv_index}
            for path, argv_index in reference_bindings
        ],
        "referencesComplete": True,
        "artifactPath": artifact_path,
    }
    return {
        "schemaVersion": APPROVAL_SCHEMA_VERSION,
        "approvalKind": "explicit-operator",
        "approvalId": f"approval-{gate['id']}",
        "approvedBy": "operator:test",
        "approvedAt": "2026-09-02T20:00:00Z",
        "gateId": gate["id"],
        "gateDefinitionDigest": canonical_digest(gate),
        "executionIdentityDigest": canonical_digest(execution),
        "execution": execution,
    }


class _TestApprovalProvider(ApprovalDecisionProvider):
    def approve(self, candidate) -> bool:
        return True


def trust(record: dict):
    return issue_trusted_gate_approval(record, _TestApprovalProvider())


def trusted_approval(gate: dict, **kwargs):
    return trust(approval(gate, **kwargs))


class GateRunnerTests(unittest.TestCase):
    def setUp(self):
        original_tempdir = tempfile.tempdir
        tempfile.tempdir = str(Path(tempfile.gettempdir()).resolve(strict=True))
        self.addCleanup(setattr, tempfile, "tempdir", original_tempdir)
        self.environment = dict(os.environ)
        self.environment["PYTHONIOENCODING"] = "utf-8"
        self.environment_identity = environment_digest(
            self.environment, current_platform_identity()
        )

    def test_exact_approvals_execute_in_ledger_order_with_closed_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            order_path = root / "order.txt"
            first = command_gate(
                "first",
                render_direct_command(
                    [
                        sys.executable,
                        "-c",
                        f"from pathlib import Path; p=Path({str(order_path)!r}); "
                        "p.write_text('1', encoding='utf-8'); print('one', end='')",
                    ]
                ),
                "stdout-exact:one",
                self.environment_identity,
            )
            second = command_gate(
                "second",
                render_direct_command(
                    [
                        sys.executable,
                        "-c",
                        f"from pathlib import Path; p=Path({str(order_path)!r}); "
                        "p.write_text(p.read_text(encoding='utf-8')+'2', encoding='utf-8'); "
                        "print('two', end='')",
                    ]
                ),
                "stdout-exact:two",
                self.environment_identity,
            )

            results = run_approved_gates(
                ledger(first, second),
                [trusted_approval(first), trusted_approval(second)],
                repository_root=root,
                environment=self.environment,
                verified_at=lambda: "2026-09-02T20:01:00Z",
            )

            self.assertEqual(["first", "second"], [item.gate_id for item in results])
            self.assertEqual(["met", "met"], [item.state for item in results])
            self.assertTrue(all(item.executed for item in results))
            self.assertEqual("12", order_path.read_text(encoding="utf-8"))
            self.assertTrue(all(item.evidence_digest.startswith("sha256:") for item in results))
            self.assertEqual(
                [approval(first)["executionIdentityDigest"], approval(second)["executionIdentityDigest"]],
                [item.execution_identity_digest for item in results],
            )

    def test_zero_exit_without_exact_stdout_marker_is_unmet(self):
        with tempfile.TemporaryDirectory() as directory:
            gate = command_gate(
                "wrong-marker",
                render_direct_command([sys.executable, "-c", "print('almost', end='')"]),
                "stdout-exact:exact",
                self.environment_identity,
            )
            result = run_approved_gates(
                ledger(gate), [trusted_approval(gate)], repository_root=Path(directory),
                environment=self.environment,
            )[0]
            self.assertEqual("unmet", result.state)
            self.assertEqual(0, result.return_code)
            self.assertIn("stdout", result.reason)

    def test_artifact_sha256_is_evaluated_independently_and_bounded(self):
        payload = b"decisive artifact"
        expected = "sha256:" + hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            gate = command_gate(
                "artifact",
                render_direct_command(
                    [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; Path('proof.bin').write_bytes(b'decisive artifact')",
                    ]
                ),
                f"artifact-sha256:{expected.removeprefix('sha256:')}",
                self.environment_identity,
            )
            result = run_approved_gates(
                ledger(gate), [trusted_approval(gate, artifact_path="proof.bin")],
                repository_root=Path(directory), environment=self.environment,
            )[0]
            self.assertEqual("met", result.state)
            self.assertEqual(expected, result.artifact_digest)

            bad_gate = copy.deepcopy(gate)
            bad_gate["id"] = "artifact-mismatch"
            bad_gate["successMarker"] = "artifact-sha256:" + "0" * 64
            bad = run_approved_gates(
                ledger(bad_gate), [trusted_approval(bad_gate, artifact_path="proof.bin")],
                repository_root=Path(directory), environment=self.environment,
            )[0]
            self.assertEqual("unmet", bad.state)

    def test_preexisting_matching_artifact_without_content_transition_is_unmet(self):
        payload = b"already here"
        expected = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "proof.bin").write_bytes(payload)
            gate = command_gate(
                "stale-artifact",
                render_direct_command([sys.executable, "-c", "print('did not write', end='')"]),
                f"artifact-sha256:{expected}",
                self.environment_identity,
            )
            result = run_approved_gates(
                ledger(gate), [trusted_approval(gate, artifact_path="proof.bin")],
                repository_root=root, environment=self.environment,
            )[0]

        self.assertEqual("unmet", result.state)
        self.assertIn("fresh", result.reason)
        self.assertTrue(hasattr(result, "artifact_before_digest"))
        self.assertEqual("sha256:" + expected, result.artifact_before_digest)

    def test_missing_manual_or_changed_approval_never_launches(self):
        command = command_gate(
            "needs-approval",
            render_direct_command([sys.executable, "-c", "print('ok', end='')"]),
            "stdout-exact:ok",
            self.environment_identity,
        )
        manual = manual_gate("human-review", self.environment_identity)
        calls: list[object] = []

        def should_not_run(*args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args[0], 0, "ok", "")

        with tempfile.TemporaryDirectory() as directory:
            changed = approval(command)
            changed["execution"]["limits"]["timeoutMs"] += 1
            with self.assertRaises(GateRunnerError):
                trust(changed)
            results = run_approved_gates(
                ledger(command, manual), [], repository_root=Path(directory),
                environment=self.environment, process_runner=should_not_run,
            )

        self.assertEqual([], calls)
        self.assertEqual(["blocked", "blocked"], [item.state for item in results])
        self.assertIn("approval is missing", results[0].reason)
        self.assertIn("manual review", results[1].reason)

    def test_runtime_identity_and_changed_references_fail_closed_before_launch(self):
        calls: list[object] = []

        def should_not_run(*args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args[0], 0, "ok", "")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "fixture.txt"
            fixture.write_text("reviewed", encoding="utf-8")
            gate = command_gate(
                "reference-bound",
                render_direct_command([sys.executable, "-c", "print('ok', end='')", "fixture.txt"]),
                "stdout-exact:ok",
                self.environment_identity,
            )
            approved = approval(
                gate,
                references=(("fixture.txt", digest_file(fixture, max_bytes=1_048_576)),),
                reference_bindings=(("fixture.txt", 3),),
            )
            trusted = trust(approved)
            fixture.write_text("changed", encoding="utf-8")
            changed_reference = run_approved_gates(
                ledger(gate), [trusted], repository_root=root,
                environment=self.environment, process_runner=should_not_run,
            )[0]
            self.assertEqual("blocked", changed_reference.state)
            self.assertIn("referenced file", changed_reference.reason)

            fixture.write_text("reviewed", encoding="utf-8")
            changed_environment = dict(self.environment)
            changed_environment["COMPASS_D2_DRIFT"] = "1"
            wrong_environment = run_approved_gates(
                ledger(gate), [trust(approved)], repository_root=root,
                environment=changed_environment, process_runner=should_not_run,
            )[0]
            self.assertEqual("blocked", wrong_environment.state)
            self.assertIn("environment", wrong_environment.reason)

            wrong_executable = copy.deepcopy(approved)
            wrong_executable["execution"]["executable"]["digest"] = "sha256:" + "0" * 64
            wrong_executable["executionIdentityDigest"] = canonical_digest(
                wrong_executable["execution"]
            )
            wrong_executable_capability = trust(wrong_executable)
            executable_result = run_approved_gates(
                ledger(gate), [wrong_executable_capability], repository_root=root,
                environment=self.environment, process_runner=should_not_run,
            )[0]
            self.assertEqual("blocked", executable_result.state)
            self.assertIn("executable", executable_result.reason)

        self.assertEqual([], calls)

    def test_environment_is_snapshotted_once_before_identity_check_and_launch(self):
        stable = {"D2_ENV": "approved"}
        identity = environment_digest(stable, current_platform_identity())
        gate = command_gate(
            "environment-snapshot",
            render_direct_command([sys.executable, "-c", "print('ok', end='')"]),
            "stdout-exact:ok",
            identity,
        )

        class DriftingEnvironment(Mapping):
            def __init__(self):
                self.iterations = 0
                self.value = "approved"

            def __iter__(self):
                self.iterations += 1
                if self.iterations > 1:
                    self.value = "changed-after-check"
                return iter(("D2_ENV",))

            def __len__(self):
                return 1

            def __getitem__(self, key):
                return self.value

        drifting = DriftingEnvironment()
        observed: dict = {}

        def capture_environment(argv, **kwargs):
            observed.update(kwargs["environment"])
            return subprocess.CompletedProcess(argv, 0, "ok", "")

        with tempfile.TemporaryDirectory() as directory:
            result = run_approved_gates(
                ledger(gate), [trusted_approval(gate)], repository_root=Path(directory),
                environment=drifting, process_runner=capture_environment,
            )[0]

        self.assertEqual("met", result.state, result.reason)
        self.assertEqual({"D2_ENV": "approved"}, observed)
        self.assertEqual(1, drifting.iterations)

    def test_pre_launch_mutation_seam_is_explicit(self):
        self.assertIn("before_launch", inspect.signature(run_approved_gates).parameters)

    def test_pre_launch_reference_mutation_blocks_before_process_start(self):
        calls: list[object] = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fixture.txt"
            source.write_text("reviewed", encoding="utf-8")
            gate = command_gate(
                "pre-launch-mutation",
                render_direct_command([sys.executable, "-c", "print('ok', end='')", "fixture.txt"]),
                "stdout-exact:ok",
                self.environment_identity,
            )
            approved = trusted_approval(
                gate,
                references=(("fixture.txt", digest_file(source, max_bytes=1_048_576)),),
                reference_bindings=(("fixture.txt", 3),),
            )
            result = run_approved_gates(
                ledger(gate), [approved], repository_root=root,
                environment=self.environment,
                before_launch=lambda gate_id: source.write_text(
                    "changed-before-snapshot", encoding="utf-8"
                ),
                process_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
            )[0]

        self.assertEqual("blocked", result.state)
        self.assertFalse(result.executed)
        self.assertEqual([], calls)

    def test_launched_command_sees_immutable_staged_reference_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fixture.txt"
            source.write_text("reviewed", encoding="utf-8")
            gate = command_gate(
                "immutable-reference",
                render_direct_command([sys.executable, "-c", "print('ok', end='')", "fixture.txt"]),
                "stdout-exact:ok",
                self.environment_identity,
            )
            approved = trusted_approval(
                gate,
                references=(("fixture.txt", digest_file(source, max_bytes=1_048_576)),),
                reference_bindings=(("fixture.txt", 3),),
            )
            observed: dict = {}

            def mutate_source_before_observation(argv, **kwargs):
                source.write_text("attacker-changed", encoding="utf-8")
                observed["cwd"] = kwargs["cwd"]
                observed["boundArg"] = Path(argv[3])
                observed["content"] = Path(argv[3]).read_text(encoding="utf-8")
                return subprocess.CompletedProcess(argv, 0, "ok", "")

            result = run_approved_gates(
                ledger(gate), [approved], repository_root=root,
                environment=self.environment,
                process_runner=mutate_source_before_observation,
            )[0]

        self.assertEqual("met", result.state, result.reason)
        self.assertEqual("reviewed", observed["content"])
        self.assertTrue(observed["boundArg"].is_absolute())
        self.assertNotEqual(source, observed["boundArg"])
        self.assertNotEqual(root, observed["cwd"])

    def test_approval_gate_mismatch_is_blocked_even_when_record_is_self_consistent(self):
        gate = command_gate(
            "exact-gate",
            render_direct_command([sys.executable, "-c", "print('ok', end='')"]),
            "stdout-exact:ok",
            self.environment_identity,
        )
        approved = approval(gate)
        approved["execution"]["successMarker"] = "stdout-exact:different"
        approved["executionIdentityDigest"] = canonical_digest(approved["execution"])
        trusted = trust(approved)
        with tempfile.TemporaryDirectory() as directory:
            result = run_approved_gates(
                ledger(gate), [trusted], repository_root=Path(directory),
                environment=self.environment,
                process_runner=lambda *args, **kwargs: self.fail("must not launch"),
            )[0]
        self.assertEqual("blocked", result.state)
        self.assertIn("successMarker", result.reason)

    def test_closed_approval_validation_and_ambiguous_records_fail_before_launch(self):
        gate = command_gate(
            "closed",
            render_direct_command([sys.executable, "-c", "print('ok', end='')"]),
            "stdout-exact:ok",
            self.environment_identity,
        )
        approved = approval(gate)
        self.assertEqual(approved, validate_gate_approval(approved))
        extra = copy.deepcopy(approved)
        extra["autoApproved"] = True
        with self.assertRaises(GateRunnerError):
            validate_gate_approval(extra)

        with tempfile.TemporaryDirectory() as directory:
            trusted = trust(approved)
            with self.assertRaises(GateRunnerError):
                run_approved_gates(
                    ledger(gate), [trusted, trusted], repository_root=Path(directory),
                    environment=self.environment,
                )

    def test_direct_mode_rejects_contradictory_shell_identity(self):
        gate = command_gate(
            "wrong-shell",
            render_direct_command([sys.executable, "-c", "print('ok', end='')"]),
            "stdout-exact:ok",
            self.environment_identity,
        )
        candidate = approval(gate)
        candidate["execution"]["shell"] = "pwsh-7"
        candidate["executionIdentityDigest"] = canonical_digest(candidate["execution"])
        with self.assertRaises(GateRunnerError):
            validate_gate_approval(candidate)

    @unittest.skipUnless(os.name == "nt", "Windows shell-mediated executable rule")
    def test_direct_mode_rejects_cmd_and_bat_executables(self):
        with tempfile.TemporaryDirectory() as directory:
            for suffix in (".cmd", ".bat"):
                with self.subTest(suffix=suffix):
                    executable = Path(directory) / f"gate{suffix}"
                    executable.write_text("@echo off\r\n", encoding="utf-8")
                    gate = command_gate(
                        f"reject-{suffix[1:]}",
                        render_direct_command([str(executable.resolve())]),
                        "stdout-exact:ok",
                        self.environment_identity,
                    )
                    candidate = approval(gate)
                    candidate["execution"]["executable"] = {
                        "path": str(executable.resolve()),
                        "digest": digest_file(executable, max_bytes=1_048_576),
                    }
                    candidate["executionIdentityDigest"] = canonical_digest(
                        candidate["execution"]
                    )
                    with self.assertRaises(GateRunnerError):
                        validate_gate_approval(candidate)

    def test_raw_mapping_cannot_cross_the_trusted_approval_boundary(self):
        gate = command_gate(
            "raw-import",
            render_direct_command([sys.executable, "-c", "print('ok', end='')"]),
            "stdout-exact:ok",
            self.environment_identity,
        )
        calls: list[object] = []

        def unexpected_launch(*args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args[0], 0, "ok", "")

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(GateRunnerError):
                run_approved_gates(
                    ledger(gate), [approval(gate)], repository_root=Path(directory),
                    environment=self.environment,
                    process_runner=unexpected_launch,
                )
        self.assertEqual([], calls)

    def test_provider_denial_cannot_issue_a_trusted_capability(self):
        class DenyingProvider(ApprovalDecisionProvider):
            def approve(self, candidate) -> bool:
                return False

        gate = command_gate(
            "provider-denied",
            render_direct_command([sys.executable, "-c", "print('ok', end='')"]),
            "stdout-exact:ok",
            self.environment_identity,
        )
        with self.assertRaises(ApprovalBoundaryError):
            issue_trusted_gate_approval(approval(gate), DenyingProvider())

    def test_approval_requires_explicit_reference_bindings_field(self):
        gate = command_gate(
            "binding-contract",
            render_direct_command([sys.executable, "-c", "print('ok', end='')"]),
            "stdout-exact:ok",
            self.environment_identity,
        )
        candidate = approval(gate)
        del candidate["execution"]["referenceBindings"]
        with self.assertRaises(GateRunnerError):
            validate_gate_approval(candidate)

    def test_snapshot_failure_reports_not_executed(self):
        gate = command_gate(
            "snapshot-failure",
            render_direct_command([sys.executable, "-c", "print('ok', end='')"]),
            "stdout-exact:ok",
            self.environment_identity,
        )

        @contextmanager
        def failing_snapshot(**kwargs):
            from compass_builder.gate_snapshot import GateSnapshotError
            raise GateSnapshotError("injected snapshot failure")
            yield

        with tempfile.TemporaryDirectory() as directory, patch(
            "compass_builder.gate_runner.staged_execution_surface", failing_snapshot
        ):
            result = run_approved_gates(
                ledger(gate), [trusted_approval(gate)], repository_root=Path(directory),
                environment=self.environment,
            )[0]
        self.assertEqual("blocked", result.state)
        self.assertFalse(result.executed)

    def test_opaque_argv_and_environment_reference_forms_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fixture.txt"
            source.write_text("reviewed", encoding="utf-8")
            reference = (("fixture.txt", digest_file(source, max_bytes=1_048_576)),)
            calls: list[object] = []
            for gate_id, token, index in (
                ("embedded-code", "print('fixture.txt')", 2),
                ("equals-form", "--config=fixture.txt", 3),
                ("response-form", "@fixture.txt", 3),
            ):
                with self.subTest(gate_id=gate_id):
                    argv = [sys.executable, "-c", token]
                    if index == 3:
                        argv.append(token)
                        argv[2] = "print('ok', end='')"
                    gate = command_gate(
                        gate_id, render_direct_command(argv), "stdout-exact:ok",
                        self.environment_identity,
                    )
                    approved = trusted_approval(
                        gate, references=reference,
                        reference_bindings=(("fixture.txt", index),),
                    )
                    result = run_approved_gates(
                        ledger(gate), [approved], repository_root=root,
                        environment=self.environment,
                        process_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
                    )[0]
                    self.assertEqual("blocked", result.state)
                    self.assertFalse(result.executed)

            environment = dict(self.environment)
            environment["D2_CONFIG"] = "fixture.txt"
            environment_identity = environment_digest(environment, current_platform_identity())
            gate = command_gate(
                "environment-form",
                render_direct_command(
                    [sys.executable, "-c", "print('ok', end='')", "fixture.txt"]
                ),
                "stdout-exact:ok", environment_identity,
            )
            result = run_approved_gates(
                ledger(gate), [trusted_approval(
                    gate, references=reference,
                    reference_bindings=(("fixture.txt", 3),),
                )], repository_root=root, environment=environment,
                process_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
            )[0]
            self.assertEqual("blocked", result.state)
            self.assertFalse(result.executed)
            self.assertEqual([], calls)

    def test_canonical_equivalent_opaque_reference_spellings_fail_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "cfg" / "check.py"
            source.parent.mkdir()
            source.write_text("reviewed", encoding="utf-8")
            reference = (("cfg/check.py", digest_file(source, max_bytes=1_048_576)),)
            spellings = [
                "cfg/./check.py",
                f"{source.parent}{os.sep}.{os.sep}{source.name}",
            ]
            if os.name == "nt":
                spellings.extend(("cfg\\.\\check.py", "CFG/./CHECK.PY"))

            calls: list[object] = []
            for ordinal, spelling in enumerate(spellings):
                with self.subTest(spelling=spelling):
                    gate = command_gate(
                        f"canonical-opaque-{ordinal}",
                        render_direct_command([
                            sys.executable,
                            "-c",
                            "print('ok', end='')",
                            "cfg/check.py",
                            spelling,
                        ]),
                        "stdout-exact:ok",
                        self.environment_identity,
                    )
                    result = run_approved_gates(
                        ledger(gate),
                        [trusted_approval(
                            gate,
                            references=reference,
                            reference_bindings=(("cfg/check.py", 3),),
                        )],
                        repository_root=root,
                        environment=self.environment,
                        process_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
                    )[0]
                    self.assertEqual("blocked", result.state)
                    self.assertFalse(result.executed)

            environment_spellings = ["cfg/./check.py"]
            if os.name == "nt":
                environment_spellings.extend(("cfg\\.\\check.py", "CFG/./CHECK.PY"))
            for ordinal, spelling in enumerate(environment_spellings):
                with self.subTest(environment_spelling=spelling):
                    environment = dict(self.environment)
                    environment["D2_CONFIG"] = spelling
                    gate = command_gate(
                        f"canonical-opaque-environment-{ordinal}",
                        render_direct_command([
                            sys.executable,
                            "-c",
                            "print('ok', end='')",
                            "cfg/check.py",
                        ]),
                        "stdout-exact:ok",
                        environment_digest(environment, current_platform_identity()),
                    )
                    result = run_approved_gates(
                        ledger(gate),
                        [trusted_approval(
                            gate,
                            references=reference,
                            reference_bindings=(("cfg/check.py", 3),),
                        )],
                        repository_root=root,
                        environment=environment,
                        process_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
                    )[0]
                    self.assertEqual("blocked", result.state)
                    self.assertFalse(result.executed)
            self.assertEqual([], calls)

    @unittest.skipUnless(os.name == "nt", "Windows path identity regression")
    def test_reference_binding_path_requires_exact_case_preserving_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Case.txt"
            source.write_text("reviewed", encoding="utf-8")
            gate = command_gate(
                "case-sensitive-wire-identity",
                render_direct_command([
                    sys.executable,
                    "-c",
                    "print('ok', end='')",
                    "case.txt",
                ]),
                "stdout-exact:ok",
                self.environment_identity,
            )
            candidate = approval(
                gate,
                references=(("Case.txt", digest_file(source, max_bytes=1_048_576)),),
                reference_bindings=(("case.txt", 3),),
            )
            with self.assertRaises(GateRunnerError):
                validate_gate_approval(candidate)

            calls: list[object] = []
            with patch(
                "compass_builder.gate_runner.consume_trusted_gate_approval",
                return_value=candidate,
            ), patch(
                "compass_builder.gate_runner.validate_gate_approval",
                side_effect=lambda record: record,
            ):
                result = run_approved_gates(
                    ledger(gate),
                    [trusted_approval(gate)],
                    repository_root=Path(directory),
                    environment=self.environment,
                    process_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
                )[0]
            self.assertEqual("blocked", result.state)
            self.assertFalse(result.executed)
            self.assertIn("reference bindings", result.reason)
            self.assertEqual([], calls)

    def test_artifact_path_cannot_alias_a_repository_reference_through_gate_cwd(self):
        expected = hashlib.sha256(b"proof").hexdigest()
        gate = command_gate(
            "artifact-reference-alias",
            render_direct_command([
                sys.executable,
                "-c",
                "print('unused')",
                "sub/proof.bin",
            ]),
            f"artifact-sha256:{expected}",
            self.environment_identity,
            working_directory="sub",
        )
        candidate = approval(
            gate,
            references=(("sub/proof.bin", f"sha256:{expected}"),),
            reference_bindings=(("sub/proof.bin", 3),),
            artifact_path="proof.bin",
        )
        with self.assertRaisesRegex(GateRunnerError, "read-only referenced input"):
            validate_gate_approval(candidate)

    def test_canonical_executable_rejects_lexical_dot_segments(self):
        executable = Path(sys.executable).resolve()
        alias = f"{executable.parent}{os.sep}.{os.sep}{executable.name}"
        gate = command_gate(
            "dot-segment-executable",
            render_direct_command([alias, "-c", "print('ok', end='')"]),
            "stdout-exact:ok",
            self.environment_identity,
        )
        candidate = approval(gate)
        candidate["execution"]["executable"]["path"] = alias
        candidate["executionIdentityDigest"] = canonical_digest(candidate["execution"])
        with self.assertRaisesRegex(GateRunnerError, "dot segment"):
            validate_gate_approval(candidate)

    def test_approval_binds_platform_specific_executable_isolation_identity(self):
        gate = command_gate(
            "isolation-identity",
            render_direct_command([sys.executable, "-c", "print('ok', end='')"]),
            "stdout-exact:ok",
            self.environment_identity,
        )
        validated = validate_gate_approval(approval(gate))
        expected = (
            "windows-locked-original-v1"
            if os.name == "nt"
            else "posix-staged-copy-v1"
        )
        self.assertEqual(expected, validated["execution"]["isolation"]["mode"])

    @unittest.skipIf(os.name == "nt", "POSIX executable-mode staging contract")
    def test_posix_staging_preserves_approved_execute_bits_without_write_bits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "gate-check"
            executable.write_text("#!/bin/sh\nprintf ok", encoding="utf-8")
            executable.chmod(0o751)
            gate = command_gate(
                "posix-mode-preservation",
                render_direct_command([str(executable.resolve())]),
                "stdout-exact:ok",
                self.environment_identity,
            )
            candidate = approval(gate)
            candidate["execution"]["executable"] = {
                "path": str(executable.resolve()),
                "digest": digest_file(executable, max_bytes=134_217_728),
            }
            candidate["execution"]["isolation"] = {
                "mode": "posix-staged-copy-v1",
                "sourceExecutableMode": 0o751,
                "stagedExecutableMode": 0o551,
            }
            candidate["executionIdentityDigest"] = canonical_digest(candidate["execution"])
            observed: dict[str, int] = {}

            def inspect_mode(argv, **kwargs):
                observed["mode"] = stat.S_IMODE(Path(argv[0]).stat().st_mode)
                return subprocess.CompletedProcess(argv, 0, "ok", "")

            result = run_approved_gates(
                ledger(gate),
                [trust(candidate)],
                repository_root=root,
                environment=self.environment,
                process_runner=inspect_mode,
            )[0]
            self.assertEqual("met", result.state, result.reason)
            self.assertEqual(0o551, observed["mode"])
            self.assertEqual(0o551 & 0o111, observed["mode"] & 0o111)

    @unittest.skipIf(os.name == "nt", "POSIX executable relocation contract")
    def test_posix_relocation_sensitive_executable_failure_is_unmet(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "relocation-check"
            executable.write_text(
                "#!/bin/sh\n[ \"$0\" = \"$EXPECTED_GATE_PATH\" ] || exit 73\nprintf ok",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            environment = dict(self.environment)
            environment["EXPECTED_GATE_PATH"] = str(executable.resolve())
            gate = command_gate(
                "posix-relocation-sensitive",
                render_direct_command([str(executable.resolve())]),
                "stdout-exact:ok",
                environment_digest(environment, current_platform_identity()),
            )
            candidate = approval(gate)
            candidate["execution"]["executable"] = {
                "path": str(executable.resolve()),
                "digest": digest_file(executable, max_bytes=134_217_728),
            }
            candidate["execution"]["isolation"] = {
                "mode": "posix-staged-copy-v1",
                "sourceExecutableMode": 0o755,
                "stagedExecutableMode": 0o555,
            }
            candidate["executionIdentityDigest"] = canonical_digest(candidate["execution"])
            result = run_approved_gates(
                ledger(gate),
                [trust(candidate)],
                repository_root=root,
                environment=environment,
            )[0]
            self.assertEqual("unmet", result.state)
            self.assertTrue(result.executed)
            self.assertEqual(73, result.return_code)

    @unittest.skipIf(os.name == "nt", "POSIX executable-mode approval contract")
    def test_posix_non_executable_source_is_never_promoted_to_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "not-executable"
            executable.write_text("#!/bin/sh\nprintf unsafe", encoding="utf-8")
            executable.chmod(0o644)
            gate = command_gate(
                "posix-non-executable",
                render_direct_command([str(executable.resolve())]),
                "stdout-exact:unsafe",
                self.environment_identity,
            )
            candidate = approval(gate)
            candidate["execution"]["executable"] = {
                "path": str(executable.resolve()),
                "digest": digest_file(executable, max_bytes=134_217_728),
            }
            candidate["execution"]["isolation"] = {
                "mode": "posix-staged-copy-v1",
                "sourceExecutableMode": 0o644,
                "stagedExecutableMode": 0o444,
            }
            candidate["executionIdentityDigest"] = canonical_digest(candidate["execution"])
            with self.assertRaisesRegex(GateRunnerError, "execute bit"):
                validate_gate_approval(candidate)

    @unittest.skipUnless(os.name == "nt", "Windows extended-path identity")
    def test_windows_extended_device_reference_alias_fails_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "cfg" / "check.py"
            source.parent.mkdir()
            source.write_text("reviewed", encoding="utf-8")
            resolved_root = root.resolve()
            device_name = ctypes.create_unicode_buffer(32768)
            query_dos_device = ctypes.windll.kernel32.QueryDosDeviceW
            if not query_dos_device(resolved_root.drive, device_name, len(device_name)):
                self.fail("could not resolve the temporary drive device identity")
            extended = (
                "\\\\?\\GLOBALROOT"
                + device_name.value
                + str(resolved_root)[len(resolved_root.drive):]
                + "\\cfg\\check.py"
            )
            self.assertEqual("reviewed", Path(extended).read_text(encoding="utf-8"))
            gate = command_gate(
                "extended-device-alias",
                render_direct_command([
                    sys.executable,
                    "-c",
                    "print('ok', end='')",
                    "cfg/check.py",
                    extended,
                ]),
                "stdout-exact:ok",
                self.environment_identity,
            )
            calls: list[object] = []
            result = run_approved_gates(
                ledger(gate),
                [trusted_approval(
                    gate,
                    references=((
                        "cfg/check.py",
                        digest_file(source, max_bytes=1_048_576),
                    ),),
                    reference_bindings=(("cfg/check.py", 3),),
                )],
                repository_root=root,
                environment=self.environment,
                process_runner=lambda argv, **kwargs: (
                    calls.append((argv, kwargs))
                    or subprocess.CompletedProcess(argv, 0, "ok", "")
                ),
            )[0]
            self.assertEqual("blocked", result.state)
            self.assertFalse(result.executed)
            self.assertEqual([], calls)

    def test_dot_segment_repository_root_cannot_hide_canonical_absolute_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            canonical_root = Path(directory).resolve()
            (canonical_root / "hop").mkdir()
            source = canonical_root / "cfg" / "check.py"
            source.parent.mkdir()
            source.write_text("reviewed", encoding="utf-8")
            root_alias = canonical_root / "hop" / ".."
            gate = command_gate(
                "canonical-root-static-reference",
                render_direct_command([
                    sys.executable,
                    "-c",
                    "print('ok', end='')",
                    "cfg/check.py",
                    str(source),
                ]),
                "stdout-exact:ok",
                self.environment_identity,
            )
            calls: list[object] = []
            result = run_approved_gates(
                ledger(gate),
                [trusted_approval(
                    gate,
                    references=((
                        "cfg/check.py",
                        digest_file(source, max_bytes=1_048_576),
                    ),),
                    reference_bindings=(("cfg/check.py", 3),),
                )],
                repository_root=root_alias,
                environment=self.environment,
                process_runner=lambda argv, **kwargs: (
                    calls.append((argv, kwargs))
                    or subprocess.CompletedProcess(argv, 0, "ok", "")
                ),
            )[0]
            self.assertEqual("blocked", result.state)
            self.assertFalse(result.executed)
            self.assertEqual([], calls)

    def test_approved_limits_are_forwarded_and_bound_failures_are_blocked(self):
        gate = command_gate(
            "bounded",
            render_direct_command([sys.executable, "-c", "print('ok', end='')"]),
            "stdout-exact:ok",
            self.environment_identity,
        )
        approved = approval(gate, timeout_ms=1234, max_output_bytes=37)
        observed: dict = {}

        def bounded_failure(argv, **kwargs):
            observed.update(kwargs)
            raise BoundedProcessError("process output exceeded 37 bytes", stdout=b"x" * 37)

        with tempfile.TemporaryDirectory() as directory:
            result = run_approved_gates(
                ledger(gate), [trust(approved)], repository_root=Path(directory),
                environment=self.environment, process_runner=bounded_failure,
            )[0]

        self.assertEqual("blocked", result.state)
        self.assertTrue(result.executed)
        self.assertEqual(1.234, observed["timeout"])
        self.assertEqual(37, observed["max_output_bytes"])
        self.assertIsNone(observed["stdin"])
        self.assertIn("terminate_process_group_on_parent_exit", observed)
        self.assertIs(True, observed["terminate_process_group_on_parent_exit"])
        self.assertEqual("x" * 37, result.stdout)

    def test_relative_argv0_is_rejected_when_gate_cwd_differs_from_controller_cwd(self):
        relative_executable = Path(sys.executable).name
        gate = command_gate(
            "absolute-executable",
            render_direct_command([relative_executable, "-c", "print('ok', end='')"]),
            "stdout-exact:ok",
            self.environment_identity,
            working_directory="gate-cwd",
        )
        approved = approval(gate)
        calls: list[object] = []

        def must_not_run(*args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args[0], 0, "ok", "")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "gate-cwd").mkdir()
            result = run_approved_gates(
                ledger(gate), [trust(approved)], repository_root=root,
                environment=self.environment, process_runner=must_not_run,
            )[0]

        self.assertEqual("blocked", result.state)
        self.assertIn("absolute", result.reason)
        self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
