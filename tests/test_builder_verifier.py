from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
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

from compass_builder.launcher import REASONING_CONFIG_KEY, build_worker_prompt, prepare_launch  # noqa: E402
from compass_builder.cli import main  # noqa: E402
from compass_builder.git_environment import prepare_git_environment  # noqa: E402
from compass_builder.git_objects import (  # noqa: E402
    GitObjectError, MAX_RAW_COMMIT_BYTES, read_raw_commit,
)
from compass_builder.process_runner import BoundedProcessError  # noqa: E402
from compass_builder.models import canonical_json  # noqa: E402
from compass_builder.state import StateStore, build_execution_bundle  # noqa: E402
from compass_builder.verifier import VerificationError, verify_worker  # noqa: E402
from tests.helpers.git_repo_factory import GitRepoFactory  # noqa: E402


CHECK = 'python -c "raise SystemExit(0)"'


def make_context(
    base: Path, *, durable: bool = True, base_depth: int = 0
) -> dict[str, object]:
    factory = GitRepoFactory(base)
    initial = factory.init({"src/alpha/value.txt": "before\n"})
    for index in range(base_depth):
        factory.git("commit", "--allow-empty", "-m", f"deep base {index}")
        initial = factory.sha("HEAD")
    run_id = "cb-test-0123456789abcdef"
    host = json.loads((
        ROOT / "tests" / "fixtures" / "compass_builder" / "host-capabilities.valid.json"
    ).read_text(encoding="utf-8"))
    spec = {
        "schemaVersion": "compass-builder.run-spec.v1", "runId": run_id,
        "baseRef": "refs/heads/main", "baseSha": initial,
        "integrationBranch": "main", "integrationExpectedSha": initial,
        "mode": "auto", "exactModel": host["selectedModel"],
        "effortPolicyVersion": "plugin-compass.effort-policy.v1",
        "hostConcurrencyCeiling": 2, "userConcurrencyCeiling": 2,
        "validationCommands": [CHECK],
        "stories": [{
            "id": "alpha", "title": "Alpha", "description": "Implement alpha safely.",
            "dependsOn": [], "writeScopes": ["src/alpha"],
            "acceptanceChecks": ["Focused check passes."], "validationCommands": [CHECK],
            "independentReviewPath": None,
            "sharedState": {"mode": "none", "description": "No shared state."},
            "priority": 1, "completionState": "pending", "complexity": "low",
            "ambiguity": "low", "risk": "low", "validationStrength": "decisive",
        }],
    }
    normalized = "sha256:" + hashlib.sha256(canonical_json(spec, "run-spec")).hexdigest()
    host_digest = "sha256:" + hashlib.sha256(canonical_json(host, "host-capabilities")).hexdigest()
    plan = {
        "schemaVersion": "compass-builder.wave-plan.v1", "runId": run_id,
        "baseSha": initial, "integrationBranch": "main", "integrationExpectedSha": initial,
        "normalizedInputDigest": normalized, "hostEvidenceDigest": host_digest,
        "effortPolicyVersion": "plugin-compass.effort-policy.v1", "mode": "sequential",
        "reasons": ["Single deterministic fixture story."], "concurrency": 1,
        "stories": [{
            "storyId": "alpha", "branch": f"cb/{run_id}/alpha",
            "recommendedEffort": "low", "handoffDigest": "sha256:" + "d" * 64,
        }],
        "waves": [{"waveIndex": 0, "storyIds": ["alpha"]}],
    }
    registered = StateStore(
        factory.repo, spec, plan, factory.environment
    ).registered_worktree("alpha")
    worker = factory.worktree(
        f"cb/{run_id}/alpha", registered, initial,
    )
    head = factory.commit({"src/alpha/value.txt": "after\n"}, "alpha worker", cwd=worker)
    launch = prepare_launch(
        spec, plan, host, planning_timestamp="2026-09-01T12:01:00Z",
        story_id="alpha", worktree=worker,
        worker_schema=BUILDER / "schemas" / "worker-output.schema.json",
        reasoning_config_key=REASONING_CONFIG_KEY,
        reasoning_config_evidence_digest=host["reasoningConfig"]["evidenceDigest"],
        git_environment=factory.environment,
        worker_start_sha=spec["baseSha"],
    )
    receipt = {
        "schemaVersion": "compass-builder.worker-receipt.v1", "runId": run_id,
        "storyId": "alpha", "branch": f"cb/{run_id}/alpha", "worktree": str(worker),
        "exactModel": host["selectedModel"], "effort": "low", "baseSha": initial,
        "headSha": head, "commitSha": head,
        "changedFiles": [{
            "path": "src/alpha/value.txt", "sourcePath": None, "changeType": "modified",
        }],
        "checks": [{
            "name": "focused", "command": CHECK, "status": "passed",
            "evidenceDigest": "sha256:" + "e" * 64,
        }],
        "elapsedMs": 1, "status": "succeeded", "blocker": None,
    }
    context = {
        "factory": factory, "worker": worker, "base": initial, "head": head,
        "spec": spec, "plan": plan, "host": host,
        "launch": dict(launch.record), "receipt": receipt,
    }
    if durable:
        publish_context(context)
    return context


def literal_commit(
    factory: GitRepoFactory,
    tree: str,
    parent: str,
    *,
    include_author: bool = True,
    include_committer: bool = True,
    committer_before_author: bool = False,
    additional_headers: tuple[bytes, ...] = (),
    message: bytes = b"literal commit\n",
) -> str:
    lines = [f"tree {tree}".encode("ascii"), f"parent {parent}".encode("ascii")]
    author = b"author Literal Test <literal@example.invalid> 946684800 +0000"
    committer = b"committer Literal Test <literal@example.invalid> 946684800 +0000"
    if committer_before_author:
        if include_committer:
            lines.append(committer)
        if include_author:
            lines.append(author)
    else:
        if include_author:
            lines.append(author)
        if include_committer:
            lines.append(committer)
    lines.extend(additional_headers)
    return factory.literal_object("commit", b"\n".join(lines) + b"\n\n" + message)


def publish_context(context: dict[str, object]) -> None:
    factory, spec, plan = context["factory"], context["spec"], context["plan"]
    store = StateStore(factory.repo, spec, plan, factory.environment)
    planned = store.initial_state()
    store.create(planned)
    dispatching = copy.deepcopy(planned)
    dispatching.update(previousState="planned", state="dispatching")
    dispatching["waves"][0]["branches"][0]["workerState"] = "running"
    store.write_transition(planned, dispatching)
    complete = copy.deepcopy(dispatching)
    complete.update(previousState="dispatching", state="wave-workers-complete")
    complete["waves"][0]["branches"][0]["workerState"] = "complete"
    store.write_transition(dispatching, complete)
    context.update(store=store, state=complete)


class BuilderVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.context = make_context(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def verify(self, **overrides):
        values = dict(self.context)
        values.update(overrides)
        return verify_worker(
            values["factory"].repo, values["spec"], values["plan"],
            values["receipt"], values["launch"], values["factory"].environment,
        )

    def test_git_derived_single_commit_scope_and_fresh_check_pass(self):
        verified = self.verify()
        self.assertEqual(self.context["head"], verified.head_sha)
        self.assertEqual("passed", verified.check_evidence[0]["status"])
        self.assertTrue(verified.evidence_digest.startswith("sha256:"))

    def test_literal_commit_missing_required_identities_is_rejected(self):
        factory, worker = self.context["factory"], self.context["worker"]
        tree = factory.sha(f"{self.context['base']}^{{tree}}")
        malformed = literal_commit(
            factory, tree, self.context["base"],
            include_author=False, include_committer=False,
        )
        factory.git("reset", "--hard", malformed, cwd=worker)
        receipt = copy.deepcopy(self.context["receipt"])
        receipt.update(headSha=malformed, commitSha=malformed)
        with self.assertRaisesRegex(VerificationError, "author and committer"):
            self.verify(receipt=receipt)

    def test_committer_before_author_is_rejected_by_verifier(self):
        factory, worker = self.context["factory"], self.context["worker"]
        tree = factory.sha(f"{self.context['base']}^{{tree}}")
        malformed = literal_commit(
            factory, tree, self.context["base"], committer_before_author=True
        )
        factory.git("reset", "--hard", malformed, cwd=worker)
        receipt = copy.deepcopy(self.context["receipt"])
        receipt.update(headSha=malformed, commitSha=malformed)
        with self.assertRaisesRegex(VerificationError, "canonical author and committer"):
            self.verify(receipt=receipt)

    def test_raw_commit_parser_bounds_and_required_header_integrity(self):
        factory = self.context["factory"]
        tree = factory.sha(f"{self.context['base']}^{{tree}}")
        environment = factory.environment.environment

        bad_tree = literal_commit(factory, "g" * 40, self.context["base"])
        with self.assertRaisesRegex(GitObjectError, "tree header"):
            read_raw_commit(factory.repo, bad_tree, environment, expected_parent_count=1)

        blob = factory.literal_object("blob", b"not a tree\n")
        wrong_type = literal_commit(factory, blob, self.context["base"])
        with self.assertRaisesRegex(GitObjectError, "tree object"):
            read_raw_commit(factory.repo, wrong_type, environment, expected_parent_count=1)

        duplicate = literal_commit(
            factory, tree, self.context["base"],
            additional_headers=(
                b"author Duplicate <duplicate@example.invalid> 946684800 +0000",
            ),
        )
        with self.assertRaisesRegex(GitObjectError, "canonical order"):
            read_raw_commit(factory.repo, duplicate, environment, expected_parent_count=1)

        author = b"author Literal Test <literal@example.invalid> 946684800 +0000"
        committer = b"committer Literal Test <literal@example.invalid> 946684800 +0000"

        def store_headers(*headers: bytes) -> str:
            return factory.literal_object(
                "commit", b"\n".join(headers) + b"\n\nliteral grammar\n"
            )

        permutations = (
            store_headers(author, f"tree {tree}".encode(), f"parent {self.context['base']}".encode(), committer),
            store_headers(f"tree {tree}".encode(), author, f"parent {self.context['base']}".encode(), committer),
            store_headers(f"parent {self.context['base']}".encode(), f"tree {tree}".encode(), author, committer),
            store_headers(f"tree {tree}".encode(), f"parent {self.context['base']}".encode(), author, committer, f"tree {tree}".encode()),
            store_headers(f"tree {tree}".encode(), author, committer, f"parent {self.context['base']}".encode()),
            store_headers(
                f"tree {tree}".encode(), f"parent {self.context['base']}".encode(),
                author, committer, b"gpgsig placeholder", b" continuation", author,
            ),
        )
        for malformed in permutations:
            with self.subTest(malformed=malformed), self.assertRaisesRegex(
                GitObjectError, "first|canonical|required header"
            ):
                read_raw_commit(
                    factory.repo, malformed, environment, expected_parent_count=1
                )

        wrong_parent = literal_commit(factory, tree, blob)
        with self.assertRaisesRegex(GitObjectError, "parent is not a commit"):
            read_raw_commit(factory.repo, wrong_parent, environment, expected_parent_count=1)

        oversized = literal_commit(
            factory, tree, self.context["base"], message=b"x" * MAX_RAW_COMMIT_BYTES
        )
        with self.assertRaisesRegex(GitObjectError, "byte bound"):
            read_raw_commit(factory.repo, oversized, environment, expected_parent_count=1)

        optional = literal_commit(
            factory, tree, self.context["base"],
            additional_headers=(
                b"gpgsig placeholder", b" continuation",
                b"mergetag object payload", b" continuation",
            ),
        )
        self.assertEqual(
            (self.context["base"],),
            read_raw_commit(
                factory.repo, optional, environment, expected_parent_count=1
            ).parents,
        )

        with patch(
            "compass_builder.git_objects.run_bounded",
            side_effect=BoundedProcessError("process timed out after 10 seconds"),
        ), self.assertRaisesRegex(GitObjectError, "unavailable"):
            read_raw_commit(factory.repo, self.context["head"], environment)

    def test_fsck_valid_custom_optional_continuation_is_accepted(self):
        factory = self.context["factory"]
        tree = factory.sha(f"{self.context['base']}^{{tree}}")
        optional = literal_commit(
            factory, tree, self.context["base"],
            additional_headers=(b"x-custom first", b" continuation",),
        )
        factory.git("fsck", "--strict", "--no-dangling", optional)
        self.assertEqual(
            (self.context["base"],),
            read_raw_commit(
                factory.repo, optional, factory.environment.environment,
                expected_parent_count=1,
            ).parents,
        )

    def test_repo_factory_is_deterministic_and_neutralizes_ambient_git_surfaces(self):
        first = GitRepoFactory(Path(self.temporary.name) / "deterministic-one")
        second = GitRepoFactory(Path(self.temporary.name) / "deterministic-two")
        self.assertEqual(first.init(), second.init())
        environment = first.environment.environment
        self.assertEqual("1", environment["GIT_CONFIG_NOSYSTEM"])
        self.assertEqual("1", environment["GIT_ATTR_NOSYSTEM"])
        self.assertEqual("2000-01-01T00:00:00Z", environment["GIT_AUTHOR_DATE"])
        config = first.git("config", "--list", "--show-origin").stdout.decode("utf-8")
        self.assertIn("core.autocrlf=false", config)
        self.assertIn("core.filemode=false", config)
        self.assertIn("commit.gpgsign=false", config.casefold())

    def test_verify_worker_cli_uses_plan_receipt_launch_and_existing_git_environment(self):
        base = Path(self.temporary.name)
        bundle = build_execution_bundle(
            self.context["spec"], self.context["plan"], self.context["host"],
            "2026-09-01T12:01:00Z", self.context["factory"].repo,
        )
        paths = {"plan": base / "plan.json", "receipt": base / "receipt.json"}
        paths["plan"].write_text(json.dumps(bundle), encoding="utf-8")
        paths["receipt"].write_text(json.dumps(self.context["receipt"]), encoding="utf-8")
        store = self.context["store"]
        environment = prepare_git_environment(store.run_root / "git-environment")
        launch = prepare_launch(
            self.context["spec"], self.context["plan"], self.context["host"],
            planning_timestamp="2026-09-01T12:01:00Z", story_id="alpha",
            worktree=self.context["worker"],
            worker_schema=BUILDER / "schemas" / "worker-output.schema.json",
            reasoning_config_key=REASONING_CONFIG_KEY,
            reasoning_config_evidence_digest=self.context["host"]["reasoningConfig"]["evidenceDigest"],
            git_environment=environment,
            worker_start_sha=self.context["base"],
        )
        launch_root = store.run_root / "launch-records"
        launch_root.mkdir()
        (launch_root / "alpha.json").write_text(json.dumps(dict(launch.record)), encoding="utf-8")
        output = StringIO()
        with redirect_stdout(output):
            status = main([
                "verify-worker", "--repo", str(self.context["factory"].repo),
                "--plan", str(paths["plan"]), "--receipt", str(paths["receipt"]),
            ])
        self.assertEqual(0, status)
        self.assertEqual("compass-builder.worker-verification.v1", json.loads(output.getvalue())["schemaVersion"])
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            main([
                "verify-worker", "--repo", str(self.context["factory"].repo),
                "--plan", str(paths["plan"]), "--receipt", str(paths["receipt"]),
                "--launch-record", "attacker.json",
            ])

    def test_forged_stale_dirty_and_missing_check_receipts_fail_closed(self):
        receipt = copy.deepcopy(self.context["receipt"])
        receipt["changedFiles"][0]["path"] = "src/alpha/forged.txt"
        with self.assertRaisesRegex(VerificationError, "Git-derived"):
            self.verify(receipt=receipt)
        receipt = copy.deepcopy(self.context["receipt"])
        receipt["checks"] = []
        with self.assertRaisesRegex(VerificationError, "required worker check|invalid"):
            self.verify(receipt=receipt)
        Path(self.context["worker"], "untracked.txt").write_text("evidence\n", encoding="utf-8")
        with self.assertRaisesRegex(VerificationError, "dirty"):
            self.verify()

    def test_replace_ref_cannot_hide_real_out_of_scope_commit(self):
        context = make_context(Path(self.temporary.name) / "replace")
        factory, worker = context["factory"], context["worker"]
        benign = context["head"]
        factory.git("reset", "--hard", context["base"], cwd=worker)
        malicious = factory.commit({"outside.txt": "malicious\n"}, "malicious", cwd=worker)
        factory.git("replace", malicious, benign)
        receipt = copy.deepcopy(context["receipt"])
        receipt.update(headSha=malicious, commitSha=malicious)
        with self.assertRaisesRegex(VerificationError, "outside declared scope|Git-derived"):
            self.verify(
                factory=factory, spec=context["spec"], plan=context["plan"],
                receipt=receipt, launch=context["launch"],
            )

    def test_graft_cannot_hide_raw_merge_parents_from_single_commit_gate(self):
        context = make_context(Path(self.temporary.name) / "graft-merge")
        factory, worker = context["factory"], context["worker"]
        side = factory.worktree(
            "cb/test/graft-side", Path(self.temporary.name) / "graft-side", context["base"]
        )
        factory.commit({"src/alpha/side.txt": "side\n"}, "graft side", cwd=side)
        factory.git(
            "merge", "--no-ff", "--no-edit", "--no-gpg-sign", "cb/test/graft-side",
            cwd=worker,
        )
        merge_head = factory.sha("HEAD", cwd=worker)
        grafts = factory.repo / ".git" / "info" / "grafts"
        grafts.parent.mkdir(parents=True, exist_ok=True)
        grafts.write_text(f"{merge_head} {context['base']}\n", encoding="ascii")
        receipt = copy.deepcopy(context["receipt"])
        receipt.update(headSha=merge_head, commitSha=merge_head)
        with self.assertRaisesRegex(VerificationError, "non-merge"):
            self.verify(
                factory=factory, spec=context["spec"], plan=context["plan"],
                receipt=receipt, launch=context["launch"],
            )

    def test_no_remote_isolated_clone_is_accepted_but_any_remote_is_rejected(self):
        context = make_context(Path(self.temporary.name) / "foreign")
        factory, worker = context["factory"], Path(context["worker"])
        factory.git("worktree", "remove", str(worker))
        subprocess.run(
            ["git", "clone", "--no-hardlinks", str(factory.repo), str(worker)],
            check=True, capture_output=True, shell=False, env=dict(factory.environment.environment),
        )
        factory.git(
            "switch", "-c", context["receipt"]["branch"],
            "--track", f"origin/{context['receipt']['branch']}", cwd=worker,
        )
        factory.git("remote", "remove", "origin", cwd=worker)
        verified = self.verify(
            factory=factory, spec=context["spec"], plan=context["plan"],
            receipt=context["receipt"], launch=context["launch"],
        )
        self.assertEqual(context["head"], verified.head_sha)

        factory.git("remote", "add", "foreign", str(factory.repo), cwd=worker)
        with self.assertRaisesRegex(VerificationError, "remote"):
            self.verify(
                factory=factory, spec=context["spec"], plan=context["plan"],
                receipt=context["receipt"], launch=context["launch"],
            )

    def test_hostile_ambient_git_state_is_stripped_for_state_and_object_checks(self):
        context = make_context(Path(self.temporary.name) / "hostile")
        factory = context["factory"]
        hostile = dict(os.environ)
        hostile.update({
            "GIT_DIR": str(Path(self.temporary.name) / "attacker.git"),
            "GIT_WORK_TREE": str(Path(self.temporary.name) / "attacker-tree"),
            "GIT_INDEX_FILE": str(Path(self.temporary.name) / "attacker-index"),
            "GIT_OBJECT_DIRECTORY": str(Path(self.temporary.name) / "attacker-objects"),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(Path(self.temporary.name) / "alternate"),
            "GIT_CONFIG_GLOBAL": str(Path(self.temporary.name) / "attacker-config"),
            "GIT_CONFIG_SYSTEM": str(Path(self.temporary.name) / "attacker-system"),
            "GIT_ATTR_NOSYSTEM": "0", "GIT_CONFIG_COUNT": "99",
        })
        environment = prepare_git_environment(Path(self.temporary.name) / "hostile-isolation", base_environment=hostile)
        for key, value in (
            ("commit.gpgSign", "true"), ("core.autocrlf", "true"),
            ("core.hooksPath", str(Path(self.temporary.name) / "hostile-hooks")),
            ("core.attributesFile", str(Path(self.temporary.name) / "hostile-attributes")),
            ("alias.diff", "!exit 99"),
        ):
            factory.git("config", "--local", key, value)
        launch = dict(context["launch"])
        launch["gitEnvironmentDigest"] = environment.digest
        verified = verify_worker(
            factory.repo, context["spec"], context["plan"], context["receipt"],
            launch, environment,
        )
        self.assertEqual(context["head"], verified.head_sha)
        self.assertEqual("1", environment.environment["GIT_NO_REPLACE_OBJECTS"])
        self.assertEqual(hostile.get("HOME"), environment.environment.get("HOME"))

    def test_multi_commit_and_wrong_sha_bypasses_fail(self):
        factory = self.context["factory"]
        worker = self.context["worker"]
        new_head = factory.commit({"src/alpha/second.txt": "second\n"}, "second", cwd=worker)
        receipt = copy.deepcopy(self.context["receipt"])
        receipt.update(headSha=new_head, commitSha=new_head)
        receipt["changedFiles"].append({
            "path": "src/alpha/second.txt", "sourcePath": None, "changeType": "added",
        })
        with self.assertRaisesRegex(VerificationError, "exactly one"):
            self.verify(receipt=receipt)

    def test_rename_compares_both_sides_and_ancestor_scope_is_boundary_aware(self):
        context = make_context(Path(self.temporary.name) / "rename")
        factory, worker = context["factory"], context["worker"]
        factory.git("reset", "--hard", context["base"], cwd=worker)
        factory.git("mv", "src/alpha/value.txt", "src/alphabeta.txt", cwd=worker)
        factory.git("commit", "-m", "escape by rename", cwd=worker)
        head = factory.sha("HEAD", cwd=worker)
        receipt = copy.deepcopy(context["receipt"])
        receipt.update(headSha=head, commitSha=head)
        receipt["changedFiles"] = [{
            "path": "src/alphabeta.txt", "sourcePath": "src/alpha/value.txt",
            "changeType": "renamed",
        }]
        with self.assertRaisesRegex(VerificationError, "outside declared scope"):
            self.verify(
                factory=factory, worker=worker, spec=context["spec"], plan=context["plan"],
                receipt=receipt, launch=context["launch"],
            )

    def test_changed_symlink_mode_is_rejected_from_git_objects(self):
        context = make_context(Path(self.temporary.name) / "symlink")
        factory, worker = context["factory"], context["worker"]
        factory.git("reset", "--hard", context["base"], cwd=worker)
        blob = factory.git("hash-object", "-w", "--stdin", cwd=worker, check=True,).stdout
        # hash-object above receives empty stdin and creates a deterministic link target blob.
        oid = blob.decode("ascii").strip()
        factory.git("update-index", "--add", "--cacheinfo", f"120000,{oid},src/alpha/link", cwd=worker)
        factory.git("commit", "-m", "symlink object", cwd=worker)
        factory.git("reset", "--hard", "HEAD", cwd=worker)
        head = factory.sha("HEAD", cwd=worker)
        receipt = copy.deepcopy(context["receipt"])
        receipt.update(headSha=head, commitSha=head)
        receipt["changedFiles"] = [{
            "path": "src/alpha/link", "sourcePath": None, "changeType": "added",
        }]
        with self.assertRaisesRegex(VerificationError, "symlink"):
            self.verify(
                factory=factory, spec=context["spec"], plan=context["plan"],
                receipt=receipt, launch=context["launch"],
            )

    def test_delete_is_verified_from_base_tree_and_submodule_mode_is_rejected(self):
        deleted = make_context(Path(self.temporary.name) / "deleted")
        factory, worker = deleted["factory"], deleted["worker"]
        factory.git("reset", "--hard", deleted["base"], cwd=worker)
        factory.git("rm", "src/alpha/value.txt", cwd=worker)
        factory.git("commit", "-m", "delete alpha", cwd=worker)
        head = factory.sha("HEAD", cwd=worker)
        receipt = copy.deepcopy(deleted["receipt"])
        receipt.update(headSha=head, commitSha=head)
        receipt["changedFiles"] = [{
            "path": "src/alpha/value.txt", "sourcePath": None, "changeType": "deleted",
        }]
        verified = self.verify(
            factory=factory, spec=deleted["spec"], plan=deleted["plan"],
            receipt=receipt, launch=deleted["launch"],
        )
        self.assertEqual("deleted", verified.changed_files[0]["changeType"])

        submodule = make_context(Path(self.temporary.name) / "submodule")
        factory, worker = submodule["factory"], submodule["worker"]
        factory.git("reset", "--hard", submodule["base"], cwd=worker)
        factory.git(
            "update-index", "--add", "--cacheinfo",
            f"160000,{submodule['base']},src/alpha/dependency", cwd=worker,
        )
        factory.git("commit", "-m", "gitlink object", cwd=worker)
        factory.git("reset", "--hard", "HEAD", cwd=worker)
        head = factory.sha("HEAD", cwd=worker)
        receipt = copy.deepcopy(submodule["receipt"])
        receipt.update(headSha=head, commitSha=head)
        receipt["changedFiles"] = [{
            "path": "src/alpha/dependency", "sourcePath": None, "changeType": "added",
        }]
        with self.assertRaisesRegex(VerificationError, "submodule"):
            self.verify(
                factory=factory, spec=submodule["spec"], plan=submodule["plan"],
                receipt=receipt, launch=submodule["launch"],
            )

    def test_merge_commit_and_missing_independent_check_bypasses_fail(self):
        context = make_context(Path(self.temporary.name) / "merge")
        factory, worker = context["factory"], context["worker"]
        side = factory.worktree("cb/test/side", Path(self.temporary.name) / "side", context["base"])
        factory.commit({"src/alpha/side.txt": "side\n"}, "side", cwd=side)
        factory.git("merge", "--no-ff", "--no-edit", "--no-gpg-sign", "cb/test/side", cwd=worker)
        merge_head = factory.sha("HEAD", cwd=worker)
        receipt = copy.deepcopy(context["receipt"])
        receipt.update(headSha=merge_head, commitSha=merge_head)
        with self.assertRaisesRegex(VerificationError, "non-merge"):
            self.verify(
                factory=factory, spec=context["spec"], plan=context["plan"],
                receipt=receipt, launch=context["launch"],
            )

        context = make_context(Path(self.temporary.name) / "checks", durable=False)
        commands = ['python -c "raise SystemExit(1)"', 'python -c "raise SystemExit(0)"']
        context["spec"]["stories"][0]["validationCommands"] = commands
        context["plan"]["normalizedInputDigest"] = "sha256:" + hashlib.sha256(
            canonical_json(context["spec"], "run-spec")
        ).hexdigest()
        context["receipt"]["checks"] = [
            {"name": str(index), "command": command, "status": "passed", "evidenceDigest": "sha256:" + str(index + 1) * 64}
            for index, command in enumerate(commands)
        ]
        context["launch"]["promptDigest"] = "sha256:" + hashlib.sha256(
            build_worker_prompt(context["spec"], story_id="alpha").encode("utf-8")
        ).hexdigest()
        publish_context(context)
        observed: list[str] = []

        def runner(argv, cwd, environment):
            observed.append(" ".join(argv))
            return __import__("subprocess").CompletedProcess(list(argv), 1 if len(observed) == 1 else 0, "", "")

        with self.assertRaisesRegex(VerificationError, "independently"):
            verify_worker(
                context["factory"].repo, context["spec"], context["plan"],
                context["receipt"], context["launch"], context["factory"].environment,
                command_runner=runner,
            )
        self.assertEqual(2, len(observed))


if __name__ == "__main__":
    unittest.main()
