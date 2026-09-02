from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "plugins" / "compass-builder"
if str(BUILDER) not in sys.path:
    sys.path.insert(0, str(BUILDER))

from compass_builder.controller import execute_run  # noqa: E402
from compass_builder.models import canonical_json  # noqa: E402
from compass_builder.state import build_execution_bundle  # noqa: E402
from tests.helpers.git_repo_factory import GitRepoFactory  # noqa: E402


CHECK = 'python -c "raise SystemExit(0)"'


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


class BuilderWorktreeIntegrationTests(unittest.TestCase):
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
            parents = factory.git(
                "rev-list", "--parents", "-n", "1", result.final_green_sha
            ).stdout.decode().split()
            self.assertEqual(3, len(parents))


if __name__ == "__main__":
    unittest.main()
