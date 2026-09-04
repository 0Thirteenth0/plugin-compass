from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "plugins" / "compass-builder"
if str(BUILDER) not in sys.path:
    sys.path.insert(0, str(BUILDER))

from compass_builder.controller import ControllerError, execute_run  # noqa: E402
import compass_builder.controller as controller_module  # noqa: E402
from compass_builder.cleanup import cleanup_run  # noqa: E402
from compass_builder.execution_bundle import build_gated_execution_bundle  # noqa: E402
from compass_builder.gate_enforcement import OperatorGateProvider  # noqa: E402
from compass_builder.gate_evidence import GateEvidenceJournal  # noqa: E402
from compass_builder.gate_runner import current_platform_identity, environment_digest  # noqa: E402
from compass_builder.git_environment import prepare_git_environment  # noqa: E402
from compass_builder.models import canonical_json  # noqa: E402
from compass_builder.state import StateStore, build_execution_bundle  # noqa: E402
from compass_builder.verifier import VerificationError  # noqa: E402
from tests.helpers.git_repo_factory import GitRepoFactory  # noqa: E402
from tests.test_builder_gate_runner import manual_gate  # noqa: E402


CHECK = 'python -c "raise SystemExit(0)"'


def _prepared_gate_environment(factory: GitRepoFactory, run_id: str):
    run_root = factory.repo / ".compass-builder" / "runs" / run_id
    git_root = run_root / "git-environment"
    probe = prepare_git_environment(git_root)
    identity = environment_digest(probe.environment, current_platform_identity())
    shutil.rmtree(run_root)

    def prepare_at_durable_root(controller_root: Path):
        if Path(controller_root).resolve(strict=False) != git_root.resolve(strict=False):
            raise AssertionError("controller requested a non-durable Git environment")
        return prepare_git_environment(git_root)

    return prepare_at_durable_root, identity


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def _inputs(factory: GitRepoFactory, base_sha: str, mode: str):
    host = json.loads((
        ROOT / "tests" / "fixtures" / "compass_builder" / "host-capabilities.valid.json"
    ).read_text(encoding="utf-8"))
    suffix = "1" if mode == "sequential" else "2"
    run_id = f"cb-integration-{mode}-{suffix * 16}"
    stories = []
    planned = []
    for index, story_id in enumerate(("alpha", "beta")):
        stories.append({
            "id": story_id, "title": story_id.title(),
            "description": f"Implement {story_id} safely.", "dependsOn": [],
            "writeScopes": [f"src/{story_id}"],
            "acceptanceChecks": [f"{story_id} check passes."],
            "validationCommands": [CHECK], "independentReviewPath": None,
            "sharedState": {"mode": "none", "description": "No shared state."},
            "priority": index + 1, "completionState": "pending",
            "complexity": "medium", "ambiguity": "low", "risk": "low",
            "validationStrength": "decisive",
        })
        planned.append({
            "storyId": story_id, "branch": f"cb/{run_id}/{story_id}",
            "recommendedEffort": "low", "handoffDigest": "sha256:" + str(index + 1) * 64,
        })
    spec = {
        "schemaVersion": "compass-builder.run-spec.v1", "runId": run_id,
        "baseRef": "refs/heads/main", "baseSha": base_sha,
        "integrationBranch": "main", "integrationExpectedSha": base_sha,
        "mode": mode, "exactModel": host["selectedModel"],
        "effortPolicyVersion": "plugin-compass.effort-policy.v1",
        "hostConcurrencyCeiling": 2, "userConcurrencyCeiling": 2,
        "validationCommands": [CHECK], "stories": stories,
    }
    width = 1 if mode == "sequential" else 2
    waves = (
        [{"waveIndex": 0, "storyIds": ["alpha"]},
         {"waveIndex": 1, "storyIds": ["beta"]}]
        if width == 1 else [{"waveIndex": 0, "storyIds": ["alpha", "beta"]}]
    )
    plan = {
        "schemaVersion": "compass-builder.wave-plan.v1", "runId": run_id,
        "baseSha": base_sha, "integrationBranch": "main",
        "integrationExpectedSha": base_sha, "normalizedInputDigest": _digest(spec),
        "hostEvidenceDigest": _digest(host),
        "effortPolicyVersion": "plugin-compass.effort-policy.v1", "mode": mode,
        "reasons": [f"Synthetic {mode} fixture."], "concurrency": width,
        "stories": planned, "waves": waves,
    }
    return build_execution_bundle(
        spec, plan, host, "2026-09-01T12:01:00Z", factory.repo,
    )


def commit_transport(launch, _story, _timeout_ms, _event_sink):
    worktree = Path(str(launch.record["worktree"]))
    story_id = str(launch.record["storyId"])
    path = worktree / "src" / story_id / "value.txt"
    path.write_text("after\n", encoding="utf-8", newline="\n")
    environment = dict(launch.environment)
    for args in (("add", "--all"), ("commit", "-m", f"{story_id} worker")):
        subprocess.run(
            ["git", "--no-pager", "-C", str(worktree), *args],
            check=True, capture_output=True, shell=False, env=environment,
        )
    head = subprocess.run(
        ["git", "--no-pager", "-C", str(worktree), "rev-parse", "HEAD"],
        check=True, capture_output=True, shell=False, env=environment,
    ).stdout.decode().strip()
    return {
        "schemaVersion": "compass-builder.worker-receipt.v1",
        "runId": launch.record["runId"], "storyId": story_id,
        "branch": launch.record["branch"], "worktree": str(worktree),
        "exactModel": launch.record["exactModel"], "effort": launch.record["effort"],
        "baseSha": launch.record["workerStartSha"], "headSha": head, "commitSha": head,
        "changedFiles": [{
            "path": f"src/{story_id}/value.txt", "sourcePath": None,
            "changeType": "modified",
        }],
        "checks": [{
            "name": "focused", "command": CHECK, "status": "passed",
            "evidenceDigest": "sha256:" + "e" * 64,
        }],
        "elapsedMs": 1, "status": "succeeded", "blocker": None,
    }


class _ApprovingOperator(OperatorGateProvider):
    def __init__(
        self, order: list[str], repository: Path, run_id: str,
        *, deny_scope: str | None = None, unavailable_scope: str | None = None,
        authenticate_seals: bool = True,
    ):
        self.order = order
        self.repository = repository
        self.run_id = run_id
        self.deny_scope = deny_scope
        self.unavailable_scope = unavailable_scope
        self.authenticate_seals = authenticate_seals
        self.count = 0
        self.requests: list[dict] = []
        self.story_ref_absent: list[bool] = []
        self.root_verified_sha_retained: list[bool] = []
        self.evidence_key = b"builder-integration-provider-key"
        self.checkpoints: dict[str, dict] = {}
        self.initialized_runs: set[str] = set()
        self.command_reservations: dict[str, dict] = {}
        self.consumed_command_ids: set[str] = set()

    def decide(self, request):
        if request["gateScope"] == self.unavailable_scope:
            raise RuntimeError("operator broker unavailable")
        self.count += 1
        self.requests.append(dict(request))
        self.order.append(f"gate:{request['gateScope']}:{request['storyId']}")
        if request["gateScope"] == "story":
            ref = f"refs/heads/cb/{self.run_id}/{request['storyId']}"
            observed = subprocess.run(
                ["git", "-C", str(self.repository), "show-ref", "--verify", "--quiet", ref],
                check=False, capture_output=True, shell=False,
            )
            self.story_ref_absent.append(observed.returncode != 0)
        else:
            state = json.loads((
                self.repository / ".compass-builder" / "runs" / self.run_id / "state.json"
            ).read_text(encoding="utf-8"))
            self.root_verified_sha_retained.append(
                state["state"] == "wave-integrated-unverified"
                and state["expectedIntegrationSha"] == request["targetSha"]
                and state["lastVerifiedIntegrationSha"] != request["targetSha"]
            )
        return {
            "approvalId": f"integration-decision-{self.count}",
            "approvedBy": "operator:integration-test",
            "approvedAt": f"2026-09-02T20:01:{self.count:02d}Z",
            "decisionState": (
                "approved" if request["gateScope"] != self.deny_scope else "denied"
            ),
            "commandApproval": None,
        }

    def seal_receipt(self, receipt):
        return "hmac-sha256:" + hmac.new(
            self.evidence_key, canonical_json(receipt), hashlib.sha256
        ).hexdigest()

    def authenticate_receipt(self, receipt):
        if not self.authenticate_seals:
            return False
        candidate = dict(receipt)
        provided = candidate.pop("providerSeal", None)
        return isinstance(provided, str) and hmac.compare_digest(
            provided, self.seal_receipt(candidate)
        )

    def read_evidence_checkpoint(self, run_id):
        value = self.checkpoints.get(run_id)
        return None if value is None else dict(value)

    def initialize_evidence_checkpoint(self, current):
        run_id = current["runId"]
        if run_id in self.initialized_runs or run_id in self.checkpoints:
            raise RuntimeError("provider lifecycle was already initialized")
        self.initialized_runs.add(run_id)
        self.checkpoints[run_id] = dict(current)

    def reserve_command_execution(self, reservation):
        value = dict(reservation)
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
        value = dict(reservation)
        existing = self.command_reservations.get(value["attemptKey"])
        if existing is None or existing["reservation"] != value:
            raise RuntimeError("command execution reservation is unavailable")
        if not self.authenticate_receipt(receipt):
            raise RuntimeError("command execution receipt is not authenticated")
        receipt_digest = _digest(receipt)
        if existing["state"] == "evidenced":
            if existing.get("receiptDigest") != receipt_digest:
                raise RuntimeError("command execution completion conflicts with history")
            return
        existing["state"] = "evidenced"
        existing["receiptDigest"] = receipt_digest

    def advance_evidence_checkpoint(self, previous, current):
        run_id = current["runId"]
        expected = None if previous is None else dict(previous)
        if self.checkpoints.get(run_id) != expected:
            raise RuntimeError("provider checkpoint compare-and-swap failed")
        if run_id not in self.initialized_runs:
            if previous is not None:
                raise RuntimeError("provider lifecycle is not initialized")
            self.initialized_runs.add(run_id)
        self.checkpoints[run_id] = dict(current)


class BuilderWorktreeIntegrationTests(unittest.TestCase):
    def test_v2_preexisting_destination_ref_blocks_before_story_gate_without_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            factory = GitRepoFactory(Path(temporary))
            base_sha = factory.init({
                "src/alpha/value.txt": "before\n",
                "src/beta/value.txt": "before\n",
                "docs/review.md": "approved review\n",
            })
            v1 = _inputs(factory, base_sha, "sequential")
            prepared, identity = _prepared_gate_environment(
                factory, v1["runSpec"]["runId"]
            )
            gate = manual_gate("story-alpha", identity)
            gate.update(gateScope="story", storyId="alpha")
            ledger = {
                "schemaVersion": "compass-builder.outcome-gate-ledger.v1",
                "controller": "compass-builder", "runId": v1["runSpec"]["runId"],
                "requiredRequirementIds": ["R100"],
                "requiredAcceptanceIds": ["A100"], "gates": [gate],
            }
            bundle = build_gated_execution_bundle(
                v1["runSpec"], v1["wavePlan"], v1["hostCapabilities"],
                v1["planningTimestamp"], factory.repo, ledger,
            )
            observed: dict[str, str] = {}

            def precreate_destination_ref(launch, story, timeout_ms, event_sink):
                receipt = commit_transport(launch, story, timeout_ms, event_sink)
                destination_ref = f"refs/heads/{receipt['branch']}"
                subprocess.run(
                    [
                        "git", "-C", str(factory.repo), "fetch", "--no-tags",
                        str(receipt["worktree"]),
                        f"{receipt['headSha']}:{destination_ref}",
                    ],
                    check=True, capture_output=True, shell=False,
                )
                observed.update(ref=destination_ref, sha=receipt["headSha"])
                return receipt

            provider = _ApprovingOperator(
                [], factory.repo, v1["runSpec"]["runId"], deny_scope="story"
            )
            with patch.object(
                controller_module, "prepare_git_environment", side_effect=prepared,
            ), self.assertRaisesRegex(ControllerError, "destination.*ref.*exists|preexisting.*ref"):
                execute_run(
                    factory.repo, bundle, worker_transport=precreate_destination_ref,
                    operator_gate_provider=provider, timeout_ms=30_000,
                )
            self.assertEqual([], provider.requests)
            self.assertEqual(observed["sha"], factory.sha(observed["ref"]))
            state = json.loads((
                factory.repo / ".compass-builder" / "runs" / v1["runSpec"]["runId"] / "state.json"
            ).read_text(encoding="utf-8"))
            self.assertEqual("verification", state["activeBlocker"]["phase"])

    def test_v2_provider_seal_authentication_mismatch_blocks_before_ref_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            factory = GitRepoFactory(Path(temporary))
            base_sha = factory.init({
                "src/alpha/value.txt": "before\n",
                "src/beta/value.txt": "before\n",
                "docs/review.md": "approved review\n",
            })
            v1 = _inputs(factory, base_sha, "sequential")
            prepared, identity = _prepared_gate_environment(
                factory, v1["runSpec"]["runId"]
            )
            gate = manual_gate("story-alpha", identity)
            gate.update(gateScope="story", storyId="alpha")
            ledger = {
                "schemaVersion": "compass-builder.outcome-gate-ledger.v1",
                "controller": "compass-builder", "runId": v1["runSpec"]["runId"],
                "requiredRequirementIds": ["R100"],
                "requiredAcceptanceIds": ["A100"], "gates": [gate],
            }
            bundle = build_gated_execution_bundle(
                v1["runSpec"], v1["wavePlan"], v1["hostCapabilities"],
                v1["planningTimestamp"], factory.repo, ledger,
            )
            provider = _ApprovingOperator(
                [], factory.repo, v1["runSpec"]["runId"], authenticate_seals=False
            )
            with patch.object(
                controller_module, "prepare_git_environment", side_effect=prepared,
            ), self.assertRaisesRegex(ControllerError, "authenticate|seal|evidence"):
                execute_run(
                    factory.repo, bundle, worker_transport=commit_transport,
                    operator_gate_provider=provider, timeout_ms=30_000,
                )
            state = json.loads((
                factory.repo / ".compass-builder" / "runs" / v1["runSpec"]["runId"] / "state.json"
            ).read_text(encoding="utf-8"))
            self.assertEqual("verification", state["activeBlocker"]["phase"])
            destination_ref = f"refs/heads/cb/{v1['runSpec']['runId']}/alpha"
            self.assertNotEqual(
                0,
                subprocess.run(
                    ["git", "-C", str(factory.repo), "show-ref", "--verify", "--quiet", destination_ref],
                    check=False, capture_output=True, shell=False,
                ).returncode,
            )
            self.assertEqual(
                0, provider.checkpoints[v1["runSpec"]["runId"]]["receiptCount"]
            )
            evidence = (
                factory.repo / ".compass-builder" / "runs"
                / v1["runSpec"]["runId"] / "gate-evidence"
            )
            self.assertEqual(0, len(tuple(evidence.glob("*.json"))))

    def test_v2_worker_verification_failure_is_a_verification_blocker_before_ref_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            factory = GitRepoFactory(Path(temporary))
            base_sha = factory.init({
                "src/alpha/value.txt": "before\n",
                "src/beta/value.txt": "before\n",
                "docs/review.md": "approved review\n",
            })
            v1 = _inputs(factory, base_sha, "sequential")
            prepared, identity = _prepared_gate_environment(
                factory, v1["runSpec"]["runId"]
            )
            gate = manual_gate("story-alpha", identity)
            gate.update(gateScope="story", storyId="alpha")
            ledger = {
                "schemaVersion": "compass-builder.outcome-gate-ledger.v1",
                "controller": "compass-builder", "runId": v1["runSpec"]["runId"],
                "requiredRequirementIds": ["R100"],
                "requiredAcceptanceIds": ["A100"], "gates": [gate],
            }
            bundle = build_gated_execution_bundle(
                v1["runSpec"], v1["wavePlan"], v1["hostCapabilities"],
                v1["planningTimestamp"], factory.repo, ledger,
            )
            provider = _ApprovingOperator([], factory.repo, v1["runSpec"]["runId"])
            with patch.object(
                controller_module, "prepare_git_environment", side_effect=prepared,
            ), patch.object(
                controller_module, "verify_worker",
                side_effect=VerificationError("synthetic independent verification failure"),
            ), self.assertRaisesRegex(ControllerError, "independent verification failure"):
                execute_run(
                    factory.repo, bundle, worker_transport=commit_transport,
                    operator_gate_provider=provider, timeout_ms=30_000,
                )
            destination_ref = f"refs/heads/cb/{v1['runSpec']['runId']}/alpha"
            self.assertNotEqual(
                0,
                subprocess.run(
                    ["git", "-C", str(factory.repo), "show-ref", "--verify", "--quiet", destination_ref],
                    check=False, capture_output=True, shell=False,
                ).returncode,
            )
            state = json.loads((
                factory.repo / ".compass-builder" / "runs" / v1["runSpec"]["runId"] / "state.json"
            ).read_text(encoding="utf-8"))
            self.assertEqual("verification", state["activeBlocker"]["phase"])
            self.assertEqual("alpha", state["activeBlocker"]["storyId"])

    def test_v2_final_completion_refolds_and_blocks_if_gate_history_is_deleted(self):
        with tempfile.TemporaryDirectory() as temporary:
            factory = GitRepoFactory(Path(temporary))
            base_sha = factory.init({
                "src/alpha/value.txt": "before\n",
                "src/beta/value.txt": "before\n",
                "docs/review.md": "approved review\n",
            })
            v1 = _inputs(factory, base_sha, "parallel")
            prepared, identity = _prepared_gate_environment(
                factory, v1["runSpec"]["runId"]
            )
            gates = []
            for story_id in ("alpha", "beta"):
                gate = manual_gate(f"story-{story_id}", identity)
                gate.update(gateScope="story", storyId=story_id)
                gates.append(gate)
            gates.append(manual_gate("root-after-merge", identity))
            ledger = {
                "schemaVersion": "compass-builder.outcome-gate-ledger.v1",
                "controller": "compass-builder", "runId": v1["runSpec"]["runId"],
                "requiredRequirementIds": ["R100"],
                "requiredAcceptanceIds": ["A100"], "gates": gates,
            }
            bundle = build_gated_execution_bundle(
                v1["runSpec"], v1["wavePlan"], v1["hostCapabilities"],
                v1["planningTimestamp"], factory.repo, ledger,
            )
            provider = _ApprovingOperator([], factory.repo, v1["runSpec"]["runId"])
            real_integrate = controller_module.integrate_verified_branch

            def delete_history_after_last_verified_merge(*args, **kwargs):
                result = real_integrate(*args, **kwargs)
                receipt = args[2]
                if receipt["storyId"] == "beta":
                    evidence = (
                        factory.repo / ".compass-builder" / "runs"
                        / v1["runSpec"]["runId"] / "gate-evidence"
                    )
                    first = min(
                        evidence.glob("*.json"),
                        key=lambda path: json.loads(path.read_text(encoding="utf-8"))["sequence"],
                    )
                    first.unlink()
                return result

            with patch.object(
                controller_module, "prepare_git_environment", side_effect=prepared,
            ), patch.object(
                controller_module, "integrate_verified_branch",
                side_effect=delete_history_after_last_verified_merge,
            ), self.assertRaisesRegex(ControllerError, "gate-evidence|checkpoint|chain|incomplete"):
                execute_run(
                    factory.repo, bundle, worker_transport=commit_transport,
                    operator_gate_provider=provider, timeout_ms=30_000,
                )
            state = json.loads((
                factory.repo / ".compass-builder" / "runs" / v1["runSpec"]["runId"] / "state.json"
            ).read_text(encoding="utf-8"))
            self.assertNotEqual("completed", state["state"])
            self.assertEqual(factory.sha("HEAD"), state["lastVerifiedIntegrationSha"])

    def test_v2_unavailable_story_decision_records_verification_handoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            factory = GitRepoFactory(Path(temporary))
            base_sha = factory.init({
                "src/alpha/value.txt": "before\n",
                "src/beta/value.txt": "before\n",
                "docs/review.md": "approved review\n",
            })
            v1 = _inputs(factory, base_sha, "sequential")
            prepared, identity = _prepared_gate_environment(
                factory, v1["runSpec"]["runId"]
            )
            gate = manual_gate("story-alpha", identity)
            gate.update(gateScope="story", storyId="alpha")
            ledger = {
                "schemaVersion": "compass-builder.outcome-gate-ledger.v1",
                "controller": "compass-builder", "runId": v1["runSpec"]["runId"],
                "requiredRequirementIds": ["R100"],
                "requiredAcceptanceIds": ["A100"], "gates": [gate],
            }
            bundle = build_gated_execution_bundle(
                v1["runSpec"], v1["wavePlan"], v1["hostCapabilities"],
                v1["planningTimestamp"], factory.repo, ledger,
            )
            provider = _ApprovingOperator(
                [], factory.repo, v1["runSpec"]["runId"], unavailable_scope="story"
            )
            with patch.object(
                controller_module, "prepare_git_environment", side_effect=prepared,
            ), self.assertRaisesRegex(ControllerError, "operator decision is unavailable"):
                execute_run(
                    factory.repo, bundle, worker_transport=commit_transport,
                    operator_gate_provider=provider, timeout_ms=30_000,
                )
            state = json.loads((
                factory.repo / ".compass-builder" / "runs" / v1["runSpec"]["runId"] / "state.json"
            ).read_text(encoding="utf-8"))
            self.assertEqual("verification", state["activeBlocker"]["phase"])
            self.assertEqual("wave-workers-complete", state["activeBlocker"]["resumeState"])
            destination_ref = f"refs/heads/cb/{v1['runSpec']['runId']}/alpha"
            self.assertNotEqual(
                0,
                subprocess.run(
                    ["git", "-C", str(factory.repo), "show-ref", "--verify", "--quiet", destination_ref],
                    check=False, capture_output=True, shell=False,
                ).returncode,
            )

    def test_v2_story_gate_denial_blocks_verification_and_never_imports_ref(self):
        with tempfile.TemporaryDirectory() as temporary:
            factory = GitRepoFactory(Path(temporary))
            base_sha = factory.init({
                "src/alpha/value.txt": "before\n",
                "src/beta/value.txt": "before\n",
                "docs/review.md": "approved review\n",
            })
            v1 = _inputs(factory, base_sha, "sequential")
            prepared, identity = _prepared_gate_environment(
                factory, v1["runSpec"]["runId"]
            )
            gate = manual_gate("story-alpha", identity)
            gate.update(gateScope="story", storyId="alpha")
            ledger = {
                "schemaVersion": "compass-builder.outcome-gate-ledger.v1",
                "controller": "compass-builder", "runId": v1["runSpec"]["runId"],
                "requiredRequirementIds": ["R100"],
                "requiredAcceptanceIds": ["A100"], "gates": [gate],
            }
            bundle = build_gated_execution_bundle(
                v1["runSpec"], v1["wavePlan"], v1["hostCapabilities"],
                v1["planningTimestamp"], factory.repo, ledger,
            )
            provider = _ApprovingOperator(
                [], factory.repo, v1["runSpec"]["runId"], deny_scope="story"
            )
            with patch.object(
                controller_module, "prepare_git_environment", side_effect=prepared,
            ), self.assertRaisesRegex(ControllerError, "required story outcome gate"):
                execute_run(
                    factory.repo, bundle, worker_transport=commit_transport,
                    operator_gate_provider=provider, timeout_ms=30_000,
                )
            destination_ref = f"refs/heads/cb/{v1['runSpec']['runId']}/alpha"
            self.assertNotEqual(
                0,
                subprocess.run(
                    ["git", "-C", str(factory.repo), "show-ref", "--verify", "--quiet", destination_ref],
                    check=False, capture_output=True, shell=False,
                ).returncode,
            )
            state = json.loads((
                factory.repo / ".compass-builder" / "runs" / v1["runSpec"]["runId"] / "state.json"
            ).read_text(encoding="utf-8"))
            self.assertEqual("blocked", state["state"])
            self.assertEqual("verification", state["activeBlocker"]["phase"])
            self.assertEqual("alpha", state["activeBlocker"]["storyId"])
            self.assertEqual("wave-workers-complete", state["activeBlocker"]["resumeState"])

    def test_v2_root_gate_denial_retains_merge_without_advancing_verified_sha(self):
        with tempfile.TemporaryDirectory() as temporary:
            factory = GitRepoFactory(Path(temporary))
            base_sha = factory.init({
                "src/alpha/value.txt": "before\n",
                "src/beta/value.txt": "before\n",
                "docs/review.md": "approved review\n",
            })
            v1 = _inputs(factory, base_sha, "sequential")
            prepared, identity = _prepared_gate_environment(
                factory, v1["runSpec"]["runId"]
            )
            root_gate = manual_gate("root-after-merge", identity)
            ledger = {
                "schemaVersion": "compass-builder.outcome-gate-ledger.v1",
                "controller": "compass-builder", "runId": v1["runSpec"]["runId"],
                "requiredRequirementIds": ["R100"],
                "requiredAcceptanceIds": ["A100"], "gates": [root_gate],
            }
            bundle = build_gated_execution_bundle(
                v1["runSpec"], v1["wavePlan"], v1["hostCapabilities"],
                v1["planningTimestamp"], factory.repo, ledger,
            )
            provider = _ApprovingOperator(
                [], factory.repo, v1["runSpec"]["runId"], deny_scope="root"
            )
            with patch.object(
                controller_module, "prepare_git_environment", side_effect=prepared,
            ), self.assertRaisesRegex(ControllerError, "required root outcome gate"):
                execute_run(
                    factory.repo, bundle, worker_transport=commit_transport,
                    operator_gate_provider=provider, timeout_ms=30_000,
                )
            state = json.loads((
                factory.repo / ".compass-builder" / "runs" / v1["runSpec"]["runId"] / "state.json"
            ).read_text(encoding="utf-8"))
            entry = state["waves"][0]["branches"][0]
            self.assertEqual("blocked", state["state"])
            self.assertEqual("post-merge-check", state["activeBlocker"]["phase"])
            self.assertIsNotNone(entry["mergeSha"])
            self.assertEqual("blocked", entry["integrationState"])
            self.assertEqual(entry["mergeSha"], factory.sha("HEAD"))
            self.assertEqual(base_sha, state["lastVerifiedIntegrationSha"])

    def test_v2_verifies_and_gates_in_clone_before_import_then_gates_each_merge(self):
        with tempfile.TemporaryDirectory() as temporary:
            factory = GitRepoFactory(Path(temporary))
            base_sha = factory.init({
                "src/alpha/value.txt": "before\n",
                "src/beta/value.txt": "before\n",
                "docs/review.md": "approved review\n",
            })
            v1 = _inputs(factory, base_sha, "parallel")
            prepared, environment_identity = _prepared_gate_environment(
                factory, v1["runSpec"]["runId"]
            )
            gates = []
            for story_id in ("alpha", "beta"):
                gate = manual_gate(f"story-{story_id}", environment_identity)
                gate.update(gateScope="story", storyId=story_id)
                gates.append(gate)
            gates.append(manual_gate("root-after-merge", environment_identity))
            ledger = {
                "schemaVersion": "compass-builder.outcome-gate-ledger.v1",
                "controller": "compass-builder", "runId": v1["runSpec"]["runId"],
                "requiredRequirementIds": ["R100"],
                "requiredAcceptanceIds": ["A100"], "gates": gates,
            }
            bundle = build_gated_execution_bundle(
                v1["runSpec"], v1["wavePlan"], v1["hostCapabilities"],
                v1["planningTimestamp"], factory.repo, ledger,
            )
            order: list[str] = []
            provider = _ApprovingOperator(order, factory.repo, v1["runSpec"]["runId"])
            real_verify = controller_module.verify_worker
            real_import = controller_module._import_worker_branch

            def observe_verify(*args, **kwargs):
                receipt = args[3]
                order.append(f"verify:{receipt['storyId']}")
                return real_verify(*args, **kwargs)

            def observe_import(*args, **kwargs):
                receipt = args[2]
                order.append(f"import:{receipt['storyId']}")
                return real_import(*args, **kwargs)

            with patch.object(
                controller_module, "prepare_git_environment", side_effect=prepared,
            ), patch.object(
                controller_module, "verify_worker", side_effect=observe_verify
            ), patch.object(
                controller_module, "_import_worker_branch", side_effect=observe_import
            ):
                result = execute_run(
                    factory.repo, bundle, worker_transport=commit_transport,
                    operator_gate_provider=provider, timeout_ms=30_000,
                )

            self.assertEqual("completed", result.state["state"])
            for story_id in ("alpha", "beta"):
                self.assertIn(
                    f"gate:story:{story_id}", order,
                    "v2 story gate was not enforced before import",
                )
                self.assertLess(order.index(f"verify:{story_id}"), order.index(f"gate:story:{story_id}"))
                self.assertLess(order.index(f"gate:story:{story_id}"), order.index(f"import:{story_id}"))
            self.assertEqual(2, order.count("gate:root:None"))
            self.assertEqual([True, True], provider.story_ref_absent)
            self.assertEqual([True, True], provider.root_verified_sha_retained)
            run_root = factory.repo / ".compass-builder" / "runs" / result.run_id
            receipts = GateEvidenceJournal(
                run_root, factory.repo / ".compass-builder"
            ).read()
            self.assertEqual([1, 2, 3, 4], [item["sequence"] for item in receipts])
            self.assertEqual(
                ["verification", "verification", "post-merge-check", "post-merge-check"],
                [item["phase"] for item in receipts],
            )

    def test_sequential_second_wave_launch_binds_verified_first_wave_head(self):
        with tempfile.TemporaryDirectory() as temporary:
            factory = GitRepoFactory(Path(temporary))
            base_sha = factory.init({
                "src/alpha/value.txt": "before\n",
                "src/beta/value.txt": "before\n",
            })
            bundle = _inputs(factory, base_sha, "sequential")
            result = execute_run(
                factory.repo, bundle, worker_transport=commit_transport,
                timeout_ms=30_000,
            )
            first_merge = result.state["waves"][0]["branches"][0]["mergeSha"]
            second_wave = result.state["waves"][1]
            self.assertEqual(first_merge, second_wave["startExpectedSha"])
            run_root = factory.repo / ".compass-builder" / "runs" / result.run_id
            beta_launch = json.loads((
                run_root / "launch-records" / "beta.json"
            ).read_text(encoding="utf-8"))
            self.assertEqual(first_merge, beta_launch["workerStartSha"])
            self.assertEqual("complete", second_wave["branches"][0]["workerState"])
            store = StateStore(
                factory.repo, bundle["runSpec"], bundle["wavePlan"], factory.environment
            )
            self.assertEqual(2, len(cleanup_run(store, factory.environment)))

    def test_parallel_workers_are_isolated_then_integrated_in_plan_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            factory = GitRepoFactory(Path(temporary))
            base_sha = factory.init({
                "src/alpha/value.txt": "before\n",
                "src/beta/value.txt": "before\n",
            })
            bundle = _inputs(factory, base_sha, "parallel")
            lock = threading.Lock()
            active = 0
            maximum_active = 0

            def fake_transport(launch, story, _timeout_ms, _event_sink):
                nonlocal active, maximum_active
                worktree = Path(str(launch.record["worktree"]))
                story_id = str(launch.record["storyId"])
                with lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                try:
                    time.sleep(0.05)
                    path = worktree / "src" / story_id / "value.txt"
                    path.write_text("after\n", encoding="utf-8", newline="\n")
                    environment = dict(launch.environment)
                    for args in (("add", "--all"), ("commit", "-m", f"{story_id} worker")):
                        result = subprocess.run(
                            ["git", "--no-pager", "-C", str(worktree), *args],
                            check=False, capture_output=True, shell=False, env=environment,
                        )
                        if result.returncode:
                            raise AssertionError(result.stderr.decode())
                    head = subprocess.run(
                        ["git", "--no-pager", "-C", str(worktree), "rev-parse", "HEAD"],
                        check=True, capture_output=True, shell=False, env=environment,
                    ).stdout.decode().strip()
                finally:
                    with lock:
                        active -= 1
                return {
                    "schemaVersion": "compass-builder.worker-receipt.v1",
                    "runId": launch.record["runId"], "storyId": story_id,
                    "branch": launch.record["branch"], "worktree": str(worktree),
                    "exactModel": launch.record["exactModel"],
                    "effort": launch.record["effort"],
                    "baseSha": launch.record["workerStartSha"], "headSha": head,
                    "commitSha": head,
                    "changedFiles": [{
                        "path": f"src/{story_id}/value.txt", "sourcePath": None,
                        "changeType": "modified",
                    }],
                    "checks": [{
                        "name": "focused", "command": CHECK, "status": "passed",
                        "evidenceDigest": "sha256:" + "e" * 64,
                    }],
                    "elapsedMs": 1, "status": "succeeded", "blocker": None,
                }

            result = execute_run(
                factory.repo, bundle, worker_transport=fake_transport, timeout_ms=30_000,
            )
            self.assertEqual("completed", result.state["state"])
            self.assertEqual(2, maximum_active)
            self.assertEqual("after\n", (factory.repo / "src/alpha/value.txt").read_text())
            self.assertEqual("after\n", (factory.repo / "src/beta/value.txt").read_text())
            run_root = factory.repo / ".compass-builder" / "runs" / result.run_id
            for story_id in ("alpha", "beta"):
                launch = json.loads((
                    run_root / "launch-records" / f"{story_id}.json"
                ).read_text(encoding="utf-8"))
                checkout = Path(launch["worktree"])
                common = factory.git(
                    "rev-parse", "--path-format=absolute", "--git-common-dir",
                    cwd=checkout,
                ).stdout.decode().strip()
                self.assertEqual((checkout / ".git").resolve(), Path(common).resolve())
                self.assertNotEqual(factory.repo.joinpath(".git").resolve(), Path(common).resolve())
                self.assertEqual(
                    "", factory.git("remote", cwd=checkout).stdout.decode().strip()
                )
            parents = factory.git(
                "rev-list", "--parents", "-n", "1", result.final_green_sha
            ).stdout.decode().split()
            self.assertEqual(3, len(parents))
            store = StateStore(
                factory.repo, bundle["runSpec"], bundle["wavePlan"], factory.environment
            )
            removed = cleanup_run(store, factory.environment)
            self.assertEqual(2, len(removed))
            self.assertTrue(all(not path.exists() for path in removed))


if __name__ == "__main__":
    unittest.main()
