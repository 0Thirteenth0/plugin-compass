from __future__ import annotations

import ast
import copy
import importlib
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "plugins" / "compass-builder"
FIXTURES = ROOT / "tests" / "fixtures" / "compass_builder" / "rolling"
SOURCE = BUILDER / "compass_builder" / "rolling_state.py"
if str(BUILDER) not in sys.path:
    sys.path.insert(0, str(BUILDER))

from compass_builder._validation import canonical_data, canonical_digest  # noqa: E402
from compass_builder.errors import StateError  # noqa: E402
from tests.helpers.git_repo_factory import GitRepoFactory  # noqa: E402


try:
    rolling_state = importlib.import_module("compass_builder.rolling_state")
except ModuleNotFoundError as exc:
    if exc.name != "compass_builder.rolling_state":
        raise
    rolling_state = None


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.valid.json").read_text(encoding="utf-8"))


def bind_bundle(bundle: dict, sha: str, run_id: str) -> dict:
    value = copy.deepcopy(bundle)
    spec = value["runSpec"]
    plan = value["pipelinePlan"]
    spec.update(
        runId=run_id,
        baseRef="main",
        baseSha=sha,
        integrationBranch="main",
        integrationExpectedSha=sha,
    )
    plan.update(
        runId=run_id,
        baseSha=sha,
        integrationBranch="main",
        integrationExpectedSha=sha,
    )
    for item in plan["stories"]:
        item["branch"] = f"cb/{run_id}/{item['storyId']}"
    plan["normalizedInputDigest"] = canonical_digest(spec)
    plan["hostEvidenceDigest"] = canonical_digest(value["hostCapabilities"])
    return value


def dispatch_for(bundle: dict, story_id: str = "alpha") -> dict:
    plan = bundle["pipelinePlan"]
    spec = bundle["runSpec"]
    story = next(item for item in plan["stories"] if item["storyId"] == story_id)
    record = fixture("dispatch-record")
    record.update(
        dispatchId=f"dispatch-{story_id}-1",
        runId=plan["runId"],
        storyId=story_id,
        attempt=1,
        planDigest=canonical_digest(plan),
        workerStartSha=plan["integrationExpectedSha"],
        prerequisites=[],
        exactModel=spec["exactModel"],
        recommendedEffort=story["recommendedEffort"],
        writeScopes=story["writeScopes"],
        requiredOutcomeGateIds=story["requiredOutcomeGateIds"],
        gateApprovalDigests=["sha256:" + "b" * 64],
        handoffDigest=story["handoffDigest"],
        registeredClone={
            "cloneId": f"clone-{story_id}-1",
            "repositoryRootDigest": "sha256:" + "7" * 64,
            "gitCommonDirDigest": "sha256:" + "c" * 64,
            "branch": story["branch"],
        },
    )
    return record


class RollingStateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.factory = GitRepoFactory(self.base / "repository with spaces")
        self.sha = self.factory.init()
        self.bundle = bind_bundle(
            fixture("execution-bundle-v2"),
            self.sha,
            "cb-rolling-1234567890abcdef",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_store(self):
        self.assertIsNotNone(rolling_state, "rolling_state module must be implemented")
        return rolling_state.RollingStateStore(self.factory.repo, self.bundle)

    def create_store(self):
        store = self.make_store()
        state = store.create()
        return store, state

    def test_initial_state_is_canonical_plan_order_and_create_is_deterministic(self) -> None:
        store = self.make_store()
        expected = {
            "schemaVersion": "compass-builder.pipeline-state.v2",
            "runId": self.bundle["runSpec"]["runId"],
            "planDigest": canonical_digest(self.bundle["pipelinePlan"]),
            "baseSha": self.sha,
            "integrationBranch": "main",
            "initialIntegrationSha": self.sha,
            "currentIntegrationSha": self.sha,
            "lastVerifiedIntegrationSha": self.sha,
            "previousState": None,
            "state": "planned",
            "lastEventSequence": 0,
            "lastEventDigest": None,
            "activeOwners": [],
            "integrationQueue": [],
            "activeBlocker": None,
            "blockerHistory": [],
            "stories": [
                {
                    "storyId": planned["storyId"],
                    "integrationOrdinal": planned["integrationOrdinal"],
                    "lifecycle": "never-launched",
                    "blockedFromLifecycle": None,
                    "attempt": 0,
                    "workerStartSha": None,
                    "branch": planned["branch"],
                    "registeredCloneDigest": None,
                    "workerReceiptDigest": None,
                    "verificationEvidenceDigest": None,
                    "importEvidenceDigest": None,
                    "mergeIntentDigest": None,
                    "integrationSha": None,
                    "postCheckEvidenceDigest": None,
                    "gateEvidenceDigests": [],
                }
                for planned in self.bundle["pipelinePlan"]["stories"]
            ],
        }
        self.assertEqual(expected, store.initial_state())
        self.assertEqual(expected, store.create())
        self.assertEqual(canonical_data(expected), store.state_path.read_bytes())
        self.assertEqual(canonical_data(self.bundle), store.execution_bundle_path.read_bytes())
        self.assertEqual(expected, store.load())
        self.assertEqual(
            {
                "controller.json",
                "create-transaction.json",
                "dispatch-records",
                "events",
                "execution-bundle.json",
                "state.json",
                "transactions",
            },
            {item.name for item in store.run_root.iterdir()},
        )
        self.assertIn("rolling-runs", store.run_root.parts)
        self.assertFalse((self.factory.repo / ".compass-builder" / "runs").exists())
        self.assertEqual(expected, store.create())

    def test_execution_bundle_root_must_be_a_mapping(self) -> None:
        pairs = list(self.bundle.items())
        with self.assertRaisesRegex(StateError, "mapping|object"):
            rolling_state.RollingStateStore(self.factory.repo, pairs)

    def test_controller_root_must_be_ignored_before_any_artifact_write(self) -> None:
        self.sha = self.factory.commit({".gitignore": "# intentionally unignored\n"}, "unignore")
        self.bundle = bind_bundle(self.bundle, self.sha, self.bundle["runSpec"]["runId"])

        def construct_and_create():
            rolling_state.RollingStateStore(self.factory.repo, self.bundle).create()

        with self.assertRaisesRegex(StateError, "ignored"):
            construct_and_create()
        self.assertFalse((self.factory.repo / ".compass-builder" / "rolling-runs").exists())

    def test_controller_root_must_be_absent_from_index_before_artifact_write(self) -> None:
        tracked = self.factory.repo / ".compass-builder" / "tracked.txt"
        tracked.parent.mkdir()
        tracked.write_text("tracked\n", encoding="utf-8")
        self.factory.git("add", "-f", ".compass-builder/tracked.txt")
        self.factory.git("commit", "-m", "track forbidden controller data")
        self.sha = self.factory.sha("HEAD")
        self.bundle = bind_bundle(self.bundle, self.sha, self.bundle["runSpec"]["runId"])

        def construct_and_create():
            rolling_state.RollingStateStore(self.factory.repo, self.bundle).create()

        with self.assertRaisesRegex(StateError, "index|tracked|absent"):
            construct_and_create()
        self.assertFalse((tracked.parent / "rolling-runs").exists())

    def test_partial_ignore_of_only_state_leaf_writes_no_artifact(self) -> None:
        pattern = "/.compass-builder/rolling-runs/*/state.json\n"
        self.sha = self.factory.commit({".gitignore": pattern}, "partially ignore state")
        self.bundle = bind_bundle(self.bundle, self.sha, self.bundle["runSpec"]["runId"])
        with self.assertRaisesRegex(StateError, "ignored|control root|artifact"):
            rolling_state.RollingStateStore(self.factory.repo, self.bundle).create()
        self.assertFalse((self.factory.repo / ".compass-builder").exists())

    def test_partial_ignore_of_durable_run_but_not_staging_writes_no_artifact(self) -> None:
        pattern = f"/.compass-builder/rolling-runs/{self.bundle['runSpec']['runId']}/\n"
        self.sha = self.factory.commit({".gitignore": pattern}, "omit staging ignore")
        self.bundle = bind_bundle(self.bundle, self.sha, self.bundle["runSpec"]["runId"])
        with self.assertRaisesRegex(StateError, "ignored|control root|staging"):
            rolling_state.RollingStateStore(self.factory.repo, self.bundle).create()
        self.assertFalse((self.factory.repo / ".compass-builder").exists())

    def test_create_staging_parent_sync_failure_leaves_explicit_interruption(self) -> None:
        store = self.make_store()
        actual_sync = getattr(store, "_sync_directory", lambda *_args, **_kwargs: None)

        def fail_staging_entry(path, root, *, label):
            if label == "rolling create staging directory entry":
                raise StateError("simulated strict staging parent sync failure")
            return actual_sync(path, root, label=label)

        with patch.object(store, "_sync_directory", create=True,
                          side_effect=fail_staging_entry):
            with self.assertRaisesRegex(StateError, "staging parent sync"):
                store.create()
        self.assertFalse(store.run_root.exists())
        staged = list(store.rolling_root.glob(f".{store.run_id}.create-*"))
        self.assertEqual(1, len(staged))
        with self.assertRaisesRegex(StateError, "interrupted rolling create"):
            store.create()

    def test_create_retries_parent_sync_for_an_existing_root_after_failure(self) -> None:
        store = self.make_store()
        actual_sync = store._sync_directory
        failures = 0

        def fail_controller_entry(path, root, *, label):
            nonlocal failures
            if label == "rolling controller root entry":
                failures += 1
                raise StateError("simulated controller-root parent sync failure")
            return actual_sync(path, root, label=label)

        with patch.object(store, "_sync_directory", side_effect=fail_controller_entry):
            for _attempt in range(2):
                with self.assertRaisesRegex(StateError, "controller-root parent sync"):
                    store.create()
        self.assertEqual(2, failures)
        self.assertFalse(store.rolling_root.exists())

    def test_create_run_rename_parent_sync_failure_never_returns_success(self) -> None:
        store = self.make_store()
        actual_sync = getattr(store, "_sync_directory", lambda *_args, **_kwargs: None)

        def fail_run_publication(path, root, *, label):
            if label == "rolling run directory publication":
                raise StateError("simulated strict rolling-root sync failure")
            return actual_sync(path, root, label=label)

        with patch.object(store, "_sync_directory", create=True,
                          side_effect=fail_run_publication):
            with self.assertRaisesRegex(StateError, "rolling-root sync"):
                store.create()
        self.assertTrue(store.run_root.is_dir())
        self.assertEqual(store.initial_state(), store.create())

    def test_create_directory_durability_precedes_each_success_boundary(self) -> None:
        store = self.make_store()
        actual_sync = getattr(store, "_sync_directory", lambda *_args, **_kwargs: None)
        actual_publish = store._publish_run_directory
        order = []

        def observe_sync(path, root, *, label):
            order.append(label)
            return actual_sync(path, root, label=label)

        def observe_publish(*args, **kwargs):
            order.append("run directory rename")
            return actual_publish(*args, **kwargs)

        with patch.object(store, "_sync_directory", create=True,
                          side_effect=observe_sync), patch.object(
            store, "_publish_run_directory", side_effect=observe_publish
        ):
            store.create()
        self.assertEqual(
            [
                "rolling controller root entry",
                "rolling publication root entry",
                "rolling create staging directory entry",
                "rolling create staging contents",
                "run directory rename",
                "rolling run directory publication",
            ],
            order,
        )

    def test_dispatch_publishes_record_then_event_before_atomic_state_and_uses_no_launcher(self) -> None:
        store, previous = self.create_store()
        record = dispatch_for(self.bundle)
        original_replace = store._atomic_replace

        def observe_replace(state, *args, **kwargs):
            self.assertEqual(canonical_data(record), store.dispatch_record_path(1).read_bytes())
            self.assertTrue(store.event_path(1).is_file())
            self.assertTrue(store.transaction_path(1).is_file())
            return original_replace(state, *args, **kwargs)

        publication_order = []
        original_write = store._write_exact

        def observe_write(path, value, *, label):
            publication_order.append(Path(path).parent.name)
            return original_write(path, value, label=label)

        with patch.object(store, "_write_exact", side_effect=observe_write), patch.object(
            store, "_atomic_replace", side_effect=observe_replace
        ):
            current = store.record_dispatch(
                previous, record, owner_id="worker-alpha-1",
                event_id="event-alpha-dispatch",
                occurred_at="2026-09-01T12:02:00Z",
            )
        self.assertEqual(["transactions", "dispatch-records", "events"], publication_order)
        event = json.loads(store.event_path(1).read_text(encoding="utf-8"))
        story = current["stories"][0]
        clone_digest = canonical_digest(record["registeredClone"])
        self.assertEqual("running", current["state"])
        self.assertEqual("running", story["lifecycle"])
        self.assertEqual(1, story["attempt"])
        self.assertEqual(clone_digest, story["registeredCloneDigest"])
        self.assertEqual(canonical_digest(record), event["evidenceDigest"])
        self.assertEqual(
            canonical_digest(
                {"dispatchRecordDigest": canonical_digest(record), "ownerId": "worker-alpha-1"}
            ),
            event["payloadDigest"],
        )
        self.assertEqual(canonical_digest(event), current["lastEventDigest"])
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse({"subprocess", "process_runner", "launcher", "rolling_controller"} & imported)

    def test_evidence_directories_are_synced_in_publication_order_before_state_exchange(self) -> None:
        store, previous = self.create_store()
        order = []
        actual_sync = getattr(store, "_sync_evidence_directory", lambda _path: None)
        actual_exchange = store._exchange_state_candidate

        def observe_sync(path):
            order.append(Path(path).name)
            return actual_sync(path)

        def observe_exchange(*args, **kwargs):
            order.append("state-exchange")
            return actual_exchange(*args, **kwargs)

        with patch.object(store, "_sync_evidence_directory", create=True,
                          side_effect=observe_sync), patch.object(
            store, "_exchange_state_candidate", side_effect=observe_exchange
        ):
            store.record_dispatch(
                previous, dispatch_for(self.bundle), owner_id="worker-alpha-1",
                event_id="event-alpha-dispatch", occurred_at="2026-09-01T12:02:00Z",
            )
        self.assertEqual(
            ["transactions", "dispatch-records", "events", "state-exchange"], order
        )

    def test_evidence_directory_sync_failure_blocks_state_and_exact_retry_completes(self) -> None:
        store, previous = self.create_store()
        predecessor = store.state_path.read_bytes()
        record = dispatch_for(self.bundle)
        arguments = {
            "owner_id": "worker-alpha-1", "event_id": "event-alpha-dispatch",
            "occurred_at": "2026-09-01T12:02:00Z",
        }

        def fail_event_sync(path):
            if Path(path) == store.events_path:
                raise StateError("simulated evidence directory sync failure")

        with patch.object(store, "_sync_evidence_directory", create=True,
                          side_effect=fail_event_sync):
            with self.assertRaisesRegex(StateError, "directory sync"):
                store.record_dispatch(previous, record, **arguments)
        self.assertEqual(predecessor, store.state_path.read_bytes())
        with self.assertRaisesRegex(StateError, "interrupted"):
            store.load()
        current = store.record_dispatch(previous, record, **arguments)
        self.assertEqual(current, store.load())

    def test_dispatch_enforces_sequential_width_without_publishing_extra_evidence(self) -> None:
        self.bundle["runSpec"]["executionMode"] = "sequential"
        self.bundle["pipelinePlan"]["executionMode"] = "sequential"
        self.bundle["pipelinePlan"]["concurrency"] = 1
        self.bundle["runSpec"]["stories"][1]["dependsOn"] = []
        self.bundle["pipelinePlan"]["stories"][1]["dependsOn"] = []
        self.bundle["pipelinePlan"]["initialReadyStoryIds"] = ["alpha", "beta"]
        self.bundle["pipelinePlan"]["normalizedInputDigest"] = canonical_digest(
            self.bundle["runSpec"]
        )
        store, planned = self.create_store()
        running = store.record_dispatch(
            planned, dispatch_for(self.bundle), owner_id="worker-alpha-1",
            event_id="event-alpha-dispatch", occurred_at="2026-09-01T12:02:00Z",
        )
        with self.assertRaisesRegex(StateError, "concurrency|width"):
            store.record_dispatch(
                running, dispatch_for(self.bundle, "beta"), owner_id="worker-beta-1",
                event_id="event-beta-dispatch", occurred_at="2026-09-01T12:03:00Z",
            )
        self.assertFalse(store.transaction_path(2).exists())
        self.assertFalse(store.event_path(2).exists())
        self.assertFalse(store.dispatch_record_path(2).exists())
        self.assertEqual(running, store.load())

    def test_parallel_plan_allows_exactly_its_bounded_width(self) -> None:
        self.bundle["runSpec"]["stories"][1]["dependsOn"] = []
        self.bundle["pipelinePlan"]["stories"][1]["dependsOn"] = []
        self.bundle["pipelinePlan"]["initialReadyStoryIds"] = ["alpha", "beta"]
        self.bundle["pipelinePlan"]["normalizedInputDigest"] = canonical_digest(
            self.bundle["runSpec"]
        )
        store, planned = self.create_store()
        running = store.record_dispatch(
            planned, dispatch_for(self.bundle), owner_id="worker-alpha-1",
            event_id="event-alpha-dispatch", occurred_at="2026-09-01T12:02:00Z",
        )
        full = store.record_dispatch(
            running, dispatch_for(self.bundle, "beta"), owner_id="worker-beta-1",
            event_id="event-beta-dispatch", occurred_at="2026-09-01T12:03:00Z",
        )
        self.assertEqual(2, self.bundle["pipelinePlan"]["concurrency"])
        self.assertEqual(["alpha", "beta"], [item["storyId"] for item in full["activeOwners"]])

    def test_commit_rechecks_exact_predecessor_and_does_not_overwrite_intervening_state(self) -> None:
        store, previous = self.create_store()
        original_replace = store._atomic_replace
        observed = {}

        def swap_before_commit(current, *args, **kwargs):
            intervening = copy.deepcopy(current)
            intervening["activeOwners"][0]["ownerId"] = "intervening-owner"
            observed["bytes"] = canonical_data(intervening)
            store.state_path.write_bytes(observed["bytes"])
            return original_replace(current, *args, **kwargs)

        with patch.object(store, "_atomic_replace", side_effect=swap_before_commit):
            with self.assertRaisesRegex(StateError, "changed|predecessor|CAS"):
                store.record_dispatch(
                    previous, dispatch_for(self.bundle), owner_id="worker-alpha-1",
                    event_id="event-alpha-dispatch", occurred_at="2026-09-01T12:02:00Z",
                )
        self.assertEqual(observed["bytes"], store.state_path.read_bytes())

    def test_final_commit_preserves_state_changed_at_atomic_exchange(self) -> None:
        store, previous = self.create_store()
        actual_exchange = getattr(store, "_exchange_state_candidate", None)
        intervening = {}

        def mutate_at_exchange(candidate, displaced, parent_descriptor):
            value = json.loads(Path(candidate).read_text(encoding="utf-8"))
            value["activeOwners"][0]["ownerId"] = "intervening-owner"
            intervening["bytes"] = canonical_data(store._validate_state(value))
            store.state_path.write_bytes(intervening["bytes"])
            return actual_exchange(candidate, displaced, parent_descriptor)

        with patch.object(store, "_exchange_state_candidate", create=True,
                          side_effect=mutate_at_exchange):
            with self.assertRaisesRegex(StateError, "CAS|predecessor|intervening|displaced"):
                store.record_dispatch(
                    previous, dispatch_for(self.bundle), owner_id="worker-alpha-1",
                    event_id="event-alpha-dispatch", occurred_at="2026-09-01T12:02:00Z",
                )
        self.assertEqual(intervening["bytes"], store.state_path.read_bytes())
        with self.assertRaisesRegex(StateError, "interrupted atomic"):
            store.load()

    def test_state_backup_deletion_sync_failure_never_claims_unverified_success(self) -> None:
        store, previous = self.create_store()
        actual_sync = getattr(store, "_sync_directory", lambda *_args, **_kwargs: None)
        order = []

        def fail_after_backup_deletion(path, root, *, label):
            order.append(label)
            if label == "rolling state backup deletion":
                raise StateError("simulated strict run-root sync failure")
            return actual_sync(path, root, label=label)

        record = dispatch_for(self.bundle)
        arguments = {
            "owner_id": "worker-alpha-1", "event_id": "event-alpha-dispatch",
            "occurred_at": "2026-09-01T12:02:00Z",
        }
        with patch.object(store, "_sync_directory", create=True,
                          side_effect=fail_after_backup_deletion):
            with self.assertRaisesRegex(StateError, "run-root sync"):
                store.record_dispatch(previous, record, **arguments)
        self.assertLess(
            order.index("rolling state exchange"),
            order.index("rolling state backup deletion"),
        )
        self.assertEqual([], list(store.run_root.glob("state-*.tmp")))
        committed = store.load()
        self.assertEqual("running", committed["stories"][0]["lifecycle"])
        self.assertEqual(committed, store.record_dispatch(previous, record, **arguments))

    def test_state_exchange_and_backup_deletion_are_synced_in_order(self) -> None:
        store, previous = self.create_store()
        actual_sync = getattr(store, "_sync_directory", lambda *_args, **_kwargs: None)
        actual_exchange = store._exchange_state_candidate
        actual_unlink = store._unlink_at
        order = []

        def observe_sync(path, root, *, label):
            order.append(label)
            return actual_sync(path, root, label=label)

        def observe_exchange(*args, **kwargs):
            order.append("state exchange operation")
            return actual_exchange(*args, **kwargs)

        def observe_unlink(*args, **kwargs):
            order.append("backup deletion operation")
            return actual_unlink(*args, **kwargs)

        with patch.object(store, "_sync_directory", create=True,
                          side_effect=observe_sync), patch.object(
            store, "_exchange_state_candidate", side_effect=observe_exchange
        ), patch.object(store, "_unlink_at", side_effect=observe_unlink):
            store.record_dispatch(
                previous, dispatch_for(self.bundle), owner_id="worker-alpha-1",
                event_id="event-alpha-dispatch", occurred_at="2026-09-01T12:02:00Z",
            )
        expected = [
            "state exchange operation", "rolling state exchange",
            "backup deletion operation", "rolling state backup deletion",
        ]
        self.assertEqual(expected, [item for item in order if item in expected])

    def test_state_temp_name_collision_fails_without_deleting_existing_leaf(self) -> None:
        store, previous = self.create_store()
        original_replace = store._atomic_replace
        collision = store.run_root / "state-fixed.tmp"

        def collide_before_commit(*args, **kwargs):
            collision.write_bytes(b"preexisting")
            return original_replace(*args, **kwargs)

        with patch.object(store, "_atomic_replace", side_effect=collide_before_commit), \
                patch.object(rolling_state.secrets, "token_hex", return_value="fixed"):
            with self.assertRaisesRegex(StateError, "published|persistence|exist"):
                store.record_dispatch(
                    previous, dispatch_for(self.bundle), owner_id="worker-alpha-1",
                    event_id="event-alpha-dispatch", occurred_at="2026-09-01T12:02:00Z",
                )
        self.assertEqual(b"preexisting", collision.read_bytes())

    def test_create_rename_and_state_replace_occur_under_directory_guard(self) -> None:
        store = self.make_store()
        actual_guard = rolling_state._secure_files._directory_guard
        actual_publish = store._publish_run_directory
        depth = 0
        rename_guarded = []

        @contextmanager
        def observe_guard(*args, **kwargs):
            nonlocal depth
            with actual_guard(*args, **kwargs) as descriptor:
                depth += 1
                try:
                    yield descriptor
                finally:
                    depth -= 1

        def observe_rename(*args, **kwargs):
            rename_guarded.append(depth > 0)
            return actual_publish(*args, **kwargs)

        with patch.object(rolling_state._secure_files, "_directory_guard", observe_guard), \
                patch.object(store, "_publish_run_directory", side_effect=observe_rename):
            planned = store.create()
        self.assertEqual([True], rename_guarded)

        actual_replace = store._exchange_state_candidate
        replace_guarded = []

        def observe_replace(*args, **kwargs):
            replace_guarded.append(depth > 0)
            return actual_replace(*args, **kwargs)

        with patch.object(rolling_state._secure_files, "_directory_guard", observe_guard), \
                patch.object(store, "_exchange_state_candidate", side_effect=observe_replace):
            store.record_dispatch(
                planned, dispatch_for(self.bundle), owner_id="worker-alpha-1",
                event_id="event-alpha-dispatch", occurred_at="2026-09-01T12:02:00Z",
            )
        self.assertEqual([True], replace_guarded)

    def test_run_directory_publication_never_replaces_concurrent_destination(self) -> None:
        store = self.make_store()
        actual_publish = getattr(store, "_publish_run_directory", None)
        appeared_identity = {}

        def destination_appears(staging, parent_descriptor):
            store.run_root.mkdir()
            appeared_identity["value"] = store.run_root.stat().st_ino
            return actual_publish(staging, parent_descriptor)

        with patch.object(store, "_publish_run_directory", create=True,
                          side_effect=destination_appears):
            with self.assertRaisesRegex(StateError, "appeared|exists|no-replace"):
                store.create()
        self.assertTrue(store.run_root.is_dir())
        self.assertEqual(appeared_identity["value"], store.run_root.stat().st_ino)
        self.assertEqual([], list(store.run_root.iterdir()))
        self.assertTrue(any(item.name.startswith(f".{store.run_id}.create-")
                            for item in store.rolling_root.iterdir()))

    def test_create_fails_closed_if_staging_directory_becomes_reparse(self) -> None:
        store = self.make_store()
        outside = self.base / "outside-create-target"
        outside.mkdir()
        marker = outside / "marker.txt"
        marker.write_text("unchanged\n", encoding="utf-8")
        staging = [None]
        actual_mkdir = store._mkdir_at
        actual_reparse = rolling_state.is_reparse

        def observe_mkdir(path, descriptor):
            result = actual_mkdir(path, descriptor)
            if Path(path).name.startswith(f".{store.run_id}.create-"):
                staging[0] = Path(path)
            return result

        def injected_reparse(path):
            return Path(path) == staging[0] or actual_reparse(path)

        with patch.object(store, "_mkdir_at", side_effect=observe_mkdir), patch.object(
            rolling_state, "is_reparse", side_effect=injected_reparse
        ):
            with self.assertRaisesRegex(StateError, "staging|unsafe|changed"):
                store.create()
        self.assertFalse(store.run_root.exists())
        self.assertEqual("unchanged\n", marker.read_text(encoding="utf-8"))
        self.assertEqual(["marker.txt"], [item.name for item in outside.iterdir()])

    def test_state_leaf_reparse_swap_fails_before_replace_without_outside_write(self) -> None:
        store, planned = self.create_store()
        predecessor = store.state_path.read_bytes()
        outside = self.base / "outside-state-target"
        outside.mkdir()
        marker = outside / "marker.txt"
        marker.write_text("unchanged\n", encoding="utf-8")
        armed = [False]
        actual_write = store._write_new_bytes_at
        actual_reparse = rolling_state.is_reparse

        def arm_after_temporary(path, payload, *, label, parent_descriptor):
            result = actual_write(path, payload, label=label,
                                  parent_descriptor=parent_descriptor)
            if Path(path).name.startswith("state-"):
                armed[0] = True
            return result

        def injected_reparse(path):
            return armed[0] and Path(path) == store.state_path or actual_reparse(path)

        with patch.object(store, "_write_new_bytes_at", side_effect=arm_after_temporary), \
                patch.object(rolling_state, "is_reparse", side_effect=injected_reparse):
            with self.assertRaisesRegex(StateError, "changed|reparse"):
                store.record_dispatch(
                    planned, dispatch_for(self.bundle), owner_id="worker-alpha-1",
                    event_id="event-alpha-dispatch", occurred_at="2026-09-01T12:02:00Z",
                )
        self.assertEqual(predecessor, store.state_path.read_bytes())
        self.assertEqual("unchanged\n", marker.read_text(encoding="utf-8"))
        self.assertEqual([], list(store.run_root.glob("state-*.tmp")))

    def test_completion_appends_chain_binds_receipt_and_removes_owner(self) -> None:
        store, planned = self.create_store()
        running = store.record_dispatch(
            planned,
            dispatch_for(self.bundle),
            owner_id="worker-alpha-1",
            event_id="event-alpha-dispatch",
            occurred_at="2026-09-01T12:02:00Z",
        )
        receipt = "sha256:" + "d" * 64
        complete = store.record_completion(
            running,
            story_id="alpha",
            worker_receipt_digest=receipt,
            event_id="event-alpha-completion",
            occurred_at="2026-09-01T12:03:00Z",
        )
        first = json.loads(store.event_path(1).read_text(encoding="utf-8"))
        second = json.loads(store.event_path(2).read_text(encoding="utf-8"))
        self.assertEqual(canonical_digest(first), second["previousEventDigest"])
        self.assertEqual(receipt, second["evidenceDigest"])
        self.assertEqual(
            canonical_digest(
                {"storyId": "alpha", "attempt": 1, "workerReceiptDigest": receipt}
            ),
            second["payloadDigest"],
        )
        self.assertEqual("worker-complete-unverified", complete["stories"][0]["lifecycle"])
        self.assertEqual(receipt, complete["stories"][0]["workerReceiptDigest"])
        self.assertEqual([], complete["activeOwners"])
        self.assertEqual("running", complete["state"])
        self.assertEqual(complete, store.load())
        self.assertEqual(
            complete,
            store.record_completion(
                running,
                story_id="alpha",
                worker_receipt_digest=receipt,
                event_id="event-alpha-completion",
                occurred_at="2026-09-01T12:03:00Z",
            ),
        )

    def test_completion_transaction_attempt_must_match_story_state(self) -> None:
        store, planned = self.create_store()
        running = store.record_dispatch(
            planned, dispatch_for(self.bundle), owner_id="worker-alpha-1",
            event_id="event-alpha-dispatch", occurred_at="2026-09-01T12:02:00Z",
        )
        store.record_completion(
            running, story_id="alpha", worker_receipt_digest="sha256:" + "d" * 64,
            event_id="event-alpha-completion", occurred_at="2026-09-01T12:03:00Z",
        )
        transaction = json.loads(store.transaction_path(2).read_text(encoding="utf-8"))
        event = json.loads(store.event_path(2).read_text(encoding="utf-8"))
        state = json.loads(store.state_path.read_text(encoding="utf-8"))
        transaction["input"]["attempt"] = 2
        transaction["inputDigest"] = canonical_digest(transaction["input"])
        event["payloadDigest"] = transaction["inputDigest"]
        transaction["eventDigest"] = canonical_digest(event)
        state["lastEventDigest"] = transaction["eventDigest"]
        transaction["nextStateDigest"] = canonical_digest(state)
        store.event_path(2).write_bytes(canonical_data(event))
        store.state_path.write_bytes(canonical_data(state))
        store.transaction_path(2).write_bytes(canonical_data(transaction))
        with self.assertRaisesRegex(StateError, "attempt"):
            store.load()

    def test_transition_cas_exact_retry_and_mismatched_duplicate_fail_closed(self) -> None:
        store, previous = self.create_store()
        record = dispatch_for(self.bundle)
        arguments = {
            "owner_id": "worker-alpha-1",
            "event_id": "event-alpha-dispatch",
            "occurred_at": "2026-09-01T12:02:00Z",
        }
        current = store.record_dispatch(previous, record, **arguments)
        immutable = {
            path: path.read_bytes()
            for path in (store.transaction_path(1), store.event_path(1), store.dispatch_record_path(1))
        }
        self.assertEqual(current, store.record_dispatch(previous, record, **arguments))
        self.assertEqual(immutable, {path: path.read_bytes() for path in immutable})
        with self.assertRaises(StateError):
            store.record_dispatch(previous, record, **{**arguments, "owner_id": "other-owner"})
        completed = store.record_completion(
            current,
            story_id="alpha",
            worker_receipt_digest="sha256:" + "d" * 64,
            event_id="event-alpha-completion",
            occurred_at="2026-09-01T12:03:00Z",
        )
        self.assertEqual("worker-complete-unverified", completed["stories"][0]["lifecycle"])
        with self.assertRaisesRegex(StateError, "stale|changed|predecessor"):
            store.record_completion(
                current,
                story_id="alpha",
                worker_receipt_digest="sha256:" + "e" * 64,
                event_id="event-alpha-completion-other",
                occurred_at="2026-09-01T12:04:00Z",
            )

    def test_interrupted_publication_is_explicit_and_exact_retry_completes_it(self) -> None:
        store, previous = self.create_store()
        record = dispatch_for(self.bundle)
        arguments = {
            "owner_id": "worker-alpha-1",
            "event_id": "event-alpha-dispatch",
            "occurred_at": "2026-09-01T12:02:00Z",
        }
        with patch.object(store, "_atomic_replace", side_effect=StateError("simulated interruption")):
            with self.assertRaisesRegex(StateError, "simulated interruption"):
                store.record_dispatch(previous, record, **arguments)
        evidence = {
            path: path.read_bytes()
            for path in (store.transaction_path(1), store.event_path(1), store.dispatch_record_path(1))
        }
        self.assertEqual(previous, json.loads(store.state_path.read_text(encoding="utf-8")))
        with self.assertRaisesRegex(StateError, "interrupted"):
            store.load()
        current = store.record_dispatch(previous, record, **arguments)
        self.assertEqual(evidence, {path: path.read_bytes() for path in evidence})
        self.assertEqual(current, store.load())

    def test_interruption_after_dispatch_evidence_is_retryable(self) -> None:
        store, previous = self.create_store()
        record = dispatch_for(self.bundle)
        arguments = {
            "owner_id": "worker-alpha-1", "event_id": "event-alpha-dispatch",
            "occurred_at": "2026-09-01T12:02:00Z",
        }
        original_write = store._write_exact

        def stop_before_event(path, value, *, label):
            if Path(path).parent == store.events_path:
                raise StateError("simulated event interruption")
            return original_write(path, value, label=label)

        with patch.object(store, "_write_exact", side_effect=stop_before_event):
            with self.assertRaisesRegex(StateError, "event interruption"):
                store.record_dispatch(previous, record, **arguments)
        self.assertTrue(store.transaction_path(1).is_file())
        self.assertTrue(store.dispatch_record_path(1).is_file())
        self.assertFalse(store.event_path(1).exists())
        with self.assertRaisesRegex(StateError, "interrupted"):
            store.load()
        current = store.record_dispatch(previous, record, **arguments)
        self.assertEqual("running", current["stories"][0]["lifecycle"])
        self.assertEqual(current, store.load())

    def test_duplicate_event_id_is_rejected_before_any_new_publication(self) -> None:
        self.bundle["runSpec"]["stories"][1]["dependsOn"] = []
        self.bundle["pipelinePlan"]["stories"][1]["dependsOn"] = []
        self.bundle["pipelinePlan"]["initialReadyStoryIds"] = ["alpha", "beta"]
        self.bundle["pipelinePlan"]["normalizedInputDigest"] = canonical_digest(
            self.bundle["runSpec"]
        )
        store, planned = self.create_store()
        running = store.record_dispatch(
            planned, dispatch_for(self.bundle), owner_id="worker-alpha-1",
            event_id="duplicate-event", occurred_at="2026-09-01T12:02:00Z",
        )
        with self.assertRaisesRegex(StateError, "event chain|duplicates"):
            store.record_dispatch(
                running, dispatch_for(self.bundle, "beta"), owner_id="worker-beta-1",
                event_id="duplicate-event", occurred_at="2026-09-01T12:03:00Z",
            )
        self.assertFalse(store.transaction_path(2).exists())
        self.assertEqual(running, store.load())

    def test_closed_artifact_set_rejects_missing_malformed_oversized_and_noncanonical(self) -> None:
        store, state = self.create_store()
        original_state = store.state_path.read_bytes()
        original_controller = store.controller_path.read_bytes()
        cases = (
            ("missing", lambda: store.controller_path.unlink(), lambda: store.controller_path.write_bytes(original_controller)),
            ("malformed", lambda: store.state_path.write_bytes(b"{bad\n"), lambda: store.state_path.write_bytes(original_state)),
            (
                "oversized|bound",
                lambda: store.state_path.write_bytes(b"x" * (rolling_state.MAX_STATE_BYTES + 1)),
                lambda: store.state_path.write_bytes(original_state),
            ),
            (
                "canonical",
                lambda: store.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8"),
                lambda: store.state_path.write_bytes(original_state),
            ),
            (
                "unknown",
                lambda: (store.run_root / "unknown.json").write_text("{}", encoding="utf-8"),
                lambda: (store.run_root / "unknown.json").unlink(),
            ),
        )
        for message, corrupt, restore in cases:
            with self.subTest(message=message):
                corrupt()
                try:
                    with self.assertRaisesRegex(StateError, message):
                        store.load()
                finally:
                    restore()
        unknown = copy.deepcopy(state)
        unknown["futureField"] = True
        store.state_path.write_bytes(canonical_data(unknown))
        with self.assertRaisesRegex(StateError, "unknown field"):
            store.load()

    def test_orphaned_state_temporary_is_an_explicit_interruption(self) -> None:
        store, _state = self.create_store()
        (store.run_root / "state-interrupted.tmp").write_bytes(b"partial")
        (store.run_root / "state-interrupted.displaced.tmp").write_bytes(b"partial")
        with self.assertRaisesRegex(StateError, "interrupted atomic"):
            store.load()

    def test_transaction_event_and_dispatch_corruption_fail_closed(self) -> None:
        store, planned = self.create_store()
        store.record_dispatch(
            planned, dispatch_for(self.bundle), owner_id="worker-alpha-1",
            event_id="event-alpha-dispatch", occurred_at="2026-09-01T12:02:00Z",
        )
        targets = (store.transaction_path(1), store.event_path(1), store.dispatch_record_path(1))
        originals = {path: path.read_bytes() for path in targets}
        corruptions = []
        for path in targets:
            value = json.loads(originals[path].decode("utf-8"))
            version = copy.deepcopy(value)
            version["schemaVersion"] = "future-version"
            corruptions.extend(
                ((path, b"{malformed\n", "malformed"),
                 (path, json.dumps(value, indent=2).encode("utf-8"), "canonical"),
                 (path, canonical_data(version), "version|schemaVersion|invalid"))
            )
        for path, payload, message in corruptions:
            with self.subTest(path=path.name, message=message):
                path.write_bytes(payload)
                try:
                    with self.assertRaisesRegex(StateError, message):
                        store.load()
                finally:
                    path.write_bytes(originals[path])

    def test_history_reconstructs_each_state_instead_of_trusting_adjacent_digests(self) -> None:
        store, planned = self.create_store()
        running = store.record_dispatch(
            planned, dispatch_for(self.bundle), owner_id="worker-alpha-1",
            event_id="event-alpha-dispatch", occurred_at="2026-09-01T12:02:00Z",
        )
        store.record_completion(
            running, story_id="alpha", worker_receipt_digest="sha256:" + "d" * 64,
            event_id="event-alpha-completion", occurred_at="2026-09-01T12:03:00Z",
        )
        first = json.loads(store.transaction_path(1).read_text(encoding="utf-8"))
        second = json.loads(store.transaction_path(2).read_text(encoding="utf-8"))
        forged = "sha256:" + "9" * 64
        first["nextStateDigest"] = forged
        second["previousStateDigest"] = forged
        store.transaction_path(1).write_bytes(canonical_data(first))
        store.transaction_path(2).write_bytes(canonical_data(second))
        with self.assertRaisesRegex(StateError, "derived|reconstruct|state digest"):
            store.load()

    def test_history_reconstructs_dispatch_owner_from_transaction_input(self) -> None:
        store, planned = self.create_store()
        current = store.record_dispatch(
            planned, dispatch_for(self.bundle), owner_id="worker-alpha-1",
            event_id="event-alpha-dispatch", occurred_at="2026-09-01T12:02:00Z",
        )
        transaction = json.loads(store.transaction_path(1).read_text(encoding="utf-8"))
        event = json.loads(store.event_path(1).read_text(encoding="utf-8"))
        transaction["input"]["ownerId"] = "forged-owner"
        transaction["inputDigest"] = canonical_digest(transaction["input"])
        event["payloadDigest"] = transaction["inputDigest"]
        transaction["eventDigest"] = canonical_digest(event)
        current["lastEventDigest"] = transaction["eventDigest"]
        transaction["nextStateDigest"] = canonical_digest(current)
        store.transaction_path(1).write_bytes(canonical_data(transaction))
        store.event_path(1).write_bytes(canonical_data(event))
        store.state_path.write_bytes(canonical_data(current))
        with self.assertRaisesRegex(StateError, "derived|owner|reconstruct"):
            store.load()

    def test_interrupted_event_with_foreign_run_id_fails_even_with_recomputed_digests(self) -> None:
        store, planned = self.create_store()
        with patch.object(store, "_atomic_replace",
                          side_effect=StateError("simulated state interruption")):
            with self.assertRaisesRegex(StateError, "state interruption"):
                store.record_dispatch(
                    planned, dispatch_for(self.bundle), owner_id="worker-alpha-1",
                    event_id="event-alpha-dispatch", occurred_at="2026-09-01T12:02:00Z",
                )
        event = json.loads(store.event_path(1).read_text(encoding="utf-8"))
        transaction = json.loads(store.transaction_path(1).read_text(encoding="utf-8"))
        event["runId"] = "cb-foreign-1234567890abcdef"
        transaction["eventDigest"] = canonical_digest(event)
        store.event_path(1).write_bytes(canonical_data(event))
        store.transaction_path(1).write_bytes(canonical_data(transaction))
        with self.assertRaisesRegex(StateError, "run|identity"):
            store._load_publication(allow_interrupted=True)

    def test_nested_and_leaf_reparse_evidence_fail_closed(self) -> None:
        store, planned = self.create_store()
        store.record_dispatch(
            planned, dispatch_for(self.bundle), owner_id="worker-alpha-1",
            event_id="event-alpha-dispatch", occurred_at="2026-09-01T12:02:00Z",
        )
        with patch.object(
            rolling_state, "is_reparse",
            side_effect=lambda path: Path(path) == store.transactions_path,
        ):
            with self.assertRaisesRegex(StateError, "unsafe|reparse"):
                store.load()
        event_path = store.event_path(1)
        actual = rolling_state._secure_files.is_reparse
        with patch.object(
            rolling_state._secure_files, "is_reparse",
            side_effect=lambda path: Path(path) == event_path or actual(path),
        ):
            with self.assertRaisesRegex(StateError, "reparse|symlink"):
                store.load()

    def test_unreadable_and_bounded_collection_fail_closed(self) -> None:
        store, _state = self.create_store()
        actual_read = rolling_state.read_no_follow

        def unreadable(path, root, *, label, max_bytes):
            if Path(path) == store.state_path:
                raise OSError("access denied")
            return actual_read(path, root, label=label, max_bytes=max_bytes)

        with patch.object(rolling_state, "read_no_follow", side_effect=unreadable):
            with self.assertRaisesRegex(StateError, "unavailable|access denied"):
                store.load()
        for index in range(len(self.bundle["pipelinePlan"]["stories"]) + 1):
            (store.dispatch_records_path / f"{index + 1:08d}.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(StateError, "bound|collection"):
            store.load()

    def test_directory_enumeration_stops_at_each_bound(self) -> None:
        store, _state = self.create_store()
        actual_iterdir = Path.iterdir

        for target, expected in ((store.run_root, 10), (store.dispatch_records_path, 3)):
            consumed = [0]

            def fake_entries(consumed=consumed):
                for index in range(32):
                    consumed[0] += 1
                    yield SimpleNamespace(name=f"hostile-{index:08d}.json")

            def hostile(path, target=target):
                if Path(path) != target:
                    yield from actual_iterdir(path)
                    return
                yield from fake_entries()

            with self.subTest(target=target.name), patch.object(Path, "iterdir", hostile), \
                    patch.object(rolling_state.os, "scandir", side_effect=lambda _fd: fake_entries()):
                with self.assertRaises(StateError):
                    (store.load() if target == store.run_root else store._read_collection(
                        target, label="hostile", maximum=2
                    ))
                self.assertEqual(expected, consumed[0])

    def test_interrupted_create_scan_stops_at_bound(self) -> None:
        store = self.make_store()
        actual_iterdir = Path.iterdir
        consumed = [0]

        def fake_entries():
            for index in range(rolling_state.MAX_PIPELINE_EVENTS + 10):
                consumed[0] += 1
                yield SimpleNamespace(name=f"other-run-{index}")

        def hostile(path):
            if Path(path) != store.rolling_root:
                yield from actual_iterdir(path)
                return
            yield from fake_entries()

        with patch.object(Path, "iterdir", hostile), patch.object(
            rolling_state.os, "scandir", side_effect=lambda _fd: fake_entries()
        ):
            with self.assertRaisesRegex(StateError, "bound|entries"):
                store.create()
        self.assertEqual(rolling_state.MAX_PIPELINE_EVENTS + 1, consumed[0])
        self.assertFalse(store.run_root.exists())

    def test_symlink_and_reparse_run_root_are_rejected(self) -> None:
        store = self.make_store()
        store.run_root.mkdir(parents=True)
        with patch.object(
            rolling_state, "is_reparse", side_effect=lambda path: Path(path) == store.run_root
        ):
            with self.assertRaisesRegex(StateError, "reparse"):
                store.create()
        store.run_root.rmdir()
        outside = self.base / "outside"
        outside.mkdir()
        store.run_root.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(outside, store.run_root, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlink creation is unavailable: {exc}")
        with self.assertRaisesRegex(StateError, "symlink|reparse|partial"):
            store.create()

    def test_run_path_escape_is_rejected(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        escaped = self.make_store()
        escaped.run_root = outside
        escaped.state_path = outside / "state.json"
        with self.assertRaisesRegex(StateError, "escape|contain"):
            escaped.create()

    def test_unknown_bundle_fields_and_versions_fail_closed(self) -> None:
        for field, mutate in (
            ("unknown field", lambda value: value.update(futureField=True)),
            ("schemaVersion", lambda value: value.update(schemaVersion="future-version")),
        ):
            with self.subTest(field=field):
                candidate = copy.deepcopy(self.bundle)
                mutate(candidate)
                with self.assertRaisesRegex(StateError, field):
                    rolling_state.RollingStateStore(self.factory.repo, candidate)

    def test_repository_paths_with_spaces_bind_controller_identity(self) -> None:
        store, _state = self.create_store()
        controller = json.loads(store.controller_path.read_text(encoding="utf-8"))
        self.assertEqual(str(self.factory.repo), controller["repositoryIdentity"]["repositoryRoot"])
        self.assertIn(" ", controller["repositoryIdentity"]["repositoryRoot"])
        self.assertEqual(
            canonical_digest(controller["repositoryIdentity"]),
            controller["repositoryIdentityDigest"],
        )
        self.assertNotIn("RollingStateStore", vars(importlib.import_module("compass_builder")))
        self.assertNotIn("rolling_state", (BUILDER / "compass_builder" / "cli.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
