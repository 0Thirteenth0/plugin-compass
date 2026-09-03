from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "plugins" / "compass-builder"
if str(BUILDER) not in sys.path:
    sys.path.insert(0, str(BUILDER))

from compass_builder.cli import main  # noqa: E402
from compass_builder.durable_artifacts import ArtifactJournal  # noqa: E402
from compass_builder.secure_files import (  # noqa: E402
    SecureFileError, read_no_follow, write_new_no_follow,
)
import compass_builder.secure_files as secure_files_module  # noqa: E402
import compass_builder.state as state_module  # noqa: E402
from compass_builder.models import canonical_json, validate_run_state_transition  # noqa: E402
from compass_builder.state import (  # noqa: E402
    StateError, StateStore, build_execution_bundle, resolve_repository,
    load_run_bundle, validate_execution_bundle,
)


DIGEST = "sha256:" + "d" * 64


def git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments], check=True, capture_output=True,
        text=True, encoding="utf-8", shell=False,
    )
    return result.stdout.strip()


def fixture(repo: Path) -> tuple[dict, dict, str, str, str]:
    repo.mkdir(parents=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Compass Builder Test")
    git(repo, "config", "user.email", "builder@example.invalid")
    (repo / ".gitignore").write_text("/.compass-builder/\n", encoding="utf-8")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    git(repo, "add", ".gitignore", "seed.txt")
    git(repo, "commit", "-m", "seed")
    initial = git(repo, "rev-parse", "HEAD")
    git(repo, "commit", "--allow-empty", "-m", "merge-one")
    second = git(repo, "rev-parse", "HEAD")
    git(repo, "commit", "--allow-empty", "-m", "merge-two")
    third = git(repo, "rev-parse", "HEAD")
    git(repo, "reset", "--hard", initial)
    run_id = "cb-test-0123456789abcdef"
    spec = {
        "schemaVersion": "compass-builder.run-spec.v1", "runId": run_id,
        "baseRef": "refs/heads/main", "baseSha": initial,
        "integrationBranch": "main", "integrationExpectedSha": initial,
        "mode": "auto", "exactModel": "gpt-5.6-sol",
        "effortPolicyVersion": "plugin-compass.effort-policy.v1",
        "hostConcurrencyCeiling": 2, "userConcurrencyCeiling": 2,
        "validationCommands": ["python -m unittest discover -s tests -v"],
        "stories": [],
    }
    for index, story_id in enumerate(("alpha", "beta")):
        spec["stories"].append({
            "id": story_id, "title": story_id.title(),
            "description": f"Implement {story_id} with focused evidence.",
            "dependsOn": [] if index == 0 else ["alpha"],
            "writeScopes": [f"src/{story_id}"],
            "acceptanceChecks": [f"{story_id} passes"],
            "validationCommands": [f"python -m unittest tests.test_{story_id} -v"],
            "independentReviewPath": None,
            "sharedState": {"mode": "none", "description": "No shared mutation."},
            "priority": index + 1, "completionState": "pending",
            "complexity": "medium", "ambiguity": "low", "risk": "low",
            "validationStrength": "decisive",
        })
    normalized_digest = "sha256:" + hashlib.sha256(canonical_json(spec, "run-spec")).hexdigest()
    plan = {
        "schemaVersion": "compass-builder.wave-plan.v1", "runId": run_id,
        "baseSha": initial, "integrationBranch": "main",
        "integrationExpectedSha": initial, "normalizedInputDigest": normalized_digest,
        "hostEvidenceDigest": "sha256:" + "a" * 64,
        "effortPolicyVersion": "plugin-compass.effort-policy.v1",
        "mode": "sequential", "reasons": ["Fixture selects sequential."],
        "concurrency": 1,
        "stories": [
            {"storyId": story_id, "branch": f"cb/{run_id}/{story_id}",
             "recommendedEffort": "medium", "handoffDigest": "sha256:" + char * 64}
            for story_id, char in (("alpha", "b"), ("beta", "c"))
        ],
        "waves": [
            {"waveIndex": 0, "storyIds": ["alpha"]},
            {"waveIndex": 1, "storyIds": ["beta"]},
        ],
    }
    return spec, plan, initial, second, third


def advance_first_wave(store: StateStore, initial: str, second: str) -> list[dict]:
    states = [store.initial_state()]
    dispatching = copy.deepcopy(states[-1])
    dispatching.update(previousState="planned", state="dispatching")
    dispatching["waves"][0]["branches"][0]["workerState"] = "running"
    validate_run_state_transition(states[-1], dispatching)
    states.append(dispatching)
    complete = copy.deepcopy(states[-1])
    complete.update(previousState="dispatching", state="wave-workers-complete")
    complete["waves"][0]["branches"][0]["workerState"] = "complete"
    validate_run_state_transition(states[-1], complete)
    states.append(complete)
    merging = copy.deepcopy(states[-1])
    merging.update(previousState="wave-workers-complete", state="wave-merging")
    entry = merging["waves"][0]["branches"][0]
    entry.update(verificationState="verified", integrationState="worker-verified")
    validate_run_state_transition(states[-1], merging)
    states.append(merging)
    integrated = copy.deepcopy(states[-1])
    integrated.update(previousState="wave-merging", state="wave-integrated-unverified")
    integrated["expectedIntegrationSha"] = second
    entry = integrated["waves"][0]["branches"][0]
    entry.update(integrationState="merged", mergeSha=second)
    validate_run_state_transition(states[-1], integrated)
    states.append(integrated)
    verified = copy.deepcopy(states[-1])
    verified.update(previousState="wave-integrated-unverified", state="wave-verified")
    verified["lastVerifiedIntegrationSha"] = second
    entry = verified["waves"][0]["branches"][0]
    entry.update(
        integrationState="integration-verified", controllerCheckDigest="sha256:" + "e" * 64,
        postCheckExpectedSha=second,
    )
    validate_run_state_transition(states[-1], verified)
    states.append(verified)
    return states


class BuilderStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.repo = self.base / "repo"
        self.spec, self.plan, self.initial, self.second, self.third = fixture(self.repo)
        self.host = json.loads(
            (ROOT / "tests" / "fixtures" / "compass_builder" / "host-capabilities.valid.json").read_text(encoding="utf-8")
        )
        self.plan["hostEvidenceDigest"] = "sha256:" + hashlib.sha256(
            canonical_json(self.host, "host-capabilities")
        ).hexdigest()
        self.store = StateStore(self.repo, self.spec, self.plan)

    def tearDown(self):
        self.temporary.cleanup()

    def test_atomic_state_binds_repository_and_registered_paths(self):
        state = self.store.initial_state()
        self.store.create(state)
        self.assertEqual(state, self.store.load())
        self.assertTrue(self.store.registry_path.is_file())
        self.assertEqual([], list(self.store.run_root.glob("state-*.tmp")))
        registrations = self.store.registrations()
        self.assertEqual(f"cb/{self.spec['runId']}/alpha", registrations[0]["branch"])
        self.assertEqual(
            self.store.worktree_root / "alpha",
            Path(registrations[0]["worktree"]),
        )

    def test_corrupt_state_registry_stale_head_and_cross_repository_replay_fail_closed(self):
        state = self.store.initial_state()
        self.store.create(state)
        git(self.repo, "reset", "--hard", self.second)
        with self.assertRaisesRegex(StateError, "stale integration HEAD"):
            self.store.load()
        git(self.repo, "reset", "--hard", self.initial)
        self.store.path.write_text("{bad", encoding="utf-8")
        with self.assertRaisesRegex(StateError, "malformed"):
            self.store.load()
        self.store._atomic_replace(state)
        registry = json.loads(self.store.registry_path.read_text(encoding="utf-8"))
        registry["repositoryIdentity"]["repositoryRoot"] = str(self.base / "other")
        self.store.registry_path.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaisesRegex(StateError, "does not bind"):
            self.store.load()

        other = self.base / "clone"
        subprocess.run(["git", "clone", "--no-hardlinks", str(self.repo), str(other)], check=True, capture_output=True)
        target = other / ".compass-builder"
        import shutil

        shutil.copytree(self.repo / ".compass-builder", target)
        replay = StateStore(other, self.spec, self.plan)
        with self.assertRaisesRegex(StateError, "does not bind"):
            replay.load()

    def test_every_run_phase_next_wave_and_partial_wave_resume_are_validated(self):
        states = advance_first_wave(self.store, self.initial, self.second)
        self.assertEqual(
            ["planned", "dispatching", "wave-workers-complete", "wave-merging",
             "wave-integrated-unverified", "wave-verified"],
            [state["state"] for state in states],
        )
        for state in states:
            projection = self.store.dry_run_projection(state)
            self.assertFalse(projection["workerExecutionAllowed"])
        git(self.repo, "reset", "--hard", self.second)
        next_wave = self.store.next_wave_state(states[-1])
        self.assertEqual("dispatching", next_wave["state"])
        self.assertEqual(1, next_wave["currentWaveIndex"])
        self.assertEqual("beta", next_wave["waves"][1]["branches"][0]["storyId"])
        self.assertEqual("pending", next_wave["waves"][1]["branches"][0]["workerState"])
        live_next_wave = self.store.next_wave_state(states[-1], start_workers=True)
        self.assertEqual("running", live_next_wave["waves"][1]["branches"][0]["workerState"])
        validate_run_state_transition(states[-1], live_next_wave)

        integrated = states[-2]
        blocked = copy.deepcopy(integrated)
        blocker = {
            "blockerId": "check-alpha", "blockedFromState": "wave-integrated-unverified",
            "phase": "post-merge-check", "storyId": "alpha",
            "reason": "Controller check failed.", "evidenceDigest": DIGEST,
            "resumeState": "wave-integrated-unverified",
        }
        blocked.update(previousState="wave-integrated-unverified", state="blocked")
        blocked["activeBlocker"] = blocker
        blocked["blockerHistory"] = [blocker]
        blocked["waves"][0]["branches"][0].update(
            integrationState="blocked", controllerCheckDigest=DIGEST
        )
        validate_run_state_transition(integrated, blocked)
        self.assertEqual("blocked", self.store.dry_run_projection(blocked)["state"])
        resumed = self.store.resume_state(blocked)
        self.assertEqual("wave-integrated-unverified", resumed["state"])
        self.assertIsNone(resumed["activeBlocker"])
        self.assertEqual([blocker], resumed["blockerHistory"])
        self.assertEqual("merged", resumed["waves"][0]["branches"][0]["integrationState"])
        self.assertEqual(self.second, resumed["waves"][0]["branches"][0]["mergeSha"])

        one_spec = copy.deepcopy(self.spec)
        one_spec["stories"] = one_spec["stories"][:1]
        one_plan = copy.deepcopy(self.plan)
        one_plan["stories"] = one_plan["stories"][:1]
        one_plan["waves"] = one_plan["waves"][:1]
        one_plan["normalizedInputDigest"] = "sha256:" + hashlib.sha256(
            canonical_json(one_spec, "run-spec")
        ).hexdigest()
        one_store = StateStore(self.repo, one_spec, one_plan)
        git(self.repo, "reset", "--hard", self.initial)
        one_verified = advance_first_wave(one_store, self.initial, self.second)[-1]
        completed = copy.deepcopy(one_verified)
        completed.update(previousState="wave-verified", state="completed")
        validate_run_state_transition(one_verified, completed)
        self.assertEqual("completed", one_store.dry_run_projection(completed)["state"])
        self.assertIsNone(one_store.dry_run_projection(completed)["firstIncompleteStoryId"])

    def test_multi_branch_partial_wave_resume_does_not_skip_or_remerge(self):
        spec = copy.deepcopy(self.spec)
        spec["stories"][1]["dependsOn"] = []
        plan = copy.deepcopy(self.plan)
        plan.update(mode="parallel", concurrency=2)
        plan["waves"] = [{"waveIndex": 0, "storyIds": ["alpha", "beta"]}]
        plan["normalizedInputDigest"] = "sha256:" + hashlib.sha256(
            canonical_json(spec, "run-spec")
        ).hexdigest()
        store = StateStore(self.repo, spec, plan)
        state = store.initial_state()
        dispatch = copy.deepcopy(state)
        dispatch.update(previousState="planned", state="dispatching")
        for entry in dispatch["waves"][0]["branches"]:
            entry["workerState"] = "running"
        validate_run_state_transition(state, dispatch)
        complete = copy.deepcopy(dispatch)
        complete.update(previousState="dispatching", state="wave-workers-complete")
        for entry in complete["waves"][0]["branches"]:
            entry["workerState"] = "complete"
        validate_run_state_transition(dispatch, complete)
        merging = copy.deepcopy(complete)
        merging.update(previousState="wave-workers-complete", state="wave-merging")
        for entry in merging["waves"][0]["branches"]:
            entry.update(verificationState="verified", integrationState="worker-verified")
        validate_run_state_transition(complete, merging)
        first_merge = copy.deepcopy(merging)
        first_merge.update(
            previousState="wave-merging", state="wave-integrated-unverified",
            expectedIntegrationSha=self.second,
        )
        first_merge["waves"][0]["branches"][0].update(
            integrationState="merged", mergeSha=self.second
        )
        validate_run_state_transition(merging, first_merge)
        second_ready = copy.deepcopy(first_merge)
        second_ready.update(
            previousState="wave-integrated-unverified", state="wave-merging",
            lastVerifiedIntegrationSha=self.second,
        )
        first, second = second_ready["waves"][0]["branches"]
        first.update(
            integrationState="integration-verified", controllerCheckDigest="sha256:" + "e" * 64,
            postCheckExpectedSha=self.second,
        )
        second["preMergeExpectedSha"] = self.second
        validate_run_state_transition(first_merge, second_ready)
        second_merge = copy.deepcopy(second_ready)
        second_merge.update(
            previousState="wave-merging", state="wave-integrated-unverified",
            expectedIntegrationSha=self.third,
        )
        second_merge["waves"][0]["branches"][1].update(
            integrationState="merged", mergeSha=self.third
        )
        validate_run_state_transition(second_ready, second_merge)
        blocked = copy.deepcopy(second_merge)
        blocker = {
            "blockerId": "check-beta", "blockedFromState": "wave-integrated-unverified",
            "phase": "post-merge-check", "storyId": "beta", "reason": "Check failed.",
            "evidenceDigest": DIGEST, "resumeState": "wave-integrated-unverified",
        }
        blocked.update(
            previousState="wave-integrated-unverified", state="blocked",
            activeBlocker=blocker, blockerHistory=[blocker],
        )
        blocked["waves"][0]["branches"][1].update(
            integrationState="blocked", controllerCheckDigest=DIGEST
        )
        validate_run_state_transition(second_merge, blocked)
        git(self.repo, "reset", "--hard", self.third)
        resumed = store.resume_state(blocked)
        first, second = resumed["waves"][0]["branches"]
        self.assertEqual("integration-verified", first["integrationState"])
        self.assertEqual("merged", second["integrationState"])
        self.assertEqual(self.third, second["mergeSha"])
        self.assertEqual("beta", store.dry_run_projection(resumed)["firstIncompleteStoryId"])

    def test_invalid_transition_and_nonexistent_or_unrelated_sha_fail_closed(self):
        initial = self.store.initial_state()
        self.store.create(initial)
        invalid = copy.deepcopy(initial)
        invalid.update(previousState="planned", state="completed")
        with self.assertRaises((StateError, ValueError)):
            self.store.write_transition(initial, invalid)
        bad = copy.deepcopy(initial)
        bad["baseSha"] = "f" * 40
        with self.assertRaisesRegex(StateError, "invalid|commit object"):
            self.store._validate_state(bad)
        unrelated_repo = self.base / "unrelated"
        _spec, _plan, _initial, _second, unrelated = fixture(unrelated_repo)
        bad = advance_first_wave(self.store, self.initial, unrelated)[-2]
        with self.assertRaisesRegex(StateError, "commit object"):
            self.store._validate_state(bad)

    def test_graft_cannot_make_invalid_recorded_ancestry_pass(self):
        git(self.repo, "checkout", "--orphan", "graft-rogue")
        git(self.repo, "commit", "--allow-empty", "-m", "unrelated graft target")
        rogue = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "main")
        git(self.repo, "reset", "--hard", self.initial)
        bad = advance_first_wave(self.store, self.initial, rogue)[-2]
        with self.assertRaisesRegex(StateError, "ancestry"):
            self.store._validate_state(bad)
        grafts = self.store.repository.common_git_dir / "info" / "grafts"
        grafts.parent.mkdir(parents=True, exist_ok=True)
        grafts.write_text(f"{rogue} {self.initial}\n", encoding="ascii")
        with self.assertRaisesRegex(StateError, "graft metadata"):
            self.store._validate_state(bad)

    def test_mutations_always_observe_git_head_and_validate_root_before_first_write(self):
        state = self.store.initial_state()
        git(self.repo, "reset", "--hard", self.second)
        with self.assertRaisesRegex(StateError, "stale integration HEAD"):
            self.store.create(state)
        self.assertFalse(self.store.registry_path.exists())
        with self.assertRaises(TypeError):
            self.store.create(state, observed_sha=self.initial)  # type: ignore[call-arg]

        git(self.repo, "reset", "--hard", self.initial)
        self.store.run_root.mkdir(parents=True)
        with patch("compass_builder.state._is_reparse", side_effect=lambda path: Path(path) == self.store.run_root):
            with self.assertRaisesRegex(StateError, "reparse|partial"):
                self.store.create(state)
        self.assertFalse(self.store.registry_path.exists())
        self.assertFalse(self.store.bundle_path.exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows junction probe")
    def test_raw_repository_junction_is_rejected_before_canonicalization(self):
        junction = self.base / "ancestor-junction"
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(self.repo.parent)],
            check=False, capture_output=True, shell=False,
        )
        if created.returncode != 0:
            self.skipTest("junction creation is unavailable on this host")
        try:
            with self.assertRaisesRegex(StateError, "reparse"):
                resolve_repository(junction / self.repo.name)
        finally:
            junction.rmdir()

    def test_cli_run_and_resume_explicit_dry_run_paths_never_start_workers(self):
        spec_path = self.base / "spec.json"
        plan_path = self.base / "plan.json"
        spec_path.write_text(json.dumps(self.spec), encoding="utf-8")
        bundle = build_execution_bundle(
            self.spec, self.plan, self.host, "2026-09-01T12:01:00Z", self.repo
        )
        plan_path.write_text(json.dumps(bundle), encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["run", "--repo", str(self.repo),
                         "--plan", str(plan_path), "--dry-run"])
        self.assertEqual(0, code, stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertEqual("run", payload["operation"])
        self.assertFalse(payload["workerExecutionAllowed"])
        self.assertTrue(payload["leaseValidated"])
        self.assertTrue(self.store.path.exists())
        self.assertTrue(self.store.bundle_path.exists())

        initial = self.store.load()
        blocker = {
            "blockerId": "preflight-stop", "blockedFromState": "planned",
            "phase": "pre-dispatch", "storyId": None, "reason": "Preflight stopped.",
            "evidenceDigest": DIGEST, "resumeState": "planned",
        }
        blocked = copy.deepcopy(initial)
        blocked.update(previousState="planned", state="blocked", activeBlocker=blocker,
                       blockerHistory=[blocker])
        self.store.write_transition(initial, blocked)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["resume", "--repo", str(self.repo),
                         "--run-id", self.spec["runId"], "--dry-run"])
        self.assertEqual(0, code, stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertEqual("planned", payload["state"])
        self.assertFalse(payload["workerExecutionAllowed"])

    def test_public_plan_output_is_directly_consumable_by_run(self):
        spec_path = self.base / "spec-for-plan.json"
        native_path = self.base / "native.json"
        plan_path = self.base / "public-plan.json"
        spec_path.write_text(json.dumps(self.spec), encoding="utf-8")
        native_path.write_text("{}", encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        report = {
            "hostCapabilities": self.host,
            "planningTimestamp": "2026-09-01T12:01:00Z",
        }
        with patch("compass_builder.cli.run_doctor", return_value=report), patch(
            "compass_builder.cli.resolve_plugin_compass", return_value=ROOT
        ), patch("compass_builder.cli.build_plan", return_value=self.plan), redirect_stdout(
            stdout
        ), redirect_stderr(stderr):
            code = main([
                "plan", "--repo", str(self.repo), "--spec", str(spec_path),
                "--native-capabilities", str(native_path), "--mode", "auto",
                "--plugin-compass-root", str(ROOT),
            ])
        self.assertEqual(0, code, stderr.getvalue())
        produced = json.loads(stdout.getvalue())
        self.assertEqual("compass-builder.plan-bundle.v1", produced["schemaVersion"])
        plan_path.write_text(stdout.getvalue(), encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["run", "--repo", str(self.repo), "--plan", str(plan_path), "--dry-run"])
        self.assertEqual(0, code, stderr.getvalue())
        self.assertEqual("run", json.loads(stdout.getvalue())["operation"])

    def test_execution_bundle_schema_and_python_validator_are_closed(self):
        bundle = build_execution_bundle(
            self.spec, self.plan, self.host, "2026-09-01T12:01:00Z", self.repo
        )
        schema_path = BUILDER / "schemas" / "plan-bundle.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        fields = {
            "schemaVersion", "runSpec", "wavePlan", "hostCapabilities",
            "planningTimestamp", "repositoryIdentity",
        }
        self.assertEqual("object", schema["type"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(fields, set(schema["required"]))
        self.assertEqual(fields, set(schema["properties"]))
        self.assertEqual(fields, set(bundle))
        self.assertEqual(
            "compass-builder.plan-bundle.v1",
            schema["properties"]["schemaVersion"]["const"],
        )
        planning_timestamp = schema["properties"]["planningTimestamp"]
        self.assertEqual(
            {"type": "string", "format": "date-time", "maxLength": 64},
            planning_timestamp,
        )
        identity_schema = schema["properties"]["repositoryIdentity"]
        identity_fields = {"repositoryRoot", "commonGitDir", "gitDir"}
        self.assertEqual("object", identity_schema["type"])
        self.assertFalse(identity_schema["additionalProperties"])
        self.assertEqual(identity_fields, set(identity_schema["required"]))
        self.assertEqual(identity_fields, set(identity_schema["properties"]))
        self.assertEqual(identity_fields, set(bundle["repositoryIdentity"]))

        def identity_rule_allows(rule: dict, value: str) -> bool:
            return (
                isinstance(value, str)
                and rule["minLength"] <= len(value) <= rule["maxLength"]
                and re.search(re.compile(rule["pattern"]), value) is not None
            )

        expected_identity_pattern = (
            r"^(?!\s)(?![\s\S]*\s$)[^\u0000-\u001f\u007f]+$"
        )
        for field, rule in identity_schema["properties"].items():
            with self.subTest(identity_field=field):
                self.assertEqual("string", rule["type"])
                self.assertEqual(1, rule["minLength"])
                self.assertEqual(1024, rule["maxLength"])
                self.assertEqual(expected_identity_pattern, rule["pattern"])
                self.assertIsNotNone(re.compile(rule["pattern"]))
                for accepted in (bundle["repositoryIdentity"][field], "x", "x" * 1024):
                    self.assertTrue(identity_rule_allows(rule, accepted))
                for rejected in ("", " leading", "trailing ", "bad\x00path", "x" * 1025):
                    self.assertFalse(identity_rule_allows(rule, rejected))

        references = {
            field: definition["$ref"]
            for field, definition in schema["properties"].items()
            if "$ref" in definition
        }
        expected_references = {
            "runSpec": "run-spec.schema.json",
            "wavePlan": "wave-plan.schema.json",
            "hostCapabilities": "host-capabilities.schema.json",
        }
        self.assertEqual(expected_references, references)
        for field, filename in expected_references.items():
            with self.subTest(field=field):
                self.assertEqual({"$ref": filename}, schema["properties"][field])
                target_path = schema_path.parent / filename
                self.assertTrue(target_path.is_file())
                target = json.loads(target_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    f"https://openai.local/compass-builder/{filename}",
                    target["$id"],
                )
        self.assertEqual(bundle, validate_execution_bundle(bundle, self.repo))
        bad = copy.deepcopy(bundle)
        bad["unexpected"] = True
        with self.assertRaisesRegex(StateError, "closed"):
            validate_execution_bundle(bad, self.repo)
        replay = copy.deepcopy(bundle)
        replay["repositoryIdentity"]["repositoryRoot"] = str(self.base / "elsewhere")
        with self.assertRaisesRegex(StateError, "does not match"):
            validate_execution_bundle(replay, self.repo)
        for identity in (
            {key: value for key, value in bundle["repositoryIdentity"].items() if key != "gitDir"},
            {**bundle["repositoryIdentity"], "unexpected": "value"},
        ):
            bad = copy.deepcopy(bundle)
            bad["repositoryIdentity"] = identity
            with self.subTest(identity_fields=sorted(identity)), self.assertRaisesRegex(
                StateError, "unsupported field set"
            ):
                validate_execution_bundle(bad)
        for invalid_timestamp in (123, "not-a-timestamp", "x" * 65):
            bad = copy.deepcopy(bundle)
            bad["planningTimestamp"] = invalid_timestamp
            with self.subTest(planning_timestamp=repr(invalid_timestamp)), self.assertRaisesRegex(
                StateError, "planningTimestamp|bindings"
            ):
                validate_execution_bundle(bad)
        for invalid in (123, " leading", "trailing ", "x" * 1025, "bad\x00path"):
            bad = copy.deepcopy(bundle)
            bad["repositoryIdentity"]["repositoryRoot"] = invalid
            with self.subTest(invalid=repr(invalid)), self.assertRaisesRegex(
                StateError, "repositoryIdentity"
            ):
                validate_execution_bundle(bad)

    def test_corrupt_canonical_plan_bundle_blocks_resume_without_repair(self):
        bundle = build_execution_bundle(
            self.spec, self.plan, self.host, "2026-09-01T12:01:00Z", self.repo
        )
        self.store.create(self.store.initial_state(), execution_bundle=bundle)
        corrupt = b'{"schemaVersion":"compass-builder.plan-bundle.v1"}\n'
        self.store.bundle_path.write_bytes(corrupt)
        with self.assertRaisesRegex(StateError, "closed"):
            load_run_bundle(self.repo, self.spec["runId"])
        self.assertEqual(corrupt, self.store.bundle_path.read_bytes())

    def test_create_rejects_mismatched_valid_bundle_before_any_artifact(self):
        alternate_spec = copy.deepcopy(self.spec)
        alternate_spec["stories"] = alternate_spec["stories"][:1]
        alternate_plan = copy.deepcopy(self.plan)
        alternate_plan["stories"] = alternate_plan["stories"][:1]
        alternate_plan["waves"] = alternate_plan["waves"][:1]
        alternate_plan["normalizedInputDigest"] = "sha256:" + hashlib.sha256(
            canonical_json(alternate_spec, "run-spec")
        ).hexdigest()
        bundle = build_execution_bundle(
            alternate_spec, alternate_plan, self.host,
            "2026-09-01T12:01:00Z", self.repo,
        )
        with self.assertRaisesRegex(StateError, "store|immutable"):
            self.store.create(self.store.initial_state(), execution_bundle=bundle)
        self.assertFalse(self.store.run_root.exists())

    def test_interrupted_create_is_never_published_or_silently_repaired(self):
        bundle = build_execution_bundle(
            self.spec, self.plan, self.host, "2026-09-01T12:01:00Z", self.repo
        )
        state = self.store.initial_state()
        with patch("compass_builder.state._write_new_file", side_effect=OSError("injected")):
            with self.assertRaisesRegex(StateError, "publication|injected"):
                self.store.create(state, execution_bundle=bundle)
        self.assertFalse(self.store.run_root.exists())
        with self.assertRaisesRegex(StateError, "partial|transaction"):
            self.store.create(state, execution_bundle=bundle)
        with self.assertRaises(StateError):
            self.store.load()

    def test_every_create_publication_boundary_is_non_repairing(self):
        for boundary in (1, 2, 3, 4, "rename"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory).resolve() / "repo"
                spec, plan, _initial, _second, _third = fixture(repo)
                host = copy.deepcopy(self.host)
                plan["hostEvidenceDigest"] = "sha256:" + hashlib.sha256(
                    canonical_json(host, "host-capabilities")
                ).hexdigest()
                store = StateStore(repo, spec, plan)
                bundle = build_execution_bundle(
                    spec, plan, host, "2026-09-01T12:01:00Z", repo
                )
                state = store.initial_state()
                calls = 0
                original_write = state_module._write_new_file

                def fail_boundary(path, payload, root, *, label):
                    nonlocal calls
                    calls += 1
                    if calls == boundary:
                        raise OSError(f"injected write {boundary}")
                    return original_write(path, payload, root, label=label)

                publication_patch = (
                    patch("compass_builder.state._write_new_file", side_effect=fail_boundary)
                    if isinstance(boundary, int)
                    else patch(
                        "compass_builder.state.os.rename",
                        side_effect=OSError("injected rename"),
                    )
                )
                with publication_patch, self.assertRaisesRegex(StateError, "publication"):
                    store.create(state, execution_bundle=bundle)
                self.assertFalse(store.run_root.exists())
                runs_root = repo / ".compass-builder" / "runs"
                self.assertTrue(any(
                    item.name.startswith(f".{spec['runId']}.create-")
                    for item in runs_root.iterdir()
                ))
                with self.assertRaisesRegex(StateError, "partial create transaction"):
                    store.create(state, execution_bundle=bundle)
                with self.assertRaises(StateError):
                    store.load()

    def test_repository_ancestor_and_durable_leaf_reparses_fail_closed(self):
        alias = self.base / "alias"
        nested = alias / "repo"
        alias.mkdir()
        nested.mkdir()
        with patch(
            "compass_builder.secure_files.is_reparse",
            side_effect=lambda path: Path(path) == alias,
        ):
            with self.assertRaisesRegex(StateError, "ancestor|reparse"):
                resolve_repository(nested)

        bundle = build_execution_bundle(
            self.spec, self.plan, self.host, "2026-09-01T12:01:00Z", self.repo
        )
        self.store.create(self.store.initial_state(), execution_bundle=bundle)
        previous = self.store.load()
        blocker = {
            "blockerId": "leaf-swap", "blockedFromState": "planned",
            "phase": "pre-dispatch", "storyId": None, "reason": "Injected swap.",
            "evidenceDigest": DIGEST, "resumeState": "planned",
        }
        blocked = copy.deepcopy(previous)
        blocked.update(
            previousState="planned", state="blocked", activeBlocker=blocker,
            blockerHistory=[blocker],
        )
        with patch(
            "compass_builder.secure_files.is_reparse",
            side_effect=lambda path: Path(path) == self.store.path,
        ):
            with self.assertRaisesRegex(StateError, "reparse"):
                self.store.load()
            with self.assertRaisesRegex(StateError, "reparse"):
                self.store.write_transition(previous, blocked)

    def test_auxiliary_journal_rejects_unknown_oversized_and_collection_floods(self):
        self.store.create(self.store.initial_state())
        journal = ArtifactJournal(self.store.run_root, self.store.control_root)
        with self.assertRaisesRegex(ValueError, "byte bound"):
            journal.record("failure-records", {"payload": "x" * 2_000_000})
        journal.record("failure-records", {"record": 1})
        directory = self.store.run_root / "failure-records"
        (directory / "unknown.tmp").write_text("hostile", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unknown entry"):
            journal.read("failure-records")
        (directory / "unknown.tmp").unlink()
        with patch("compass_builder.durable_artifacts.MAX_RECORDS", 1), self.assertRaisesRegex(
            ValueError, "collection"
        ):
            journal.record("failure-records", {"record": 2})
        with patch("compass_builder.durable_artifacts.MAX_AGGREGATE_BYTES", 1), self.assertRaisesRegex(
            ValueError, "collection"
        ):
            journal.read("failure-records")

        receipt = next(directory.glob("*.json"))
        real_stat_at = secure_files_module._stat_at

        def swapped_stat_at(path, parent_descriptor):
            result = real_stat_at(path, parent_descriptor)
            if Path(path) == receipt:
                return SimpleNamespace(st_dev=result.st_dev, st_ino=result.st_ino + 1)
            return result

        with patch("compass_builder.secure_files._stat_at", side_effect=swapped_stat_at), self.assertRaisesRegex(
            ValueError, "changed while"
        ):
            journal.read("failure-records")

    def test_auxiliary_journal_rejects_filename_digest_mismatch(self):
        self.store.create(self.store.initial_state())
        journal = ArtifactJournal(self.store.run_root, self.store.control_root)
        receipt = journal.record("failure-records", {"record": "authentic"})
        mismatched = receipt.with_name("0" * 64 + ".json")
        receipt.rename(mismatched)

        with self.assertRaisesRegex(ValueError, "digest"):
            journal.read("failure-records")

    @unittest.skipUnless(sys.platform == "win32", "Windows ancestor swap probe")
    def test_secure_file_guard_blocks_read_and_write_ancestor_junction_swap(self):
        controller = self.base / "controller"
        guarded_parent = controller / "runs" / "run-1" / "failure-records"
        guarded_parent.mkdir(parents=True)
        escaped_parent = self.base / "escaped"
        escaped_parent.mkdir()
        read_target = guarded_parent / "read.json"
        read_target.write_bytes(b"controller")
        (escaped_parent / read_target.name).write_bytes(b"escaped")
        write_target = guarded_parent / "write.json"
        original_open = state_module.os.open

        def exercise(operation):
            backup = guarded_parent.with_name(guarded_parent.name + "-held")

            def swap_inside_open(path, *args, **kwargs):
                guarded_parent.rename(backup)
                result = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(guarded_parent), str(escaped_parent)],
                    check=False, capture_output=True,
                )
                if result.returncode:
                    raise OSError("injected junction creation failed")
                return original_open(path, *args, **kwargs)

            try:
                with patch("compass_builder.secure_files.os.open", side_effect=swap_inside_open):
                    with self.assertRaises(SecureFileError):
                        operation()
            finally:
                if guarded_parent.exists() and backup.exists():
                    guarded_parent.rmdir()
                if backup.exists():
                    backup.rename(guarded_parent)

        exercise(lambda: read_no_follow(
            read_target, controller, label="ancestor-swap read", max_bytes=1024,
        ))
        exercise(lambda: write_new_no_follow(
            write_target, b"controller", controller, label="ancestor-swap write",
        ))
        self.assertFalse((escaped_parent / write_target.name).exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows journal junction probe")
    def test_auxiliary_journal_rejects_run_root_junction_escape(self):
        self.store.create(self.store.initial_state())
        escaped = self.base / "escaped-run"
        self.store.run_root.rename(escaped)
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(self.store.run_root), str(escaped)],
            check=False, capture_output=True,
        )
        if result.returncode:
            escaped.rename(self.store.run_root)
            self.skipTest("junction creation is unavailable on this host")
        try:
            with self.assertRaisesRegex(ValueError, "reparse|symlink"):
                ArtifactJournal(self.store.run_root, self.store.control_root)
        finally:
            self.store.run_root.rmdir()


if __name__ == "__main__":
    unittest.main()
