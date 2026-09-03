from __future__ import annotations

import json
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

from compass_builder.controller import (  # noqa: E402
    ControllerError, codex_worker_transport,
)
from compass_builder.launcher import PreparedLaunch  # noqa: E402
from compass_builder.process_runner import run_bounded as actual_run_bounded  # noqa: E402
from tests.helpers.git_repo_factory import GitRepoFactory  # noqa: E402


class ControllerWorkerTransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.factory = GitRepoFactory(Path(self.temporary.name))
        self.base_sha = self.factory.init({"src/__init__.py": ""})
        self.worktree = self.factory.worktree(
            "cb/controller-test/story",
            Path(self.temporary.name) / "worker",
            self.base_sha,
        )
        self.launch = PreparedLaunch(
            argv=("codex", "exec"),
            stdin="bounded worker prompt",
            environment=self.factory.environment.environment,
            record={
                "runId": "cb-controller-test-0123456789abcdef",
                "storyId": "story",
                "branch": "cb/controller-test/story",
                "worktree": str(self.worktree),
                "exactModel": "gpt-5.6-sol",
                "effort": "low",
                "workerStartSha": self.base_sha,
            },
        )
        self.story = {
            "writeScopes": ["src/story.py"],
            "validationCommands": ['python -c "raise SystemExit(0)"'],
        }
        self.output = (json.dumps({
            "schemaVersion": "compass-builder.worker-output.v1",
            "status": "succeeded",
            "summary": "Implemented the story and ran its check.",
            "acceptanceChecks": [{
                "check": "focused",
                "status": "passed",
                "evidence": "Focused check passed.",
            }],
            "blocker": None,
        }) + "\n").encode("utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def transport(self, worker_action):
        def runner(argv, **kwargs):
            if argv[0] == "codex":
                worker_action()
                return subprocess.CompletedProcess(list(argv), 0, self.output, b"")
            return actual_run_bounded(argv, **kwargs)

        with patch("compass_builder.controller.run_bounded", side_effect=runner):
            return codex_worker_transport(
                self.launch, self.story, 30_000, lambda _kind, _details: None
            )

    def test_controller_creates_the_only_story_commit_after_worker_edits(self):
        def worker_edits():
            (self.worktree / "src" / "story.py").write_text(
                "VALUE = 1\n", encoding="utf-8", newline="\n"
            )

        receipt = self.transport(worker_edits)
        self.assertEqual("succeeded", receipt["status"])
        self.assertNotEqual(self.base_sha, receipt["headSha"])
        self.assertEqual(receipt["headSha"], receipt["commitSha"])
        self.assertEqual([{
            "path": "src/story.py", "sourcePath": None, "changeType": "added",
        }], receipt["changedFiles"])
        self.assertEqual(
            "1",
            self.factory.git(
                "rev-list", "--count", f"{self.base_sha}..{receipt['headSha']}",
                cwd=self.worktree,
            ).stdout.decode().strip(),
        )
        self.assertEqual(b"", self.factory.git(
            "status", "--porcelain=v1", "-z", "--untracked-files=all",
            cwd=self.worktree,
        ).stdout)

    def test_worker_created_commit_is_rejected_before_controller_commit(self):
        def worker_commits():
            self.factory.commit(
                {"src/story.py": "VALUE = 1\n"}, "worker-owned commit",
                cwd=self.worktree,
            )

        with self.assertRaisesRegex(ControllerError, "worker changed HEAD"):
            self.transport(worker_commits)

    def test_worker_mutated_index_is_rejected_before_controller_commit(self):
        def worker_stages():
            (self.worktree / "src" / "story.py").write_text(
                "VALUE = 1\n", encoding="utf-8", newline="\n"
            )
            self.factory.git("add", "--all", cwd=self.worktree)

        with self.assertRaisesRegex(ControllerError, "worker changed the Git index"):
            self.transport(worker_stages)

    def test_out_of_scope_edit_is_rejected_before_controller_commit(self):
        def worker_edits_outside_scope():
            (self.worktree / "outside.txt").write_text(
                "not authorized\n", encoding="utf-8", newline="\n"
            )

        with self.assertRaisesRegex(ControllerError, "outside declared scope"):
            self.transport(worker_edits_outside_scope)
        self.assertEqual(self.base_sha, self.factory.sha("HEAD", cwd=self.worktree))


if __name__ == "__main__":
    unittest.main()
