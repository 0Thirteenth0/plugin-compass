from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "plugins" / "compass-builder"
if str(BUILDER) not in sys.path:
    sys.path.insert(0, str(BUILDER))

from compass_builder.benchmark_runner import BenchmarkRunnerError, run_benchmark  # noqa: E402
from compass_builder.cli import main  # noqa: E402
from compass_builder.controller import ControllerResult, empty_metrics  # noqa: E402
from compass_builder.models import canonical_json  # noqa: E402
from compass_builder.state import build_execution_bundle  # noqa: E402
from tests.helpers.git_repo_factory import GitRepoFactory  # noqa: E402


CHECK = 'python -c "raise SystemExit(0)"'


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def bundles(factory: GitRepoFactory, base_sha: str):
    host = json.loads((
        ROOT / "tests" / "fixtures" / "compass_builder" / "host-capabilities.valid.json"
    ).read_text(encoding="utf-8"))
    run_id = "cb-benchmark-1234567890abcdef"
    stories = []
    planned = []
    for index, story_id in enumerate(("alpha", "beta")):
        stories.append({
            "id": story_id, "title": story_id.title(),
            "description": f"Implement {story_id} safely.", "dependsOn": [],
            "writeScopes": [f"src/{story_id}"],
            "acceptanceChecks": [f"{story_id} passes."],
            "validationCommands": [CHECK], "independentReviewPath": None,
            "sharedState": {"mode": "none", "description": "No shared state."},
            "priority": index + 1, "completionState": "pending",
            "complexity": "medium", "ambiguity": "low", "risk": "low",
            "validationStrength": "decisive",
        })
        planned.append({
            "storyId": story_id, "branch": f"cb/{run_id}/{story_id}",
            "recommendedEffort": "low",
            "handoffDigest": "sha256:" + str(index + 3) * 64,
        })
    spec = {
        "schemaVersion": "compass-builder.run-spec.v1", "runId": run_id,
        "baseRef": "refs/heads/main", "baseSha": base_sha,
        "integrationBranch": "main", "integrationExpectedSha": base_sha,
        "mode": "auto", "exactModel": host["selectedModel"],
        "effortPolicyVersion": "plugin-compass.effort-policy.v1",
        "hostConcurrencyCeiling": 2, "userConcurrencyCeiling": 2,
        "validationCommands": [CHECK], "stories": stories,
    }
    result = {}
    for mode in ("sequential", "parallel"):
        waves = (
            [{"waveIndex": 0, "storyIds": ["alpha"]},
             {"waveIndex": 1, "storyIds": ["beta"]}]
            if mode == "sequential"
            else [{"waveIndex": 0, "storyIds": ["alpha", "beta"]}]
        )
        plan = {
            "schemaVersion": "compass-builder.wave-plan.v1", "runId": run_id,
            "baseSha": base_sha, "integrationBranch": "main",
            "integrationExpectedSha": base_sha, "normalizedInputDigest": _digest(spec),
            "hostEvidenceDigest": _digest(host),
            "effortPolicyVersion": "plugin-compass.effort-policy.v1", "mode": mode,
            "reasons": [f"Synthetic {mode} benchmark."],
            "concurrency": 1 if mode == "sequential" else 2,
            "stories": planned, "waves": waves,
        }
        result[mode] = build_execution_bundle(
            spec, plan, host, "2026-09-01T12:01:00Z", factory.repo,
        )
    return result


class BuilderBenchmarkRunnerTests(unittest.TestCase):
    def test_public_run_command_uses_controller_when_dry_run_is_absent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            factory = GitRepoFactory(root / "fixture")
            base_sha = factory.init()
            bundle = bundles(factory, base_sha)["sequential"]
            plan_path = root / "plan.json"
            plan_path.write_bytes(canonical_json(bundle))
            result = ControllerResult(
                run_id=str(bundle["runSpec"]["runId"]), final_green_sha=base_sha,
                started_at="2026-09-01T12:00:00.000Z",
                ended_at="2026-09-01T12:00:01.000Z", elapsed_ms=1000,
                metrics=empty_metrics(), state={"state": "completed"},
            )
            output = StringIO()
            with patch("compass_builder.cli.execute_run", return_value=result) as execute, redirect_stdout(output):
                code = main([
                    "run", "--repo", str(factory.repo), "--plan", str(plan_path),
                ])
            self.assertEqual(0, code)
            execute.assert_called_once()
            self.assertEqual("completed", json.loads(output.getvalue())["state"])

    def test_runner_uses_fresh_repositories_and_complete_alternating_accounting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            factory = GitRepoFactory(root / "fixture")
            base_sha = factory.init({
                "src/alpha/value.txt": "before\n", "src/beta/value.txt": "before\n",
            })
            templates = bundles(factory, base_sha)
            seen_repositories: list[Path] = []
            seen_modes: list[str] = []

            def fake_run(repository, bundle, **kwargs):
                path = Path(repository).resolve()
                seen_repositories.append(path)
                mode = str(bundle["wavePlan"]["mode"])
                seen_modes.append(mode)
                self.assertEqual(base_sha, subprocess_sha(path))
                self.assertFalse((path / ".compass-builder").exists())
                sink = kwargs["event_sink"]
                sink("worker-launch", {"storyId": "alpha", "attempt": 1})
                sink("worker-completion", {
                    "storyId": "alpha", "status": "succeeded", "headSha": base_sha,
                })
                duration = 1000 if mode == "sequential" else 700
                return ControllerResult(
                    run_id=str(bundle["runSpec"]["runId"]), final_green_sha=base_sha,
                    started_at="2026-09-01T12:00:00.000Z",
                    ended_at=(
                        "2026-09-01T12:00:01.000Z" if duration == 1000
                        else "2026-09-01T12:00:00.700Z"
                    ),
                    elapsed_ms=duration, metrics=empty_metrics(), state={"state": "completed"},
                )

            output = root / "benchmark-output"
            aggregate = run_benchmark(
                factory.repo, templates["sequential"], templates["parallel"], output,
                pairs=5, timeout_ms=30_000, run_executor=fake_run,
            )
            self.assertEqual(12, len(seen_repositories))
            self.assertEqual(12, len(set(seen_repositories)))
            self.assertEqual([
                "sequential", "parallel", "sequential", "parallel",
                "parallel", "sequential", "sequential", "parallel",
                "parallel", "sequential", "sequential", "parallel",
            ], seen_modes)
            self.assertEqual(12, len(aggregate["attempts"]))
            self.assertTrue((output / "aggregate.json").is_file())
            self.assertTrue((output / "events.jsonl").is_file())
            self.assertFalse((output / "repositories").exists())

    def test_runner_rejects_less_than_five_pairs_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            factory = GitRepoFactory(root / "fixture")
            base_sha = factory.init()
            templates = bundles(factory, base_sha)
            output = root / "benchmark-output"
            with self.assertRaisesRegex(BenchmarkRunnerError, "pairs >= 5"):
                run_benchmark(
                    factory.repo, templates["sequential"], templates["parallel"], output,
                    pairs=4, timeout_ms=30_000,
                )
            self.assertFalse(output.exists())


def subprocess_sha(repository: Path) -> str:
    import subprocess

    return subprocess.run(
        ["git", "--no-pager", "-C", str(repository), "rev-parse", "HEAD"],
        check=True, capture_output=True, shell=False,
    ).stdout.decode().strip()


if __name__ == "__main__":
    unittest.main()
