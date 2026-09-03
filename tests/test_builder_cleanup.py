from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "plugins" / "compass-builder"
if str(BUILDER) not in sys.path:
    sys.path.insert(0, str(BUILDER))

from compass_builder.cleanup import CleanupError, cleanup_run  # noqa: E402
from compass_builder.cli import main  # noqa: E402
from compass_builder.git_environment import prepare_git_environment  # noqa: E402
from compass_builder.git_objects import read_raw_commit  # noqa: E402
from compass_builder.integrator import integrate_verified_branch  # noqa: E402
from compass_builder.launcher import REASONING_CONFIG_KEY, prepare_launch  # noqa: E402
from compass_builder.models import canonical_json  # noqa: E402
from compass_builder.state import StateStore, build_execution_bundle  # noqa: E402
from compass_builder.verifier import verify_worker  # noqa: E402
from tests.test_builder_integrator import complete_store, verified_merging  # noqa: E402
from tests.test_builder_verifier import CHECK, make_context  # noqa: E402


def integrated_context(base: Path) -> dict[str, object]:
    context = make_context(base, durable=False)
    store, complete = complete_store(context)
    merging = verified_merging(store, complete)
    result = integrate_verified_branch(
        store, merging, context["receipt"], context["factory"].environment,
        acquired_at="2026-09-01T12:01:00Z", expires_at="2026-09-01T12:06:00Z",
    )
    context.update(store=store, integrated=result.state)
    return context


def two_story_integrated_context(base: Path, *, base_depth: int = 0) -> dict[str, object]:
    context = make_context(base, durable=False, base_depth=base_depth)
    factory = context["factory"]
    beta_branch = f"cb/{context['spec']['runId']}/beta"
    beta_story = copy.deepcopy(context["spec"]["stories"][0])
    beta_story.update(id="beta", title="Beta", description="Implement beta safely.", writeScopes=["src/beta"])
    context["spec"]["stories"].append(beta_story)
    context["plan"].update(mode="parallel", concurrency=2)
    context["plan"]["stories"].append({
        "storyId": "beta", "branch": beta_branch, "recommendedEffort": "low",
        "handoffDigest": "sha256:" + "f" * 64,
    })
    context["plan"]["waves"][0]["storyIds"].append("beta")
    context["plan"]["normalizedInputDigest"] = "sha256:" + hashlib.sha256(
        canonical_json(context["spec"], "run-spec")
    ).hexdigest()
    beta_path = StateStore(
        factory.repo, context["spec"], context["plan"], factory.environment
    ).registered_worktree("beta")
    beta = factory.worktree(beta_branch, beta_path, context["base"])
    beta_head = factory.commit({"src/beta/value.txt": "beta\n"}, "beta worker", cwd=beta)
    beta_launch = prepare_launch(
        context["spec"], context["plan"], context["host"],
        planning_timestamp="2026-09-01T12:01:00Z", story_id="beta", worktree=beta,
        worker_schema=BUILDER / "schemas" / "worker-output.schema.json",
        reasoning_config_key=REASONING_CONFIG_KEY,
        reasoning_config_evidence_digest=context["host"]["reasoningConfig"]["evidenceDigest"],
        git_environment=factory.environment,
        worker_start_sha=context["base"],
    )
    beta_receipt = copy.deepcopy(context["receipt"])
    beta_receipt.update(storyId="beta", branch=beta_branch, worktree=str(beta), headSha=beta_head, commitSha=beta_head)
    beta_receipt["changedFiles"] = [{"path": "src/beta/value.txt", "sourcePath": None, "changeType": "added"}]
    context["launchRecords"] = {"beta": beta_launch.record}

    store, complete = complete_store(context)
    merging = verified_merging(store, complete)
    first = integrate_verified_branch(
        store, merging, context["receipt"], factory.environment,
        acquired_at="2026-09-01T12:01:00Z", expires_at="2026-09-01T12:06:00Z",
    )
    second = integrate_verified_branch(
        store, first.state, beta_receipt, factory.environment,
        acquired_at="2026-09-01T12:07:00Z", expires_at="2026-09-01T12:12:00Z",
    )
    context.update(store=store, integrated=second.state, beta=beta, betaHead=beta_head)
    return context


class BuilderCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.context = integrated_context(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def test_cleanup_is_derived_only_from_verified_merge_ledger(self):
        removed = cleanup_run(self.context["store"], self.context["factory"].environment)
        self.assertEqual((Path(self.context["worker"]),), removed)
        self.assertFalse(Path(self.context["worker"]).exists())

    def test_dirty_branch_drift_and_wrong_run_id_retain_evidence(self):
        worker = Path(self.context["worker"])
        (worker / "evidence.txt").write_text("retain\n", encoding="utf-8")
        with self.assertRaisesRegex(CleanupError, "dirty"):
            cleanup_run(self.context["store"], self.context["factory"].environment)
        self.assertTrue(worker.is_dir())
        (worker / "evidence.txt").unlink()
        self.context["factory"].git(
            "update-ref", f"refs/heads/{self.context['receipt']['branch']}", self.context["base"]
        )
        with self.assertRaisesRegex(CleanupError, "branch/HEAD|branch ref"):
            cleanup_run(self.context["store"], self.context["factory"].environment)
        self.assertTrue(worker.is_dir())

        error = StringIO()
        with redirect_stderr(error):
            status = main([
                "cleanup", "--repo", str(self.context["factory"].repo),
                "--run-id", "cb-wrong-0123456789abcdef",
            ])
        self.assertEqual(4, status)
        self.assertTrue(worker.is_dir())

    def test_preflight_revalidates_all_targets_before_any_deletion(self):
        context = two_story_integrated_context(Path(self.temporary.name) / "two")
        (Path(context["beta"]) / "unsafe.txt").write_text("retain\n", encoding="utf-8")
        with self.assertRaisesRegex(CleanupError, "dirty"):
            cleanup_run(context["store"], context["factory"].environment)
        self.assertTrue(Path(context["worker"]).is_dir())
        self.assertTrue(Path(context["beta"]).is_dir())

    def test_cleanup_race_records_first_removal_and_rerun_reconciles(self):
        context = two_story_integrated_context(Path(self.temporary.name) / "race")
        import compass_builder.cleanup as cleanup_module

        original_git = cleanup_module._git
        dirtied = False

        def dirty_second_after_first(repo, environment, arguments, *, check=True):
            nonlocal dirtied
            result = original_git(repo, environment, arguments, check=check)
            if arguments[:2] == ["worktree", "remove"] and not dirtied:
                dirtied = True
                (Path(context["beta"]) / "race.txt").write_text("retain\n", encoding="utf-8")
            return result

        with patch(
            "compass_builder.cleanup._git", side_effect=dirty_second_after_first
        ), self.assertRaisesRegex(CleanupError, "dirty"):
            cleanup_run(context["store"], context["factory"].environment)
        self.assertFalse(Path(context["worker"]).exists())
        self.assertTrue(Path(context["beta"]).exists())
        self.assertEqual("wave-verified", context["store"].load()["state"])
        alpha_progress = [
            item for item in context["store"].cleanup_progress()
            if item["storyId"] == "alpha"
        ]
        self.assertIn("removed", {item["status"] for item in alpha_progress})

        (Path(context["beta"]) / "race.txt").unlink()
        removed = cleanup_run(context["store"], context["factory"].environment)
        self.assertEqual({Path(context["worker"]), Path(context["beta"])}, set(removed))
        self.assertFalse(Path(context["beta"]).exists())
        self.assertEqual("wave-verified", context["store"].load()["state"])

    def test_cleanup_cli_identical_rerun_loads_auxiliary_receipts(self):
        store = self.context["store"]
        bundle = build_execution_bundle(
            self.context["spec"], self.context["plan"], self.context["host"],
            "2026-09-01T12:01:00Z", self.context["factory"].repo,
        )
        store.bundle_path.write_bytes(canonical_json(bundle))
        cleanup_run(store, self.context["factory"].environment)
        prepare_git_environment(store.run_root / "git-environment")
        output = StringIO()
        with redirect_stdout(output):
            status = main([
                "cleanup", "--repo", str(self.context["factory"].repo),
                "--run-id", self.context["spec"]["runId"],
            ])
        self.assertEqual(0, status)
        self.assertEqual(
            [str(self.context["worker"])],
            json.loads(output.getvalue())["removedWorktrees"],
        )

    def test_cleanup_inspects_only_ledger_sized_chain_over_deep_base(self):
        context = two_story_integrated_context(
            Path(self.temporary.name) / "deep-ledger", base_depth=40
        )
        with patch(
            "compass_builder.cleanup.read_raw_commit", wraps=read_raw_commit
        ) as inspected:
            removed = cleanup_run(context["store"], context["factory"].environment)
        self.assertEqual(2, inspected.call_count)
        self.assertEqual({Path(context["worker"]), Path(context["beta"])}, set(removed))

    def test_cleanup_rejects_extra_first_parent_chain_edge(self):
        context = two_story_integrated_context(Path(self.temporary.name) / "extra-edge")
        context["factory"].git("commit", "--allow-empty", "-m", "unrecorded integration")
        extra = context["factory"].sha("HEAD")
        forged = copy.deepcopy(context["integrated"])
        forged["expectedIntegrationSha"] = extra
        with patch.object(context["store"], "load", return_value=forged), self.assertRaisesRegex(
            CleanupError, "extra or missing merge-chain edge"
        ):
            cleanup_run(context["store"], context["factory"].environment)
        self.assertTrue(Path(context["worker"]).is_dir())
        self.assertTrue(Path(context["beta"]).is_dir())

    def test_graft_cannot_rebind_old_merge_to_advanced_unmerged_worker_head(self):
        worker = Path(self.context["worker"])
        new_head = self.context["factory"].commit(
            {"src/alpha/advanced.txt": "not integrated\n"}, "advanced worker", cwd=worker
        )
        entry = self.context["integrated"]["waves"][0]["branches"][0]
        grafts = self.context["factory"].repo / ".git" / "info" / "grafts"
        original_load = self.context["store"].load

        def load_then_install_graft():
            state = original_load()
            grafts.parent.mkdir(parents=True, exist_ok=True)
            grafts.write_text(
                f"{entry['mergeSha']} {entry['preMergeExpectedSha']} {new_head}\n",
                encoding="ascii",
            )
            return state

        with patch.object(
            self.context["store"], "load", side_effect=load_then_install_graft
        ), self.assertRaisesRegex(CleanupError, "active Git graft|branch/HEAD|branch ref"):
            cleanup_run(self.context["store"], self.context["factory"].environment)
        self.assertTrue(worker.is_dir())

    def test_cleanup_cli_has_no_caller_registry_or_environment_override(self):
        store = self.context["store"]
        bundle = build_execution_bundle(
            self.context["spec"], self.context["plan"], self.context["host"],
            "2026-09-01T12:01:00Z", self.context["factory"].repo,
        )
        store.bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        environment = prepare_git_environment(store.run_root / "git-environment")
        output = StringIO()
        hostile = dict(os.environ)
        hostile.update({
            "GIT_DIR": str(Path(self.temporary.name) / "wrong.git"),
            "GIT_WORK_TREE": str(Path(self.temporary.name) / "wrong-tree"),
            "GIT_INDEX_FILE": str(Path(self.temporary.name) / "wrong-index"),
            "GIT_OBJECT_DIRECTORY": str(Path(self.temporary.name) / "wrong-objects"),
            "GIT_CONFIG_GLOBAL": str(Path(self.temporary.name) / "wrong-config"),
            "GIT_CONFIG_COUNT": "77",
        })
        with patch.dict(os.environ, hostile, clear=True), redirect_stdout(output):
            status = main([
                "cleanup", "--repo", str(self.context["factory"].repo),
                "--run-id", self.context["spec"]["runId"],
            ])
        self.assertEqual(0, status)
        self.assertEqual([str(self.context["worker"])], json.loads(output.getvalue())["removedWorktrees"])
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            main([
                "cleanup", "--repo", str(self.context["factory"].repo),
                "--run-id", self.context["spec"]["runId"], "--registry", "attacker.json",
            ])
        self.assertEqual("1", environment.environment["GIT_NO_REPLACE_OBJECTS"])

    def test_second_wave_verification_binds_first_wave_merge_as_worker_start(self):
        context = make_context(Path(self.temporary.name) / "two-wave", durable=False)
        factory = context["factory"]
        beta_story = copy.deepcopy(context["spec"]["stories"][0])
        beta_story.update(
            id="beta", title="Beta", description="Implement beta safely.",
            dependsOn=["alpha"], writeScopes=["src/beta"],
        )
        context["spec"]["stories"].append(beta_story)
        beta_branch = f"cb/{context['spec']['runId']}/beta"
        context["plan"]["stories"].append({
            "storyId": "beta", "branch": beta_branch, "recommendedEffort": "low",
            "handoffDigest": "sha256:" + "f" * 64,
        })
        context["plan"]["waves"] = [
            {"waveIndex": 0, "storyIds": ["alpha"]},
            {"waveIndex": 1, "storyIds": ["beta"]},
        ]
        context["plan"].update(mode="sequential", concurrency=1)
        context["plan"]["normalizedInputDigest"] = "sha256:" + hashlib.sha256(
            canonical_json(context["spec"], "run-spec")
        ).hexdigest()
        context["launch"] = prepare_launch(
            context["spec"], context["plan"], context["host"],
            planning_timestamp="2026-09-01T12:01:00Z", story_id="alpha",
            worktree=context["worker"],
            worker_schema=BUILDER / "schemas" / "worker-output.schema.json",
            reasoning_config_key=REASONING_CONFIG_KEY,
            reasoning_config_evidence_digest=context["host"]["reasoningConfig"]["evidenceDigest"],
            git_environment=factory.environment, worker_start_sha=context["base"],
        ).record
        store, complete = complete_store(context)
        first = integrate_verified_branch(
            store, verified_merging(store, complete), context["receipt"], factory.environment,
            acquired_at="2026-09-01T12:01:00Z", expires_at="2026-09-01T12:06:00Z",
        )
        dispatching = store.next_wave_state(first.state)
        store.write_transition(first.state, dispatching)
        beta = factory.worktree(
            beta_branch, store.registered_worktree("beta"), first.merge_sha,
        )
        beta_head = factory.commit({"src/beta/value.txt": "beta\n"}, "beta worker", cwd=beta)
        beta_launch = prepare_launch(
            context["spec"], context["plan"], context["host"],
            planning_timestamp="2026-09-01T12:01:00Z", story_id="beta", worktree=beta,
            worker_schema=BUILDER / "schemas" / "worker-output.schema.json",
            reasoning_config_key=REASONING_CONFIG_KEY,
            reasoning_config_evidence_digest=context["host"]["reasoningConfig"]["evidenceDigest"],
            git_environment=factory.environment, worker_start_sha=first.merge_sha,
        )
        (store.run_root / "launch-records" / "beta.json").write_bytes(
            canonical_json(beta_launch.record)
        )
        complete_beta = copy.deepcopy(dispatching)
        complete_beta.update(previousState="dispatching", state="wave-workers-complete")
        complete_beta["waves"][1]["branches"][0]["workerState"] = "complete"
        store.write_transition(dispatching, complete_beta)
        receipt = copy.deepcopy(context["receipt"])
        receipt.update(
            storyId="beta", branch=beta_branch, worktree=str(beta),
            baseSha=first.merge_sha, headSha=beta_head, commitSha=beta_head,
            changedFiles=[{"path": "src/beta/value.txt", "sourcePath": None, "changeType": "added"}],
        )
        verified = verify_worker(
            factory.repo, context["spec"], context["plan"], receipt,
            beta_launch.record, factory.environment,
        )
        self.assertEqual(first.merge_sha, verified.base_sha)
        wrong = copy.deepcopy(receipt)
        wrong["baseSha"] = context["base"]
        with self.assertRaisesRegex(Exception, "start|baseSha|parented"):
            verify_worker(
                factory.repo, context["spec"], context["plan"], wrong,
                beta_launch.record, factory.environment,
            )


if __name__ == "__main__":
    unittest.main()
