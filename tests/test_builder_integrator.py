from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "plugins" / "compass-builder"
if str(BUILDER) not in sys.path:
    sys.path.insert(0, str(BUILDER))

from compass_builder.integrator import IntegrationError, integrate_verified_branch
from compass_builder.git_environment import prepare_git_environment
from compass_builder.lease import acquire_lease, release_lease
from compass_builder.models import canonical_json
from compass_builder.process_runner import (
    BoundedProcessError, parse_command, run_bounded,
)
from compass_builder.state import StateStore
from compass_builder.verifier import verify_worker
from tests.test_builder_verifier import literal_commit, make_context


def complete_store(
    context: dict[str, object], environment=None
) -> tuple[StateStore, dict[str, object]]:
    environment = environment or context["factory"].environment
    store = StateStore(
        context["factory"].repo, context["spec"], context["plan"],
        environment,
    )
    planned = store.initial_state()
    store.create(planned)
    launch_directory = store.run_root / "launch-records"
    launch_directory.mkdir()
    records = {"alpha": context["launch"], **context.get("launchRecords", {})}
    for story_id, record in records.items():
        (launch_directory / f"{story_id}.json").write_bytes(canonical_json(record))
    dispatching = copy.deepcopy(planned)
    dispatching.update(previousState="planned", state="dispatching")
    for entry in dispatching["waves"][0]["branches"]:
        entry["workerState"] = "running"
    store.write_transition(planned, dispatching)
    complete = copy.deepcopy(dispatching)
    complete.update(previousState="dispatching", state="wave-workers-complete")
    for entry in complete["waves"][0]["branches"]:
        entry["workerState"] = "complete"
    store.write_transition(dispatching, complete)
    return store, complete


def verified_merging(store: StateStore, complete: dict[str, object]) -> dict[str, object]:
    merging = copy.deepcopy(complete)
    merging.update(previousState="wave-workers-complete", state="wave-merging")
    for entry in merging["waves"][0]["branches"]:
        entry.update(verificationState="verified", integrationState="worker-verified")
    store.write_transition(complete, merging)
    return merging


class BuilderIntegratorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.context = make_context(Path(self.temporary.name), durable=False)
        self.store, complete = complete_store(self.context)
        self.verified_for_forgery = verify_worker(
            self.context["factory"].repo, self.context["spec"], self.context["plan"],
            self.context["receipt"], self.context["launch"],
            self.context["factory"].environment,
        )
        self.state = verified_merging(self.store, complete)

    def tearDown(self):
        self.temporary.cleanup()

    def test_no_ff_merge_atomically_advances_ledger_then_marks_wave_verified(self):
        result = integrate_verified_branch(
            self.store, self.state, self.context["receipt"], self.context["factory"].environment,
            acquired_at="2026-09-01T12:01:00Z", expires_at="2026-09-01T12:06:00Z",
        )
        self.assertEqual("wave-verified", result.state["state"])
        entry = result.state["waves"][0]["branches"][0]
        self.assertEqual("integration-verified", entry["integrationState"])
        self.assertEqual(result.merge_sha, entry["mergeSha"])
        parents = self.context["factory"].git("rev-list", "--parents", "-n", "1", result.merge_sha).stdout.decode().split()
        self.assertEqual(3, len(parents))

    def test_caller_constructed_verification_authority_cannot_merge_out_of_scope_commit(self):
        factory, worker = self.context["factory"], self.context["worker"]
        factory.git("reset", "--hard", self.context["base"], cwd=worker)
        malicious = factory.commit({"outside.txt": "escape\n"}, "escape scope", cwd=worker)
        forged = replace(self.verified_for_forgery, head_sha=malicious)
        with self.assertRaises(IntegrationError):
            integrate_verified_branch(
                self.store, self.state, forged, factory.environment,
                acquired_at="2026-09-01T12:01:00Z",
                expires_at="2026-09-01T12:06:00Z",
            )
        self.assertEqual(self.context["base"], factory.sha("HEAD"))

    def test_branch_advance_after_fresh_verification_fails_closed(self):
        factory, worker = self.context["factory"], self.context["worker"]
        real_verify = verify_worker

        def verify_then_advance(*args, **kwargs):
            result = real_verify(*args, **kwargs)
            factory.commit({"src/alpha/race.txt": "race\n"}, "advance after verify", cwd=worker)
            return result

        with patch(
            "compass_builder.integrator.verify_worker", side_effect=verify_then_advance
        ), self.assertRaisesRegex(IntegrationError, "advanced|changed"):
            integrate_verified_branch(
                self.store, self.state, self.context["receipt"], factory.environment,
                acquired_at="2026-09-01T12:01:00Z",
                expires_at="2026-09-01T12:06:00Z",
            )
        self.assertEqual(self.context["base"], factory.sha("HEAD"))
        self.assertEqual("blocked", self.store.load()["state"])

    def test_unproven_post_merge_head_is_never_recorded_as_success(self):
        factory = self.context["factory"]
        import compass_builder.integrator as integrator_module

        original_git = integrator_module._git

        def inject_after_merge(repo, environment, arguments, *, check=True):
            result = original_git(repo, environment, arguments, check=check)
            if arguments and arguments[0] == "merge" and result.returncode == 0:
                factory.git("commit", "--allow-empty", "-m", "concurrent one-parent commit")
            return result

        with patch(
            "compass_builder.integrator._git", side_effect=inject_after_merge
        ), self.assertRaisesRegex(IntegrationError, "proof|parents|identity"):
            integrate_verified_branch(
                self.store, self.state, self.context["receipt"], factory.environment,
                acquired_at="2026-09-01T12:01:00Z",
                expires_at="2026-09-01T12:06:00Z",
            )
        durable = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertNotEqual("wave-verified", durable["state"])
        self.assertTrue(self.store.failure_records())
        self.assertTrue(any(
            item.get("observedHead") == factory.sha("HEAD")
            for item in self.store.failure_records()
        ))

    def test_exact_merge_is_adopted_when_normal_state_publication_fails(self):
        original = self.store.record_integration_merge
        attempts = 0

        def fail_once(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("injected publication failure")
            return original(*args, **kwargs)

        with patch.object(
            self.store, "record_integration_merge", side_effect=fail_once
        ), self.assertRaisesRegex(IntegrationError, "publication failure"):
            integrate_verified_branch(
                self.store, self.state, self.context["receipt"],
                self.context["factory"].environment,
                acquired_at="2026-09-01T12:01:00Z",
                expires_at="2026-09-01T12:06:00Z",
            )
        blocked = self.store.load()
        self.assertEqual("blocked", blocked["state"])
        self.assertNotEqual(self.context["base"], blocked["expectedIntegrationSha"])
        self.assertEqual(
            "wave-integrated-unverified", blocked["activeBlocker"]["resumeState"]
        )
        resumed = self.store.resume_state(blocked)
        self.assertEqual("wave-integrated-unverified", resumed["state"])
        self.assertEqual(blocked["expectedIntegrationSha"], resumed["expectedIntegrationSha"])

    def test_new_controller_adopts_exact_orphaned_merge_intent(self):
        handle = acquire_lease(
            self.store.repository.common_git_dir, "main", owner_id="dead-controller",
            evidence_digest="sha256:" + "d" * 64,
            acquired_at="2026-09-01T12:01:00Z", expires_at="2026-09-01T12:06:00Z",
        )
        self.store.record_merge_intent(
            self.state, story_id="alpha", expected_sha=self.context["base"],
            verified_head_sha=self.context["head"],
        )
        self.context["factory"].git(
            "merge", "--no-ff", "--no-edit", "--no-gpg-sign", self.context["head"]
        )
        orphaned_merge = self.context["factory"].sha("HEAD")
        release_lease(handle)
        restarted = StateStore(
            self.context["factory"].repo, self.context["spec"], self.context["plan"],
            self.context["factory"].environment,
        )
        with self.assertRaisesRegex(IntegrationError, "recovered exact prior merge"):
            integrate_verified_branch(
                restarted, self.state, self.context["receipt"],
                self.context["factory"].environment,
                acquired_at="2026-09-01T12:07:00Z", expires_at="2026-09-01T12:12:00Z",
            )
        blocked = restarted.load()
        self.assertEqual(orphaned_merge, blocked["expectedIntegrationSha"])
        self.assertEqual("wave-integrated-unverified", blocked["activeBlocker"]["resumeState"])
        resumed = restarted.resume_state(blocked)
        restarted.write_transition(blocked, resumed)
        head_before = self.context["factory"].sha("HEAD")
        result = integrate_verified_branch(
            restarted, resumed, self.context["receipt"],
            self.context["factory"].environment,
            acquired_at="2026-09-01T12:13:00Z", expires_at="2026-09-01T12:18:00Z",
        )
        self.assertEqual("wave-verified", result.state["state"])
        self.assertEqual(head_before, self.context["factory"].sha("HEAD"))
        self.assertEqual(orphaned_merge, result.merge_sha)

    def test_lease_release_failure_records_blocker_instead_of_success(self):
        with patch(
            "compass_builder.integrator.release_lease",
            side_effect=Exception("release injection"),
        ), self.assertRaisesRegex(IntegrationError, "release"):
            integrate_verified_branch(
                self.store, self.state, self.context["receipt"],
                self.context["factory"].environment,
                acquired_at="2026-09-01T12:01:00Z",
                expires_at="2026-09-01T12:06:00Z",
            )
        self.assertEqual("blocked", self.store.load()["state"])

    def test_bounded_runner_terminates_hung_and_flooding_processes(self):
        started = time.monotonic()
        with self.assertRaisesRegex(BoundedProcessError, "timed out") as hung:
            run_bounded(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                timeout=0.1, max_output_bytes=32,
            )
        self.assertLess(time.monotonic() - started, 5)
        self.assertLessEqual(len(hung.exception.stdout), 32)
        self.assertLessEqual(len(hung.exception.stderr), 32)

        with self.assertRaisesRegex(BoundedProcessError, "exceeded") as flooded:
            run_bounded(
                [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1000000)"],
                timeout=5, max_output_bytes=64,
            )
        self.assertLessEqual(len(flooded.exception.stdout), 64)
        self.assertLessEqual(len(flooded.exception.stderr), 64)

    def test_bounded_runner_terminates_descendant_tree_before_delayed_marker(self):
        marker = Path(self.temporary.name) / "descendant-marker.txt"
        child = (
            "import pathlib,time; time.sleep(1.5); "
            f"pathlib.Path({str(marker)!r}).write_text('escaped')"
        )
        parent = (
            "import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(30)"
        )
        started = time.monotonic()
        with self.assertRaisesRegex(BoundedProcessError, "timed out"):
            run_bounded([sys.executable, "-c", parent], timeout=0.2, max_output_bytes=64)
        self.assertLess(time.monotonic() - started, 2)
        time.sleep(1.6)
        self.assertFalse(marker.exists())

        overflow_marker = Path(self.temporary.name) / "overflow-descendant-marker.txt"
        overflow_child = (
            "import pathlib,time; time.sleep(1.5); "
            f"pathlib.Path({str(overflow_marker)!r}).write_text('escaped')"
        )
        overflowing_parent = (
            "import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable,'-c',{overflow_child!r}]); "
            "sys.stdout.write('x'*1000000); sys.stdout.flush(); time.sleep(30)"
        )
        started = time.monotonic()
        with self.assertRaisesRegex(BoundedProcessError, "exceeded"):
            run_bounded(
                [sys.executable, "-c", overflowing_parent], timeout=2,
                max_output_bytes=64,
            )
        self.assertLess(time.monotonic() - started, 2)
        time.sleep(1.6)
        self.assertFalse(overflow_marker.exists())

    def test_bounded_runner_propagates_pipe_reader_errors(self):
        import compass_builder.process_runner as runner_module
        real_popen = subprocess.Popen

        class BrokenPipe:
            def __init__(self, wrapped):
                self.wrapped = wrapped

            def read(self, _size):
                raise OSError("reader injection")

            def close(self):
                self.wrapped.close()

        def broken_stdout(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            process.stdout = BrokenPipe(process.stdout)
            return process

        with patch.object(runner_module.subprocess, "Popen", side_effect=broken_stdout), self.assertRaisesRegex(
            BoundedProcessError, "reader failed.*reader injection"
        ):
            run_bounded([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)

    @unittest.skipUnless(os.name == "nt", "Windows command-line parsing contract")
    def test_windows_validation_command_parser_preserves_backslashes_and_spaces(self):
        self.assertEqual(
            ["python", r"C:\Temp\check.py"],
            parse_command(r"python C:\Temp\check.py", platform="windows"),
        )
        self.assertEqual(
            ["python", r"C:\Temp Folder\check.py", "--flag"],
            parse_command(r'python "C:\Temp Folder\check.py" --flag', platform="windows"),
        )

    def test_bounded_git_and_validation_failures_release_lease_and_block(self):
        with patch(
            "compass_builder.integrator.run_bounded",
            side_effect=BoundedProcessError("process timed out after 0.1 seconds"),
        ), self.assertRaisesRegex(IntegrationError, "timed out"):
            integrate_verified_branch(
                self.store, self.state, self.context["receipt"],
                self.context["factory"].environment,
                acquired_at="2026-09-01T12:01:00Z",
                expires_at="2026-09-01T12:06:00Z",
            )
        self.assertEqual("blocked", self.store.load()["state"])
        lease_root = self.store.repository.common_git_dir / "compass-builder-leases"
        self.assertEqual([], list(lease_root.iterdir()))

        other = make_context(Path(self.temporary.name) / "flood-integration", durable=False)
        store, complete = complete_store(other)
        merging = verified_merging(store, complete)

        def flood(_argv, _cwd, _environment):
            return subprocess.CompletedProcess([], 0, "x" * 1_100_000, "")

        with self.assertRaisesRegex(IntegrationError, "checks failed"):
            integrate_verified_branch(
                store, merging, other["receipt"], other["factory"].environment,
                acquired_at="2026-09-01T12:01:00Z",
                expires_at="2026-09-01T12:06:00Z", command_runner=flood,
            )
        self.assertEqual("blocked", store.load()["state"])
        lease_root = store.repository.common_git_dir / "compass-builder-leases"
        self.assertEqual([], list(lease_root.iterdir()))

    def test_literal_commit_missing_identities_cannot_be_integrated(self):
        factory, worker = self.context["factory"], self.context["worker"]
        tree = factory.sha(f"{self.context['base']}^{{tree}}")
        malformed = literal_commit(
            factory, tree, self.context["base"],
            include_author=False, include_committer=False,
        )
        factory.git("reset", "--hard", malformed, cwd=worker)
        forged = copy.deepcopy(self.context["receipt"])
        forged.update(headSha=malformed, commitSha=malformed)
        with self.assertRaisesRegex(IntegrationError, "author and committer"):
            integrate_verified_branch(
                self.store, self.state, forged, factory.environment,
                acquired_at="2026-09-01T12:01:00Z",
                expires_at="2026-09-01T12:06:00Z",
            )
        self.assertEqual(self.context["base"], factory.sha("HEAD"))
        self.assertEqual("blocked", self.store.load()["state"])

    def test_committer_before_author_is_rejected_under_released_integration_lease(self):
        factory, worker = self.context["factory"], self.context["worker"]
        tree = factory.sha(f"{self.context['base']}^{{tree}}")
        malformed = literal_commit(
            factory, tree, self.context["base"], committer_before_author=True
        )
        factory.git("reset", "--hard", malformed, cwd=worker)
        forged = copy.deepcopy(self.context["receipt"])
        forged.update(headSha=malformed, commitSha=malformed)
        with self.assertRaisesRegex(IntegrationError, "canonical author and committer"):
            integrate_verified_branch(
                self.store, self.state, forged, factory.environment,
                acquired_at="2026-09-01T12:01:00Z",
                expires_at="2026-09-01T12:06:00Z",
            )
        self.assertEqual(self.context["base"], factory.sha("HEAD"))
        self.assertEqual("blocked", self.store.load()["state"])
        lease_root = self.store.repository.common_git_dir / "compass-builder-leases"
        self.assertEqual([], list(lease_root.iterdir()))

    def test_validation_created_mutation_blocks_and_retains_worker_and_merge_evidence(self):
        def mutate(argv, cwd, environment):
            (cwd / "validation-evidence.txt").write_text("created\n", encoding="utf-8")
            return subprocess.CompletedProcess(list(argv), 0, "ok", "")

        with self.assertRaisesRegex(IntegrationError, "mutated"):
            integrate_verified_branch(
                self.store, self.state, self.context["receipt"], self.context["factory"].environment,
                acquired_at="2026-09-01T12:01:00Z", expires_at="2026-09-01T12:06:00Z",
                command_runner=mutate,
            )
        blocked = self.store.load()
        self.assertEqual("blocked", blocked["state"])
        self.assertEqual("post-merge-check", blocked["activeBlocker"]["phase"])
        self.assertTrue(Path(self.context["worker"]).is_dir())
        self.assertTrue((self.context["factory"].repo / "validation-evidence.txt").is_file())

    def test_existing_lease_stops_before_merge_without_deleting_worktree(self):
        before = copy.deepcopy(self.store.load())
        handle = acquire_lease(
            self.store.repository.common_git_dir, "main", owner_id="other-controller",
            evidence_digest="sha256:" + "a" * 64,
            acquired_at="2026-09-01T12:01:00Z", expires_at="2026-09-01T12:06:00Z",
        )
        try:
            with self.assertRaisesRegex(IntegrationError, "lease"):
                integrate_verified_branch(
                    self.store, self.state, self.context["receipt"], self.context["factory"].environment,
                    acquired_at="2026-09-01T12:02:00Z", expires_at="2026-09-01T12:07:00Z",
                )
        finally:
            release_lease(handle)
        self.assertEqual(self.context["base"], self.context["factory"].sha("HEAD"))
        self.assertTrue(Path(self.context["worker"]).is_dir())
        self.assertEqual(before, self.store.load())
        self.assertTrue(self.store.failure_records())

    def test_same_sha_scratch_checkout_is_rejected_before_mutation(self):
        factory = self.context["factory"]
        factory.git("branch", "scratch", self.context["base"])
        factory.git("switch", "scratch")
        with self.assertRaisesRegex(IntegrationError, "integration branch"):
            integrate_verified_branch(
                self.store, self.state, self.context["receipt"], factory.environment,
                acquired_at="2026-09-01T12:01:00Z", expires_at="2026-09-01T12:06:00Z",
            )
        self.assertEqual(self.context["base"], factory.sha("refs/heads/main"))
        self.assertEqual("blocked", self.store.load()["state"])

    def test_integrator_state_and_git_checks_ignore_hostile_ambient_variables(self):
        context = make_context(Path(self.temporary.name) / "hostile-integrator", durable=False)
        hostile = dict(os.environ)
        hostile.update({
            "GIT_DIR": str(Path(self.temporary.name) / "wrong.git"),
            "GIT_WORK_TREE": str(Path(self.temporary.name) / "wrong-tree"),
            "GIT_INDEX_FILE": str(Path(self.temporary.name) / "wrong-index"),
            "GIT_OBJECT_DIRECTORY": str(Path(self.temporary.name) / "wrong-objects"),
            "GIT_CONFIG_GLOBAL": str(Path(self.temporary.name) / "wrong-config"),
            "GIT_CONFIG_COUNT": "50",
        })
        environment = prepare_git_environment(
            Path(self.temporary.name) / "hostile-integrator-env", base_environment=hostile
        )
        context["launch"]["gitEnvironmentDigest"] = environment.digest
        with patch.dict(os.environ, hostile, clear=True):
            store, complete = complete_store(context, environment)
            verified = verify_worker(
                context["factory"].repo, context["spec"], context["plan"],
                context["receipt"], context["launch"], environment,
            )
            state = verified_merging(store, complete)
            result = integrate_verified_branch(
                store, state, context["receipt"], environment,
                acquired_at="2026-09-01T12:01:00Z", expires_at="2026-09-01T12:06:00Z",
            )
        self.assertEqual("wave-verified", result.state["state"])

    def test_check_startup_and_parse_errors_persist_post_merge_blockers(self):
        def missing(argv, cwd, environment):
            raise FileNotFoundError("missing-controller-check")

        with self.assertRaisesRegex(IntegrationError, "checks failed"):
            integrate_verified_branch(
                self.store, self.state, self.context["receipt"], self.context["factory"].environment,
                acquired_at="2026-09-01T12:01:00Z", expires_at="2026-09-01T12:06:00Z",
                command_runner=missing,
            )
        blocked = self.store.load()
        self.assertEqual("post-merge-check", blocked["activeBlocker"]["phase"])
        self.assertEqual("wave-integrated-unverified", blocked["activeBlocker"]["resumeState"])

        context = make_context(Path(self.temporary.name) / "malformed", durable=False)
        context["spec"]["validationCommands"] = ['python -c "unterminated']
        context["plan"]["normalizedInputDigest"] = "sha256:" + hashlib.sha256(
            canonical_json(context["spec"], "run-spec")
        ).hexdigest()
        store, complete = complete_store(context)
        verified = verify_worker(
            context["factory"].repo, context["spec"], context["plan"],
            context["receipt"], context["launch"], context["factory"].environment,
        )
        state = verified_merging(store, complete)
        with self.assertRaisesRegex(IntegrationError, "checks failed"):
            integrate_verified_branch(
                store, state, context["receipt"], context["factory"].environment,
                acquired_at="2026-09-01T12:01:00Z", expires_at="2026-09-01T12:06:00Z",
            )
        self.assertEqual("post-merge-check", store.load()["activeBlocker"]["phase"])

    def test_merge_conflict_stops_without_ledger_advance_or_cleanup(self):
        import compass_builder.integrator as integrator_module
        original_git = integrator_module._git

        def fail_merge(repo, environment, arguments, *, check=True):
            if arguments and arguments[0] == "merge":
                return subprocess.CompletedProcess(arguments, 1, b"", b"conflict")
            return original_git(repo, environment, arguments, check=check)

        with patch(
            "compass_builder.integrator._git", side_effect=fail_merge
        ), self.assertRaisesRegex(IntegrationError, "conflict"):
            integrate_verified_branch(
                self.store, self.state, self.context["receipt"],
                self.context["factory"].environment,
                acquired_at="2026-09-01T12:01:00Z", expires_at="2026-09-01T12:06:00Z",
            )
        self.assertEqual("blocked", self.store.load()["state"])
        self.assertTrue(Path(self.context["worker"]).is_dir())


if __name__ == "__main__":
    unittest.main()
