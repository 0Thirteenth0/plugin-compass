from __future__ import annotations

import copy
from contextlib import ExitStack
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Mapping
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "plugins" / "compass-builder"
if str(BUILDER) not in sys.path:
    sys.path.insert(0, str(BUILDER))

import compass_builder.controller as controller_module  # noqa: E402
import compass_builder.durable_artifacts as artifacts_module  # noqa: E402
import compass_builder.state as state_module  # noqa: E402
from compass_builder.controller import (  # noqa: E402
    ControllerError, codex_worker_transport, execute_run,
)
from compass_builder.durable_artifacts import ArtifactJournal  # noqa: E402
from compass_builder.launcher import (  # noqa: E402
    BUNDLED_WORKER_SCHEMA, FailureEvidence, PreparedLaunch,
    REASONING_CONFIG_KEY, prepare_launch, prepare_retry_launch,
    validate_launch_record,
)
from compass_builder.git_environment import prepare_git_environment  # noqa: E402
from compass_builder.models import canonical_digest, canonical_json  # noqa: E402
from compass_builder.process_runner import (  # noqa: E402
    BoundedProcessError, run_bounded as actual_run_bounded,
)
from compass_builder.state import StateError, StateStore, build_execution_bundle  # noqa: E402
from tests.helpers.git_repo_factory import GitRepoFactory  # noqa: E402


CHECK = 'python -c "raise SystemExit(0)"'
INTERNAL_USAGE_EVENT = "_worker-usage-observation"


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def _terminal(
    *, input_tokens: int = 100, cached_input_tokens: int = 40,
    output_tokens: int = 20, reasoning_output_tokens: int = 5,
) -> bytes:
    return (json.dumps({
        "type": "turn.completed",
        "usage": {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
            "reasoning_output_tokens": reasoning_output_tokens,
        },
    }, separators=(",", ":")) + "\n").encode("utf-8")


def _worker_output(status: str = "succeeded") -> bytes:
    return (json.dumps({
        "schemaVersion": "compass-builder.worker-output.v1",
        "status": status,
        "summary": "Synthetic worker output.",
        "acceptanceChecks": [{
            "check": "focused", "status": "passed",
            "evidence": "Synthetic focused evidence.",
        }],
        "blocker": None if status == "succeeded" else "synthetic blocker",
    }, separators=(",", ":")) + "\n").encode("utf-8")


def _bundle(
    factory: GitRepoFactory, base_sha: str, *,
    story_ids: tuple[str, ...] = ("alpha",), mode: str = "sequential",
) -> dict[str, object]:
    host = json.loads((
        ROOT / "tests" / "fixtures" / "compass_builder"
        / "host-capabilities.valid.json"
    ).read_text(encoding="utf-8"))
    suffix = "1" if len(story_ids) == 1 else "2"
    run_id = f"cb-usage-{mode}-{suffix * 16}"
    stories = []
    planned = []
    for index, story_id in enumerate(story_ids):
        stories.append({
            "id": story_id,
            "title": story_id.title(),
            "description": f"Implement {story_id} safely.",
            "dependsOn": [],
            "writeScopes": [f"src/{story_id}"],
            "acceptanceChecks": [f"{story_id} check passes."],
            "validationCommands": [CHECK],
            "independentReviewPath": None,
            "sharedState": {"mode": "none", "description": "No shared state."},
            "priority": index + 1,
            "completionState": "pending",
            "complexity": "medium",
            "ambiguity": "low",
            "risk": "low",
            "validationStrength": "decisive",
        })
        planned.append({
            "storyId": story_id,
            "branch": f"cb/{run_id}/{story_id}",
            "recommendedEffort": "low",
            "handoffDigest": "sha256:" + str(index + 1) * 64,
        })
    spec = {
        "schemaVersion": "compass-builder.run-spec.v1",
        "runId": run_id,
        "baseRef": "refs/heads/main",
        "baseSha": base_sha,
        "integrationBranch": "main",
        "integrationExpectedSha": base_sha,
        "mode": mode,
        "exactModel": host["selectedModel"],
        "effortPolicyVersion": "plugin-compass.effort-policy.v1",
        "hostConcurrencyCeiling": 2,
        "userConcurrencyCeiling": 2,
        "validationCommands": [CHECK],
        "stories": stories,
    }
    width = 1 if mode == "sequential" else len(story_ids)
    waves = (
        [{"waveIndex": index, "storyIds": [story_id]}
         for index, story_id in enumerate(story_ids)]
        if width == 1 else
        [{"waveIndex": 0, "storyIds": list(story_ids)}]
    )
    plan = {
        "schemaVersion": "compass-builder.wave-plan.v1",
        "runId": run_id,
        "baseSha": base_sha,
        "integrationBranch": "main",
        "integrationExpectedSha": base_sha,
        "normalizedInputDigest": _digest(spec),
        "hostEvidenceDigest": _digest(host),
        "effortPolicyVersion": "plugin-compass.effort-policy.v1",
        "mode": mode,
        "reasons": ["Synthetic worker-usage fixture."],
        "concurrency": width,
        "stories": planned,
        "waves": waves,
    }
    return build_execution_bundle(
        spec, plan, host, "2026-09-01T12:01:00Z", factory.repo,
    )


def _receipt(launch, status: str, *, succeeded_head: str | None = None) -> dict:
    succeeded = status == "succeeded"
    return {
        "schemaVersion": "compass-builder.worker-receipt.v1",
        "runId": launch.record["runId"],
        "storyId": launch.record["storyId"],
        "branch": launch.record["branch"],
        "worktree": launch.record["worktree"],
        "exactModel": launch.record["exactModel"],
        "effort": launch.record["effort"],
        "baseSha": launch.record["workerStartSha"],
        "headSha": succeeded_head,
        "commitSha": succeeded_head,
        "changedFiles": ([{
            "path": f"src/{launch.record['storyId']}/value.txt",
            "sourcePath": None,
            "changeType": "modified",
        }] if succeeded else []),
        "checks": ([{
            "name": "focused", "command": CHECK, "status": "passed",
            "evidenceDigest": "sha256:" + "e" * 64,
        }] if succeeded else []),
        "elapsedMs": 1,
        "status": status,
        "blocker": None if succeeded else f"synthetic {status}",
    }


def _commit_transport(launch, _story, _timeout_ms, _event_sink):
    worktree = Path(str(launch.record["worktree"]))
    story_id = str(launch.record["storyId"])
    path = worktree / "src" / story_id / "value.txt"
    path.write_text("after\n", encoding="utf-8", newline="\n")
    environment = dict(launch.environment)
    for arguments in (("add", "--all"), ("commit", "-m", f"{story_id} worker")):
        completed = subprocess.run(
            ["git", "--no-pager", "-C", str(worktree), *arguments],
            check=False, capture_output=True, shell=False, env=environment,
        )
        if completed.returncode:
            raise AssertionError(completed.stderr.decode("utf-8", errors="replace"))
    head = subprocess.run(
        ["git", "--no-pager", "-C", str(worktree), "rev-parse", "HEAD"],
        check=True, capture_output=True, shell=False, env=environment,
    ).stdout.decode("ascii").strip()
    return _receipt(launch, "succeeded", succeeded_head=head)


def _journal_records(factory: GitRepoFactory, run_id: str) -> tuple[dict, ...]:
    control_root = factory.repo / ".compass-builder"
    return ArtifactJournal(
        control_root / "runs" / run_id, control_root
    ).read("worker-usage")


class ControllerWorkerUsageTests(unittest.TestCase):
    def test_direct_transport_observation_is_bound_to_launch_and_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            factory = GitRepoFactory(Path(temporary))
            base_sha = factory.init({"src/alpha/value.txt": "before\n"})
            bundle = _bundle(factory, base_sha)
            receipts: dict[str, dict] = {}
            events: list[tuple[str, dict]] = []
            stdout = _terminal() + _worker_output()

            def runner(argv, **kwargs):
                if argv[0] == "codex":
                    worktree = Path(kwargs["cwd"])
                    story_id = worktree.name
                    (worktree / "src" / story_id / "value.txt").write_text(
                        "after\n", encoding="utf-8", newline="\n"
                    )
                    return subprocess.CompletedProcess(list(argv), 0, stdout, b"")
                return actual_run_bounded(argv, **kwargs)

            def measured_transport(launch, story, timeout_ms, event_sink):
                receipt = codex_worker_transport(
                    launch, story, timeout_ms, event_sink
                )
                receipts[str(launch.record["storyId"])] = dict(receipt)
                return receipt

            with patch.object(controller_module, "run_bounded", side_effect=runner):
                result = execute_run(
                    factory.repo, bundle, worker_transport=measured_transport,
                    timeout_ms=30_000,
                    event_sink=lambda kind, details: events.append((kind, dict(details))),
                )

            usage_events = [details for kind, details in events if kind == "worker-usage"]
            self.assertEqual(1, len(usage_events))
            self.assertEqual(tuple(usage_events), _journal_records(factory, result.run_id))
            usage = usage_events[0]
            launch = json.loads((
                factory.repo / ".compass-builder" / "runs" / result.run_id
                / "launch-records" / "alpha.json"
            ).read_text(encoding="utf-8"))
            self.assertTrue(usage["observed"])
            self.assertEqual(100, usage["usage"]["inputTokens"])
            self.assertEqual(canonical_digest(launch), usage["launchDigest"])
            self.assertEqual(
                canonical_digest(receipts["alpha"]), usage["workerReceiptDigest"]
            )
            self.assertEqual("succeeded", usage["terminalStatus"])
            expected_identity = {
                "runId": result.run_id,
                "storyId": "alpha",
                "attempt": 1,
                "launchDigest": canonical_digest(launch),
            }
            for kind in ("worker-launch", "worker-completion", "worker-branch-import"):
                matching = [details for event_kind, details in events if event_kind == kind]
                self.assertEqual(1, len(matching), kind)
                self.assertTrue(
                    set(expected_identity) <= set(matching[0]),
                    f"{kind} lacks exact attempt identity",
                )
                self.assertEqual(
                    expected_identity,
                    {field: matching[0][field] for field in expected_identity},
                    kind,
                )

    def test_custom_transport_without_telemetry_gets_explicit_missing_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            factory = GitRepoFactory(Path(temporary))
            base_sha = factory.init({"src/alpha/value.txt": "before\n"})
            bundle = _bundle(factory, base_sha)
            events: list[tuple[str, dict]] = []

            result = execute_run(
                factory.repo, bundle, worker_transport=_commit_transport,
                timeout_ms=30_000,
                event_sink=lambda kind, details: events.append((kind, dict(details))),
            )

            records = _journal_records(factory, result.run_id)
            self.assertEqual(1, len(records))
            self.assertEqual(False, records[0]["observed"])
            self.assertEqual("no-terminal-usage", records[0]["unavailableReason"])
            self.assertEqual("succeeded", records[0]["terminalStatus"])
            self.assertIsNotNone(records[0]["workerReceiptDigest"])
            self.assertEqual(
                [records[0]], [details for kind, details in events if kind == "worker-usage"]
            )

    def test_returned_failure_statuses_each_persist_exactly_one_record(self):
        for status in ("failed", "blocked", "timed-out"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                factory = GitRepoFactory(Path(temporary))
                base_sha = factory.init({"src/alpha/value.txt": "before\n"})
                bundle = _bundle(factory, base_sha)
                events: list[tuple[str, dict]] = []

                def transport(launch, _story, _timeout_ms, _event_sink):
                    return _receipt(launch, status)

                with self.assertRaises(ControllerError):
                    execute_run(
                        factory.repo, bundle, worker_transport=transport,
                        timeout_ms=30_000,
                        event_sink=lambda kind, details: events.append((kind, dict(details))),
                    )
                records = _journal_records(
                    factory, str(bundle["runSpec"]["runId"])
                )
                self.assertEqual(1, len(records))
                self.assertEqual(status, records[0]["terminalStatus"])
                self.assertFalse(records[0]["observed"])
                self.assertIsNotNone(records[0]["workerReceiptDigest"])
                self.assertEqual(1, sum(kind == "worker-usage" for kind, _ in events))

    def test_timeout_and_transport_exception_preserve_partial_stdout_usage(self):
        cases = ("timeout", "exception")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                factory = GitRepoFactory(Path(temporary))
                base_sha = factory.init({"src/alpha/value.txt": "before\n"})
                bundle = _bundle(factory, base_sha)
                events: list[tuple[str, dict]] = []

                if case == "timeout":
                    def runner(argv, **kwargs):
                        if argv[0] == "codex":
                            raise BoundedProcessError(
                                "process timed out after 1 seconds",
                                stdout=_terminal(
                                    input_tokens=13, cached_input_tokens=3,
                                    output_tokens=7,
                                ),
                            )
                        return actual_run_bounded(argv, **kwargs)

                    transport = codex_worker_transport
                    context = patch.object(
                        controller_module, "run_bounded", side_effect=runner
                    )
                else:
                    def transport(launch, _story, _timeout_ms, event_sink):
                        event_sink(INTERNAL_USAGE_EVENT, {"stdout": _terminal(
                            input_tokens=13, cached_input_tokens=3,
                            output_tokens=7
                        )})
                        raise OSError("synthetic transport failure")

                    context = patch.object(controller_module, "time", wraps=time)

                with context, self.assertRaises(ControllerError):
                    execute_run(
                        factory.repo, bundle, worker_transport=transport,
                        timeout_ms=30_000,
                        event_sink=lambda kind, details: events.append((kind, dict(details))),
                    )
                records = _journal_records(
                    factory, str(bundle["runSpec"]["runId"])
                )
                self.assertEqual(1, len(records))
                self.assertTrue(records[0]["observed"])
                self.assertEqual(13, records[0]["usage"]["inputTokens"])
                self.assertEqual(
                    "timed-out" if case == "timeout" else "transport-error",
                    records[0]["terminalStatus"],
                )
                self.assertEqual(
                    case == "timeout", records[0]["workerReceiptDigest"] is not None
                )
                self.assertEqual(1, sum(kind == "worker-usage" for kind, _ in events))

    def test_structured_output_and_later_controller_failures_still_record_usage(self):
        for stage in ("structured-output", "controller-commit", "controller-check"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:
                factory = GitRepoFactory(Path(temporary))
                base_sha = factory.init({"src/alpha/value.txt": "before\n"})
                bundle = _bundle(factory, base_sha)
                stdout = _terminal(
                    input_tokens=17, cached_input_tokens=4, output_tokens=8
                )
                if stage in {"controller-commit", "controller-check"}:
                    stdout += _worker_output()

                def runner(argv, **kwargs):
                    if argv[0] == "codex":
                        if stage == "controller-check":
                            worktree = Path(kwargs["cwd"])
                            (worktree / "src" / "alpha" / "value.txt").write_text(
                                "after\n", encoding="utf-8", newline="\n"
                            )
                        return subprocess.CompletedProcess(list(argv), 0, stdout, b"")
                    return actual_run_bounded(argv, **kwargs)

                with ExitStack() as stack:
                    stack.enter_context(patch.object(
                        controller_module, "run_bounded", side_effect=runner
                    ))
                    if stage == "controller-check":
                        stack.enter_context(patch.object(
                            controller_module, "run_bounded_text",
                            side_effect=OSError("synthetic check processing failure"),
                        ))
                    with self.assertRaises(ControllerError):
                        execute_run(factory.repo, bundle, timeout_ms=30_000)
                records = _journal_records(
                    factory, str(bundle["runSpec"]["runId"])
                )
                self.assertEqual(1, len(records))
                self.assertTrue(records[0]["observed"])
                self.assertEqual(17, records[0]["usage"]["inputTokens"])
                self.assertEqual("transport-error", records[0]["terminalStatus"])
                self.assertIsNone(records[0]["workerReceiptDigest"])

    def test_transport_cannot_forward_forged_malformed_or_duplicate_telemetry(self):
        cases = ("public-forgery", "malformed-internal", "duplicate-internal")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                factory = GitRepoFactory(Path(temporary))
                base_sha = factory.init({"src/alpha/value.txt": "before\n"})
                bundle = _bundle(factory, base_sha)
                external: list[tuple[str, dict]] = []

                def transport(launch, _story, _timeout_ms, event_sink):
                    if case == "public-forgery":
                        event_sink("worker-usage", {
                            "runId": launch.record["runId"], "forged": True,
                        })
                    elif case == "malformed-internal":
                        event_sink(INTERNAL_USAGE_EVENT, {"stdout": "not bytes"})
                    else:
                        event_sink(INTERNAL_USAGE_EVENT, {"stdout": _terminal()})
                        event_sink(INTERNAL_USAGE_EVENT, {"stdout": _terminal()})
                    return _receipt(launch, "failed")

                with self.assertRaises(ControllerError):
                    execute_run(
                        factory.repo, bundle, worker_transport=transport,
                        timeout_ms=30_000,
                        event_sink=lambda kind, details: external.append(
                            (kind, dict(details))
                        ),
                    )
                records = _journal_records(
                    factory, str(bundle["runSpec"]["runId"])
                )
                self.assertEqual(1, len(records))
                self.assertFalse(records[0]["observed"])
                self.assertEqual(
                    "invalid-transport-telemetry",
                    records[0]["unavailableReason"],
                )
                self.assertEqual(
                    [records[0]],
                    [details for kind, details in external if kind == "worker-usage"],
                )

    def test_completed_builtin_with_second_observation_finalizes_once_then_rethrows(self):
        with tempfile.TemporaryDirectory() as temporary:
            factory = GitRepoFactory(Path(temporary))
            base_sha = factory.init({"src/alpha/value.txt": "before\n"})
            bundle = _bundle(factory, base_sha)
            external: list[tuple[str, dict]] = []
            stdout = _terminal() + _worker_output()

            def runner(argv, **kwargs):
                if argv[0] == "codex":
                    worktree = Path(kwargs["cwd"])
                    (worktree / "src" / "alpha" / "value.txt").write_text(
                        "after\n", encoding="utf-8", newline="\n"
                    )
                    return subprocess.CompletedProcess(list(argv), 0, stdout, b"")
                return actual_run_bounded(argv, **kwargs)

            def duplicate_after_completion(launch, story, timeout_ms, event_sink):
                receipt = codex_worker_transport(
                    launch, story, timeout_ms, event_sink
                )
                event_sink(INTERNAL_USAGE_EVENT, {"stdout": _terminal()})
                return receipt

            with patch.object(
                controller_module, "run_bounded", side_effect=runner
            ), self.assertRaisesRegex(ControllerError, "duplicate"):
                execute_run(
                    factory.repo, bundle,
                    worker_transport=duplicate_after_completion,
                    timeout_ms=30_000,
                    event_sink=lambda kind, details: external.append(
                        (kind, dict(details))
                    ),
                )

            records = _journal_records(factory, str(bundle["runSpec"]["runId"]))
            self.assertEqual(1, len(records))
            self.assertFalse(records[0]["observed"])
            self.assertIsNone(records[0]["usage"])
            self.assertIsNone(records[0]["workerReceiptDigest"])
            self.assertEqual("transport-error", records[0]["terminalStatus"])
            self.assertEqual(
                "invalid-transport-telemetry", records[0]["unavailableReason"]
            )
            self.assertEqual(
                [records[0]],
                [details for kind, details in external if kind == "worker-usage"],
            )

    def test_cross_launch_receipt_finalizes_unavailable_once_then_rethrows(self):
        mutations = {
            "storyId": "cross-story",
            "branch": "cb/cross/alpha",
            "worktree": "C:\\synthetic\\cross-launch\\alpha",
            "baseSha": "f" * 40,
        }
        for field, value in mutations.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                factory = GitRepoFactory(Path(temporary))
                base_sha = factory.init({"src/alpha/value.txt": "before\n"})
                bundle = _bundle(factory, base_sha)
                external: list[tuple[str, dict]] = []
                stdout = _terminal() + _worker_output()

                def runner(argv, **kwargs):
                    if argv[0] == "codex":
                        worktree = Path(kwargs["cwd"])
                        (worktree / "src" / "alpha" / "value.txt").write_text(
                            "after\n", encoding="utf-8", newline="\n"
                        )
                        return subprocess.CompletedProcess(
                            list(argv), 0, stdout, b""
                        )
                    return actual_run_bounded(argv, **kwargs)

                def cross_launch_receipt(launch, story, timeout_ms, event_sink):
                    receipt = dict(codex_worker_transport(
                        launch, story, timeout_ms, event_sink
                    ))
                    receipt[field] = value
                    return receipt

                with patch.object(
                    controller_module, "run_bounded", side_effect=runner
                ), self.assertRaises(ControllerError):
                    execute_run(
                        factory.repo, bundle,
                        worker_transport=cross_launch_receipt,
                        timeout_ms=30_000,
                        event_sink=lambda kind, details: external.append(
                            (kind, dict(details))
                        ),
                    )

                records = _journal_records(
                    factory, str(bundle["runSpec"]["runId"])
                )
                self.assertEqual(1, len(records))
                self.assertFalse(records[0]["observed"])
                self.assertIsNone(records[0]["usage"])
                self.assertIsNone(records[0]["workerReceiptDigest"])
                self.assertEqual("transport-error", records[0]["terminalStatus"])
                self.assertEqual(
                    "worker-receipt-binding-failed",
                    records[0]["unavailableReason"],
                )
                self.assertEqual(
                    [records[0]],
                    [
                        details for kind, details in external
                        if kind == "worker-usage"
                    ],
                )

    def test_two_parallel_custom_workers_create_distinct_records_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            factory = GitRepoFactory(Path(temporary))
            base_sha = factory.init({
                "src/alpha/value.txt": "before\n",
                "src/beta/value.txt": "before\n",
            })
            bundle = _bundle(
                factory, base_sha, story_ids=("alpha", "beta"), mode="parallel"
            )
            events: list[tuple[str, dict]] = []
            event_lock = threading.Lock()

            def sink(kind, details):
                with event_lock:
                    events.append((kind, dict(details)))

            def transport(launch, story, timeout_ms, event_sink):
                time.sleep(0.03)
                return _commit_transport(
                    launch, story, timeout_ms, event_sink
                )

            result = execute_run(
                factory.repo, bundle, worker_transport=transport,
                timeout_ms=30_000, event_sink=sink,
            )
            records = _journal_records(factory, result.run_id)
            self.assertEqual(["alpha", "beta"], sorted(
                record["storyId"] for record in records
            ))
            self.assertEqual(2, len({
                record["launchDigest"] for record in records
            }))
            self.assertEqual(2, sum(kind == "worker-usage" for kind, _ in events))

    def test_telemetry_persistence_failure_stops_the_run_before_public_event(self):
        with tempfile.TemporaryDirectory() as temporary:
            factory = GitRepoFactory(Path(temporary))
            base_sha = factory.init({"src/alpha/value.txt": "before\n"})
            bundle = _bundle(factory, base_sha)
            events: list[tuple[str, dict]] = []
            original = getattr(StateStore, "record_worker_usage", None)
            self.assertIsNotNone(original, "StateStore must own worker usage persistence")
            with patch.object(
                StateStore, "record_worker_usage",
                side_effect=StateError("synthetic persistence failure"),
            ), self.assertRaisesRegex(ControllerError, "persistence failure"):
                execute_run(
                    factory.repo, bundle, worker_transport=_commit_transport,
                    timeout_ms=30_000,
                    event_sink=lambda kind, details: events.append((kind, dict(details))),
                )
            self.assertEqual(0, sum(kind == "worker-usage" for kind, _ in events))


class WorkerUsageJournalTests(unittest.TestCase):
    def _store(
        self, temporary: str, *, publish_launch: bool = True,
        story_ids: tuple[str, ...] = ("alpha",),
    ):
        factory = GitRepoFactory(Path(temporary))
        base_sha = factory.init({
            f"src/{story_id}/value.txt": "before\n" for story_id in story_ids
        })
        bundle = _bundle(factory, base_sha, story_ids=story_ids, mode="sequential")
        provisional = StateStore(factory.repo, bundle["runSpec"], bundle["wavePlan"])
        provisional.create(provisional.initial_state(), execution_bundle=bundle)
        environment = prepare_git_environment(
            provisional.run_root / "git-environment",
            base_environment=factory.environment.environment,
        )
        store = StateStore(
            factory.repo, bundle["runSpec"], bundle["wavePlan"], environment
        )
        worktree = store.registered_worktree("alpha")
        worktree.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, store.worktree_root, ignore_errors=True)
        launch = prepare_launch(
            bundle["runSpec"], bundle["wavePlan"], bundle["hostCapabilities"],
            planning_timestamp=str(bundle["planningTimestamp"]),
            story_id="alpha", worktree=worktree,
            worker_schema=BUNDLED_WORKER_SCHEMA,
            reasoning_config_key=REASONING_CONFIG_KEY,
            reasoning_config_evidence_digest=str(
                bundle["hostCapabilities"]["reasoningConfig"]["evidenceDigest"]
            ),
            git_environment=environment, worker_start_sha=base_sha,
        )
        if publish_launch:
            controller_module._publish_launch(store, "alpha", launch)
        return factory, bundle, store, launch

    def _record(self, launch: PreparedLaunch | Mapping[str, object]) -> dict:
        source = launch.record if isinstance(launch, PreparedLaunch) else launch
        return {
            "schemaVersion": "compass-builder.worker-usage.v1",
            "source": "codex-exec-jsonl-stdout",
            "runId": source["runId"],
            "storyId": source["storyId"],
            "attempt": source["attempt"],
            "exactModel": source["exactModel"],
            "effort": source["effort"],
            "launchDigest": canonical_digest(dict(source)),
            "workerReceiptDigest": None,
            "terminalStatus": "transport-error",
            "observed": False,
            "unavailableReason": "no-terminal-usage",
            "usage": None,
        }

    def _retry_record(
        self, first_launch, *, kind="reasoning", source="controller",
        run_id=None, story_id=None, evidence_digest=None,
        previous_launch_digest=None,
    ):
        return {
            "schemaVersion": "compass-builder.retry-evidence.v1",
            "runId": run_id or first_launch.record["runId"],
            "storyId": story_id or first_launch.record["storyId"],
            "attempt": 2,
            "source": source,
            "kind": kind,
            "evidenceDigest": evidence_digest or "sha256:" + "f" * 64,
            "previousLaunchDigest": (
                previous_launch_digest or canonical_digest(dict(first_launch.record))
            ),
        }

    def _publish_retry(
        self, store, bundle, first_launch, worktree, *,
        record_retry: bool = True, kind: str = "reasoning",
        source: str = "controller",
    ):
        evidence_digest = "sha256:" + "f" * 64
        store.record_failure_evidence(
            blocked_from_state="dispatching", reason="synthetic reasoning failure",
            evidence_digest=evidence_digest, story_id="alpha",
            observed_head=str(first_launch.record["workerStartSha"]),
        )
        retry = prepare_retry_launch(
            bundle["runSpec"], bundle["wavePlan"], bundle["hostCapabilities"],
            planning_timestamp=str(bundle["planningTimestamp"]),
            story_id="alpha", worktree=worktree,
            worker_schema=BUNDLED_WORKER_SCHEMA,
            reasoning_config_key=REASONING_CONFIG_KEY,
            reasoning_config_evidence_digest=str(
                bundle["hostCapabilities"]["reasoningConfig"]["evidenceDigest"]
            ),
            git_environment=store.git_environment,
            previous_launch=first_launch.record,
            failure_evidence=FailureEvidence(
                kind="reasoning", evidence_digest=evidence_digest,
            ),
        )
        if record_retry:
            self.assertTrue(
                hasattr(store, "record_retry_evidence"),
                "StateStore must own closed retry authorization evidence",
            )
            store.record_retry_evidence(self._retry_record(
                first_launch, kind=kind, source=source,
                evidence_digest=evidence_digest,
            ))
        controller_module._publish_launch(store, "alpha", retry)
        return retry

    def _sync_launch_argv(self, launch: dict[str, object]) -> None:
        launch["argv"] = [
            "codex", "exec", "-C", str(launch["worktree"]),
            "-m", str(launch["exactModel"]),
            "-c", (
                f'{launch["reasoningConfigKey"]}="{launch["effort"]}"'
            ),
            "--disable", "multi_agent", "--disable", "plugins",
            "--disable", "hooks", "--ignore-user-config", "--ephemeral",
            "--approve-for-me", "--json", "--output-schema",
            str(launch["workerOutputSchemaPath"]), "-",
        ]

    def _replace_launch(self, store, launch: Mapping[str, object]) -> None:
        path = store.launch_record_path(
            str(launch["storyId"]), int(launch["attempt"])
        )
        path.write_bytes(canonical_json(launch))

    def _assert_usage_rejected(self, store, launch, phase: str) -> None:
        record = self._record(launch)
        if phase == "write":
            with self.assertRaises(StateError):
                store.record_worker_usage(record)
        else:
            ArtifactJournal(store.run_root, store.control_root).record(
                "worker-usage", record
            )
            restarted = StateStore(
                store.repository.root, store.spec, store.plan
            )
            with self.assertRaises(StateError):
                restarted.worker_usage_records()

    def test_state_store_validates_binds_and_reads_distinct_attempts_after_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            factory, bundle, store, first_launch = self._store(temporary)
            self.assertTrue(hasattr(store, "record_worker_usage"))
            retry_launch = self._publish_retry(
                store, bundle, first_launch,
                Path(str(first_launch.record["worktree"])),
            )
            first = self._record(first_launch)
            second = self._record(retry_launch)
            store.record_worker_usage(first)
            store.record_worker_usage(second)
            self.assertTrue((
                store.run_root / "launch-records" / "alpha.json"
            ).is_file())
            self.assertTrue((
                store.run_root / "launch-records" / "__attempt-2__alpha.json"
            ).is_file())

            restarted = StateStore(
                factory.repo, bundle["runSpec"], bundle["wavePlan"]
            )
            self.assertEqual((first, second), restarted.worker_usage_records())

            wrong_run = copy.deepcopy(first)
            wrong_run["runId"] = "cb-wrong-run-1111111111111111"
            with self.assertRaisesRegex(StateError, "another run"):
                store.record_worker_usage(wrong_run)
            malformed = copy.deepcopy(first)
            malformed["extra"] = True
            with self.assertRaises(StateError):
                store.record_worker_usage(malformed)

    def test_worker_usage_journal_keeps_secure_allowlist_and_entry_guarantees(self):
        with tempfile.TemporaryDirectory() as temporary:
            factory, _bundle_value, store, launch = self._store(temporary)
            record = self._record(launch)
            store.record_worker_usage(record)
            journal = ArtifactJournal(store.run_root, store.control_root)
            with self.assertRaises(ValueError):
                journal.record("worker-usage-unknown", record)
            with patch.object(
                artifacts_module, "is_reparse",
                side_effect=lambda path: Path(path).name == "worker-usage",
            ), self.assertRaisesRegex(StateError, "unsafe"):
                store.worker_usage_records()

        corruptions = ("digest-mismatch", "unknown-entry", "oversized")
        for corruption in corruptions:
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as temporary:
                _factory, _bundle_value, store, launch = self._store(temporary)
                record = self._record(launch)
                directory = store.run_root / "worker-usage"
                directory.mkdir()
                payload = canonical_json(record, "worker-usage")
                if corruption == "digest-mismatch":
                    (directory / ("0" * 64 + ".json")).write_bytes(payload)
                elif corruption == "unknown-entry":
                    (directory / "unexpected.txt").write_bytes(payload)
                else:
                    (directory / ("0" * 64 + ".json")).write_bytes(
                        b"x" * (artifacts_module.MAX_RECORD_BYTES + 1)
                    )
                with self.assertRaises(StateError):
                    store.worker_usage_records()

    def test_restart_rejects_digest_valid_noncanonical_json_encodings(self):
        variants = ("duplicate-key", "whitespace", "key-order")
        for variant in variants:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as temporary:
                _factory, _bundle_value, store, launch = self._store(temporary)
                record = self._record(launch)
                canonical = canonical_json(record, "worker-usage")
                if variant == "duplicate-key":
                    payload = canonical.replace(
                        b'"attempt":1', b'"attempt":2,"attempt":1', 1
                    )
                elif variant == "whitespace":
                    payload = json.dumps(
                        record, sort_keys=True, indent=2
                    ).encode("utf-8") + b"\n"
                else:
                    payload = (
                        json.dumps(
                            dict(reversed(tuple(record.items()))),
                            separators=(",", ":"),
                        ) + "\n"
                    ).encode("utf-8")
                self.assertNotEqual(canonical, payload)
                directory = store.run_root / "worker-usage"
                directory.mkdir()
                name = hashlib.sha256(payload).hexdigest() + ".json"
                (directory / name).write_bytes(payload)

                with self.assertRaisesRegex(StateError, "canonical"):
                    store.worker_usage_records()

    def test_worker_usage_requires_matching_durable_launch_on_write_and_restart(self):
        for phase in ("write", "restart"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                _factory, _bundle_value, store, launch = self._store(
                    temporary, publish_launch=False
                )
                record = self._record(launch)
                if phase == "write":
                    with self.assertRaisesRegex(StateError, "launch"):
                        store.record_worker_usage(record)
                else:
                    ArtifactJournal(
                        store.run_root, store.control_root
                    ).record("worker-usage", record)
                    with self.assertRaisesRegex(StateError, "launch"):
                        store.worker_usage_records()

    def test_worker_usage_rejects_every_drift_from_durable_launch(self):
        mutations = {
            "runId": lambda record: record.update(
                runId="cb-wrong-run-1111111111111111"
            ),
            "launchDigest": lambda record: record.update(
                launchDigest="sha256:" + "0" * 64
            ),
            "effort": lambda record: record.update(effort="medium"),
            "attempt": lambda record: record.update(attempt=2, effort="medium"),
            "storyId": lambda record: record.update(storyId="beta"),
            "exactModel": lambda record: record.update(exactModel="gpt-5.5"),
        }
        for field, mutate in mutations.items():
            for phase in ("write", "restart"):
                with self.subTest(
                    field=field, phase=phase
                ), tempfile.TemporaryDirectory() as temporary:
                    story_ids = ("alpha", "beta") if field == "storyId" else ("alpha",)
                    _factory, _bundle_value, store, launch = self._store(
                        temporary, story_ids=story_ids
                    )
                    record = self._record(launch)
                    mutate(record)
                    if phase == "write":
                        with self.assertRaises(StateError):
                            store.record_worker_usage(record)
                    else:
                        ArtifactJournal(
                            store.run_root, store.control_root
                        ).record("worker-usage", record)
                        with self.assertRaises(StateError):
                            store.worker_usage_records()

    def test_worker_usage_rejects_self_consistent_forged_launch_authority(self):
        mutations = {
            "workerStartSha": lambda launch: launch.update(
                workerStartSha="f" * 40
            ),
            "worktree": lambda launch: launch.update(
                worktree=str(Path(str(launch["worktree"])).with_name("forged"))
            ),
            "effort/initial": lambda launch: launch.update(
                effort="medium", initialRecommendedEffort="medium"
            ),
            "handoffDigest": lambda launch: launch.update(
                handoffDigest="sha256:" + "e" * 64
            ),
            "hostEvidenceDigest": lambda launch: launch.update(
                hostEvidenceDigest="sha256:" + "e" * 64
            ),
            "promptDigest": lambda launch: launch.update(
                promptDigest="sha256:" + "e" * 64
            ),
            "gitEnvironmentDigest": lambda launch: launch.update(
                gitEnvironmentDigest="sha256:" + "e" * 64
            ),
            "reasoningConfigEvidenceDigest": lambda launch: launch.update(
                reasoningConfigEvidenceDigest="sha256:" + "e" * 64
            ),
        }
        for field, mutate in mutations.items():
            for phase in ("write", "restart"):
                with self.subTest(
                    field=field, phase=phase
                ), tempfile.TemporaryDirectory() as temporary:
                    _factory, _bundle_value, store, prepared = self._store(temporary)
                    forged = copy.deepcopy(dict(prepared.record))
                    mutate(forged)
                    self._sync_launch_argv(forged)
                    self.assertEqual(forged, validate_launch_record(forged))
                    self._replace_launch(store, forged)
                    self._assert_usage_rejected(store, forged, phase)

    def test_worker_usage_rejects_forged_schema_and_argv_launch_evidence(self):
        mutations = {
            "workerOutputSchemaPath": lambda launch: launch.update(
                workerOutputSchemaPath=str(
                    Path(str(launch["workerOutputSchemaPath"])).with_name(
                        "forged-worker-output.schema.json"
                    )
                )
            ),
            "workerOutputSchemaDigest": lambda launch: launch.update(
                workerOutputSchemaDigest="sha256:" + "e" * 64
            ),
            "argv": lambda launch: launch["argv"].append("--full-auto"),
        }
        for field, mutate in mutations.items():
            for phase in ("write", "restart"):
                with self.subTest(
                    field=field, phase=phase
                ), tempfile.TemporaryDirectory() as temporary:
                    _factory, _bundle_value, store, prepared = self._store(temporary)
                    forged = copy.deepcopy(dict(prepared.record))
                    mutate(forged)
                    if field == "workerOutputSchemaPath":
                        self._sync_launch_argv(forged)
                    self._replace_launch(store, forged)
                    self._assert_usage_rejected(store, forged, phase)

    def test_worker_usage_rejects_self_consistent_forged_retry_authority(self):
        for field in ("previousLaunchDigest", "retryEvidenceDigest"):
            for phase in ("write", "restart"):
                with self.subTest(
                    field=field, phase=phase
                ), tempfile.TemporaryDirectory() as temporary:
                    _factory, bundle, store, first = self._store(temporary)
                    retry = self._publish_retry(
                        store, bundle, first, Path(str(first.record["worktree"]))
                    )
                    forged = copy.deepcopy(dict(retry.record))
                    forged[field] = "sha256:" + "e" * 64
                    self.assertEqual(forged, validate_launch_record(forged))
                    self._replace_launch(store, forged)
                    self._assert_usage_rejected(store, forged, phase)

    def test_attempt_two_rejects_non_reasoning_or_noncontroller_retry_authority(self):
        authorities = (
            ("permission", "controller"),
            ("tool", "controller"),
            ("validation", "controller"),
            ("other", "controller"),
            ("reasoning", "worker"),
        )
        for kind, source in authorities:
            for phase in ("write", "restart"):
                with self.subTest(
                    kind=kind, source=source, phase=phase
                ), tempfile.TemporaryDirectory() as temporary:
                    _factory, bundle, store, first = self._store(temporary)
                    retry = self._publish_retry(
                        store, bundle, first, Path(str(first.record["worktree"])),
                        kind=kind, source=source,
                    )
                    self._assert_usage_rejected(store, retry, phase)

    def test_retry_evidence_write_rejects_wrong_run_story_or_first_launch(self):
        mutations = {
            "runId": lambda record: record.update(
                runId="cb-wrong-run-1111111111111111"
            ),
            "storyId": lambda record: record.update(storyId="beta"),
            "previousLaunchDigest": lambda record: record.update(
                previousLaunchDigest="sha256:" + "e" * 64
            ),
        }
        for field, mutate in mutations.items():
            for phase in ("write", "restart"):
                with self.subTest(
                    field=field, phase=phase
                ), tempfile.TemporaryDirectory() as temporary:
                    factory, bundle, store, first = self._store(temporary)
                    retry = self._publish_retry(
                        store, bundle, first,
                        Path(str(first.record["worktree"])), record_retry=False,
                    )
                    self.assertTrue(hasattr(store, "record_retry_evidence"))
                    record = self._retry_record(first)
                    mutate(record)
                    if phase == "write":
                        with self.assertRaises(StateError):
                            store.record_retry_evidence(record)
                    else:
                        ArtifactJournal(
                            store.run_root, store.control_root
                        ).record("retry-evidence", record)
                        ArtifactJournal(
                            store.run_root, store.control_root
                        ).record("worker-usage", self._record(retry))
                        restarted = StateStore(
                            factory.repo, bundle["runSpec"], bundle["wavePlan"]
                        )
                        with self.assertRaises(StateError):
                            restarted.worker_usage_records()

    def test_attempt_two_rejects_absent_and_ambiguous_retry_evidence(self):
        for phase in ("write", "restart"):
            with self.subTest(
                case="absent", phase=phase
            ), tempfile.TemporaryDirectory() as temporary:
                _factory, bundle, store, first = self._store(temporary)
                retry = self._publish_retry(
                    store, bundle, first, Path(str(first.record["worktree"])),
                    record_retry=False,
                )
                self._assert_usage_rejected(store, retry, phase)

        with tempfile.TemporaryDirectory() as temporary:
            _factory, bundle, store, first = self._store(temporary)
            self.assertTrue(hasattr(store, "record_retry_evidence"))
            exact = self._retry_record(first)
            store.record_retry_evidence(exact)
            ambiguous = copy.deepcopy(exact)
            ambiguous.update(
                kind="tool", evidenceDigest="sha256:" + "e" * 64
            )
            with self.assertRaises(StateError):
                store.record_retry_evidence(ambiguous)
            retry = self._publish_retry(
                store, bundle, first, Path(str(first.record["worktree"])),
                record_retry=False,
            )
            ArtifactJournal(store.run_root, store.control_root).record(
                "retry-evidence", ambiguous
            )
            ArtifactJournal(store.run_root, store.control_root).record(
                "worker-usage", self._record(retry)
            )
            restarted = StateStore(
                store.repository.root, store.spec, store.plan
            )
            with self.assertRaises(StateError):
                restarted.worker_usage_records()

    def test_attempt_two_accepts_one_exact_controller_reasoning_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            factory, bundle, store, first = self._store(temporary)
            retry = self._publish_retry(
                store, bundle, first, Path(str(first.record["worktree"])),
            )
            record = self._record(retry)
            store.record_worker_usage(record)
            restarted = StateStore(
                factory.repo, bundle["runSpec"], bundle["wavePlan"]
            )
            self.assertEqual((record,), restarted.worker_usage_records())

    def test_worker_usage_requires_canonical_bundle_and_durable_git_environment(self):
        cases = (
            "missing-bundle", "ambiguous-bundle", "noncanonical-bundle",
            "missing-git-environment", "ambiguous-git-environment",
            "noncanonical-git-environment",
        )
        for case in cases:
            for phase in ("write", "restart"):
                with self.subTest(
                    case=case, phase=phase
                ), tempfile.TemporaryDirectory() as temporary:
                    _factory, _bundle_value, store, launch = self._store(temporary)
                    if case == "missing-bundle":
                        store.bundle_path.unlink()
                    elif case == "ambiguous-bundle":
                        (store.run_root / "plan-bundle.copy.json").write_bytes(
                            store.bundle_path.read_bytes()
                        )
                    elif case == "noncanonical-bundle":
                        bundle = json.loads(store.bundle_path.read_text(encoding="utf-8"))
                        store.bundle_path.write_text(
                            json.dumps(bundle, indent=2) + "\n",
                            encoding="utf-8", newline="\n",
                        )
                    elif case == "missing-git-environment":
                        store.git_environment.global_config.unlink()
                    elif case == "ambiguous-git-environment":
                        (store.git_environment.root / "unexpected.txt").write_text(
                            "unexpected\n", encoding="utf-8", newline="\n"
                        )
                    else:
                        store.git_environment.global_config.write_text(
                            "# not empty\n", encoding="utf-8", newline="\n"
                        )
                    self._assert_usage_rejected(store, launch, phase)

    def test_restart_rejects_noncanonical_ambiguous_and_unsafe_launch_records(self):
        for case in ("noncanonical", "ambiguous", "unsafe"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                _factory, _bundle_value, store, launch = self._store(temporary)
                ArtifactJournal(
                    store.run_root, store.control_root
                ).record("worker-usage", self._record(launch))
                launch_path = store.run_root / "launch-records" / "alpha.json"
                if case == "noncanonical":
                    value = json.loads(launch_path.read_text(encoding="utf-8"))
                    launch_path.write_text(
                        json.dumps(value, indent=2) + "\n",
                        encoding="utf-8", newline="\n",
                    )
                    context = patch.object(state_module, "_is_reparse", return_value=False)
                elif case == "ambiguous":
                    (launch_path.parent / "alpha.attempt-1.json").write_bytes(
                        launch_path.read_bytes()
                    )
                    context = patch.object(state_module, "_is_reparse", return_value=False)
                else:
                    context = patch.object(
                        state_module, "_is_reparse",
                        side_effect=lambda path: Path(path).name == "alpha.json",
                    )
                with context, self.assertRaises(StateError):
                    store.worker_usage_records()


if __name__ == "__main__":
    unittest.main()
