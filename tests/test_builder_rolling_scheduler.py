from __future__ import annotations

import ast
import copy
import hashlib
import importlib
import json
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "compass-builder"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "compass_builder" / "rolling"
SCHEDULER_SOURCE = PLUGIN_ROOT / "compass_builder" / "rolling_scheduler.py"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from compass_builder import models as rolling_models  # noqa: E402
from compass_builder._validation import canonical_data, canonical_digest  # noqa: E402


DECISION_EVIDENCE_VERSION = "compass-builder.scheduler-decision-evidence.v1"

try:
    scheduler = importlib.import_module("compass_builder.rolling_scheduler")
except ModuleNotFoundError as exc:
    if exc.name != "compass_builder.rolling_scheduler":
        raise
    scheduler = None


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.valid.json").read_text(encoding="utf-8"))


def _story_digest(story_id: str, label: str) -> str:
    return canonical_digest({"label": label, "storyId": story_id})


def _story_sha(story_id: str) -> str:
    return hashlib.sha256(story_id.encode("utf-8")).hexdigest()[:40]


def make_bundle(
    stories: list[dict],
    *,
    execution_mode: str = "parallel",
    concurrency: int | None = None,
    host_valid_until: str = "2026-09-01T12:05:00Z",
) -> dict:
    bundle = fixture("execution-bundle-v2")
    spec = bundle["runSpec"]
    plan = bundle["pipelinePlan"]
    host = bundle["hostCapabilities"]
    if concurrency is None:
        concurrency = 1 if execution_mode == "sequential" else 2
    spec["executionMode"] = execution_mode
    plan["executionMode"] = execution_mode
    plan["concurrency"] = concurrency
    host["validUntil"] = host_valid_until

    spec_stories = []
    plan_stories = []
    for index, definition in enumerate(stories):
        story_id = definition["id"]
        dependencies = list(definition.get("dependsOn", []))
        scopes = list(definition.get("writeScopes", [f"src/{story_id}"]))
        gates = list(definition.get("gates", [f"gate-{story_id}"]))
        effort = definition.get("effort", "medium")
        spec_stories.append(
            {
                "id": story_id,
                "title": f"Story {story_id}",
                "description": f"Deterministic scheduler story {story_id}.",
                "dependsOn": dependencies,
                "writeScopes": scopes,
                "acceptanceChecks": [f"{story_id} is accepted"],
                "validationCommands": [f"check-{story_id}"],
                "independentReviewPath": None,
                "sharedState": {
                    "mode": definition.get("sharedMode", "none"),
                    "description": f"Shared-state policy for {story_id}.",
                },
                "priority": definition.get("priority", 10),
                "completionState": "pending",
                "complexity": "medium",
                "ambiguity": "low",
                "risk": "medium",
                "validationStrength": "decisive",
                "requiredOutcomeGateIds": gates,
            }
        )
        plan_stories.append(
            {
                "storyId": story_id,
                "specificationOrder": index,
                "integrationOrdinal": index + 1,
                "dependsOn": dependencies,
                "branch": f"cb/{spec['runId']}/{story_id}",
                "recommendedEffort": effort,
                "handoffDigest": _story_digest(story_id, "handoff"),
                "writeScopes": scopes,
                "requiredOutcomeGateIds": gates,
            }
        )
    spec["stories"] = spec_stories
    plan["stories"] = plan_stories
    plan["initialReadyStoryIds"] = [
        story["storyId"] for story in plan_stories if not story["dependsOn"]
    ]
    plan["normalizedInputDigest"] = canonical_digest(spec)
    plan["hostEvidenceDigest"] = canonical_digest(host)
    return bundle


def make_state(bundle: dict) -> dict:
    plan = bundle["pipelinePlan"]
    state = fixture("pipeline-state")
    state.update(
        {
            "runId": plan["runId"],
            "planDigest": canonical_digest(plan),
            "baseSha": plan["baseSha"],
            "integrationBranch": plan["integrationBranch"],
            "initialIntegrationSha": plan["integrationExpectedSha"],
            "currentIntegrationSha": plan["integrationExpectedSha"],
            "lastVerifiedIntegrationSha": plan["integrationExpectedSha"],
            "previousState": None,
            "state": "planned",
            "lastEventSequence": 0,
            "lastEventDigest": None,
            "activeOwners": [],
            "integrationQueue": [],
            "activeBlocker": None,
            "blockerHistory": [],
            "stories": [],
        }
    )
    for planned in plan["stories"]:
        state["stories"].append(
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
        )
    return state


def set_lifecycle(state: dict, bundle: dict, story_id: str, lifecycle: str) -> None:
    story = next(item for item in state["stories"] if item["storyId"] == story_id)
    planned = next(
        item for item in bundle["pipelinePlan"]["stories"] if item["storyId"] == story_id
    )
    story.update(
        {
            "lifecycle": lifecycle,
            "blockedFromLifecycle": None,
            "attempt": 0,
            "workerStartSha": None,
            "registeredCloneDigest": None,
            "workerReceiptDigest": None,
            "verificationEvidenceDigest": None,
            "importEvidenceDigest": None,
            "mergeIntentDigest": None,
            "integrationSha": None,
            "postCheckEvidenceDigest": None,
            "gateEvidenceDigests": [],
        }
    )
    if lifecycle == "never-launched":
        return
    story.update(
        {
            "attempt": 1,
            "workerStartSha": state["lastVerifiedIntegrationSha"],
            "registeredCloneDigest": _story_digest(story_id, "clone"),
        }
    )
    if lifecycle in {
        "worker-complete-unverified",
        "verified-unimported",
        "imported-awaiting-integration",
        "merged-awaiting-post-check",
        "integration-verified",
    }:
        story["workerReceiptDigest"] = _story_digest(story_id, "worker-receipt")
    if lifecycle in {
        "verified-unimported",
        "imported-awaiting-integration",
        "merged-awaiting-post-check",
        "integration-verified",
    }:
        story["verificationEvidenceDigest"] = _story_digest(story_id, "verification")
    if lifecycle in {
        "imported-awaiting-integration",
        "merged-awaiting-post-check",
        "integration-verified",
    }:
        story["importEvidenceDigest"] = _story_digest(story_id, "import")
        story["gateEvidenceDigests"] = [
            _story_digest(story_id, f"durable-gate:{gate_id}")
            for gate_id in planned["requiredOutcomeGateIds"]
        ]
    if lifecycle in {"merged-awaiting-post-check", "integration-verified"}:
        story["mergeIntentDigest"] = _story_digest(story_id, "merge-intent")
        story["integrationSha"] = _story_sha(story_id)
    if lifecycle == "integration-verified":
        story["postCheckEvidenceDigest"] = _story_digest(story_id, "post-check")


def finalize_state(state: dict, bundle: dict, *, completed: bool = False) -> None:
    plan_by_id = {
        item["storyId"]: item for item in bundle["pipelinePlan"]["stories"]
    }
    state["activeOwners"] = [
        {
            "storyId": story["storyId"],
            "ownerId": f"worker-{story['storyId']}-1",
            "writeScopes": copy.deepcopy(plan_by_id[story["storyId"]]["writeScopes"]),
            "workerStartSha": story["workerStartSha"],
            "registeredCloneDigest": story["registeredCloneDigest"],
        }
        for story in state["stories"]
        if story["lifecycle"] in {"running", "process-unknown"}
    ]
    state["integrationQueue"] = [
        story["storyId"]
        for story in state["stories"]
        if story["lifecycle"] == "imported-awaiting-integration"
    ]
    verified_prefix = []
    for story in state["stories"]:
        if (
            story["lifecycle"] == "integration-verified"
            and len(verified_prefix) == story["integrationOrdinal"] - 1
        ):
            verified_prefix.append(story)
    state["lastVerifiedIntegrationSha"] = (
        verified_prefix[-1]["integrationSha"]
        if verified_prefix
        else state["initialIntegrationSha"]
    )
    merged = [
        story
        for story in state["stories"]
        if story["lifecycle"] == "merged-awaiting-post-check"
    ]
    state["currentIntegrationSha"] = (
        merged[0]["integrationSha"] if merged else state["lastVerifiedIntegrationSha"]
    )
    state["previousState"] = "running" if completed else "planned"
    state["state"] = "completed" if completed else "running"


def make_blocked_state(bundle: dict) -> dict:
    state = make_state(bundle)
    story = state["stories"][0]
    story["lifecycle"] = "blocked"
    story["blockedFromLifecycle"] = "never-launched"
    blocker = {
        "blockerId": "scheduler-blocker",
        "eventId": "scheduler-block-event",
        "storyId": story["storyId"],
        "phase": "dispatch",
        "reason": "Durable dispatch blocker.",
        "evidenceDigest": _story_digest(story["storyId"], "blocker"),
        "resumeState": "planned",
    }
    state.update(
        {
            "previousState": "planned",
            "state": "blocked",
            "activeBlocker": blocker,
            "blockerHistory": [copy.deepcopy(blocker)],
        }
    )
    return state


def make_evidence(
    bundle: dict,
    *,
    statuses: dict[str, str] | None = None,
    approvals: dict[str, list[str]] | None = None,
    observed_at: str = "2026-09-01T12:02:00Z",
    valid_until: str = "2026-09-01T12:04:00Z",
) -> dict:
    statuses = statuses or {}
    approvals = approvals or {}
    plan = bundle["pipelinePlan"]
    readiness = []
    for planned in plan["stories"]:
        story_id = planned["storyId"]
        status = statuses.get(story_id, "actionable")
        default_approvals = (
            [
                _story_digest(story_id, f"approval:{gate_id}")
                for gate_id in planned["requiredOutcomeGateIds"]
            ]
            if status == "actionable"
            else []
        )
        readiness.append(
            {
                "storyId": story_id,
                "status": status,
                "requiredOutcomeGateIds": copy.deepcopy(
                    planned["requiredOutcomeGateIds"]
                ),
                "approvalDigests": copy.deepcopy(
                    approvals.get(story_id, default_approvals)
                ),
            }
        )
    return {
        "schemaVersion": DECISION_EVIDENCE_VERSION,
        "planDigest": canonical_digest(plan),
        "hostEvidenceDigest": plan["hostEvidenceDigest"],
        "gatePolicyDigest": plan["gatePolicyDigest"],
        "observedAt": observed_at,
        "validUntil": valid_until,
        "gateReadiness": readiness,
    }


class BuilderRollingSchedulerTests(unittest.TestCase):
    def require_scheduler(self):
        self.assertIsNotNone(
            scheduler,
            "missing public compass_builder.rolling_scheduler scheduler API",
        )
        return scheduler

    def assert_scheduler_error(self, pattern: str, function) -> None:
        module = self.require_scheduler()
        with self.assertRaisesRegex(module.SchedulerDecisionError, pattern):
            function()

    def test_public_scheduler_api_exists(self):
        module = self.require_scheduler()
        self.assertEqual(DECISION_EVIDENCE_VERSION, module.DECISION_EVIDENCE_VERSION)
        self.assertTrue(callable(module.decide))
        self.assertTrue(issubclass(module.SchedulerDecisionError, ValueError))

    def test_valid_bundle_state_and_evidence_return_closed_dispatch_proposal(self):
        module = self.require_scheduler()
        bundle = make_bundle(
            [{"id": "alpha", "priority": 3, "effort": "high", "gates": ["gate-alpha"]}]
        )
        state = make_state(bundle)
        evidence = make_evidence(bundle)

        decision = module.decide(bundle, state, evidence)

        self.assertEqual("dispatch", decision["action"])
        self.assertEqual("eligibility-proposal-only", decision["proposalType"])
        self.assertEqual(
            {
                "storyId": "alpha",
                "specificationOrder": 0,
                "priority": 3,
                "workerStartSha": state["lastVerifiedIntegrationSha"],
                "exactModel": bundle["runSpec"]["exactModel"],
                "recommendedEffort": "high",
                "handoffDigest": bundle["pipelinePlan"]["stories"][0]["handoffDigest"],
                "writeScopes": ["src/alpha"],
                "requiredOutcomeGateIds": ["gate-alpha"],
                "gateApprovalDigests": evidence["gateReadiness"][0]["approvalDigests"],
                "prerequisites": [],
            },
            decision["candidates"][0],
        )
        self.assertEqual(
            {
                "decisionEvidenceDigest",
                "executionBundleDigest",
                "gatePolicyDigest",
                "hostEvidenceDigest",
                "pipelineStateDigest",
                "planDigest",
                "schedulingPolicyDigest",
            },
            set(decision["bindings"]),
        )
        self.assertNotIn("dispatchId", canonical_data(decision).decode("utf-8"))
        self.assertNotIn("registeredClone", canonical_data(decision).decode("utf-8"))

    def test_dag_roots_dispatch_and_join_waits_for_every_integrated_dependency(self):
        module = self.require_scheduler()
        bundle = make_bundle(
            [
                {"id": "alpha", "priority": 10},
                {"id": "beta", "priority": 5},
                {"id": "join", "dependsOn": ["alpha", "beta"], "priority": 0},
            ]
        )
        state = make_state(bundle)
        evidence = make_evidence(bundle)
        self.assertEqual(
            ["beta", "alpha"],
            [item["storyId"] for item in module.decide(bundle, state, evidence)["candidates"]],
        )

        set_lifecycle(state, bundle, "alpha", "integration-verified")
        finalize_state(state, bundle)
        self.assertNotEqual(
            "join",
            module.decide(bundle, state, evidence).get("candidates", [{}])[0].get("storyId"),
        )

        set_lifecycle(state, bundle, "beta", "integration-verified")
        finalize_state(state, bundle)
        decision = module.decide(bundle, state, evidence)
        self.assertEqual(["join"], [item["storyId"] for item in decision["candidates"]])

    def test_lower_numeric_priority_then_specification_order_controls_dispatch(self):
        module = self.require_scheduler()
        bundle = make_bundle(
            [
                {"id": "alpha", "priority": 8},
                {"id": "beta", "priority": 3},
                {"id": "gamma", "priority": 3},
                {"id": "delta", "priority": 1},
            ]
        )
        decision = module.decide(bundle, make_state(bundle), make_evidence(bundle))
        self.assertEqual(
            ["delta", "beta"],
            [item["storyId"] for item in decision["candidates"]],
        )

    def test_capacity_subtracts_active_ownership_and_sequential_width_is_one(self):
        module = self.require_scheduler()
        stories = [
            {"id": "alpha", "priority": 0},
            {"id": "beta", "priority": 1},
            {"id": "gamma", "priority": 2},
        ]
        parallel = make_bundle(stories)
        state = make_state(parallel)
        set_lifecycle(state, parallel, "alpha", "running")
        finalize_state(state, parallel)
        self.assertEqual(
            ["beta"],
            [
                item["storyId"]
                for item in module.decide(parallel, state, make_evidence(parallel))["candidates"]
            ],
        )

        sequential = make_bundle(stories, execution_mode="sequential")
        decision = module.decide(
            sequential, make_state(sequential), make_evidence(sequential)
        )
        self.assertEqual(["alpha"], [item["storyId"] for item in decision["candidates"]])

    def test_windows_case_ancestor_conflicts_protect_active_and_post_worker_scopes(self):
        module = self.require_scheduler()
        for lifecycle in (
            "running",
            "process-unknown",
            "worker-complete-unverified",
            "verified-unimported",
        ):
            with self.subTest(lifecycle=lifecycle):
                bundle = make_bundle(
                    [
                        {"id": "owner", "writeScopes": ["SRC/Core"], "priority": 9},
                        {
                            "id": "conflict",
                            "writeScopes": ["src/core/file.py"],
                            "priority": 0,
                        },
                        {"id": "safe", "writeScopes": ["src/other"], "priority": 1},
                    ]
                )
                state = make_state(bundle)
                set_lifecycle(state, bundle, "owner", lifecycle)
                finalize_state(state, bundle)
                decision = module.decide(bundle, state, make_evidence(bundle))
                self.assertEqual(
                    ["safe"], [item["storyId"] for item in decision["candidates"]]
                )

    def test_imported_and_merged_scopes_are_protected_until_post_check(self):
        module = self.require_scheduler()
        imported_bundle = make_bundle(
            [
                {"id": "first", "writeScopes": ["src/first"]},
                {"id": "protected", "writeScopes": ["SRC/Core"]},
                {"id": "conflict", "writeScopes": ["src/core/child"], "priority": 0},
                {"id": "safe", "writeScopes": ["src/safe"], "priority": 1},
            ]
        )
        imported_state = make_state(imported_bundle)
        set_lifecycle(imported_state, imported_bundle, "first", "worker-complete-unverified")
        set_lifecycle(imported_state, imported_bundle, "protected", "imported-awaiting-integration")
        finalize_state(imported_state, imported_bundle)
        imported = module.decide(
            imported_bundle, imported_state, make_evidence(imported_bundle)
        )
        self.assertEqual(["safe"], [item["storyId"] for item in imported["candidates"]])

        merged_bundle = make_bundle(
            [
                {"id": "protected", "writeScopes": ["SRC/Core"]},
                {"id": "conflict", "writeScopes": ["src/core/child"], "priority": 0},
                {"id": "safe", "writeScopes": ["src/safe"], "priority": 1},
            ]
        )
        merged_state = make_state(merged_bundle)
        set_lifecycle(merged_state, merged_bundle, "protected", "merged-awaiting-post-check")
        finalize_state(merged_state, merged_bundle)
        merged = module.decide(merged_bundle, merged_state, make_evidence(merged_bundle))
        self.assertEqual(["safe"], [item["storyId"] for item in merged["candidates"]])

    def test_same_decision_candidates_use_windows_ancestor_conflict_rules(self):
        module = self.require_scheduler()
        bundle = make_bundle(
            [
                {"id": "parent", "writeScopes": ["SRC/Feature"], "priority": 0},
                {"id": "child", "writeScopes": ["src/feature/sub"], "priority": 1},
                {"id": "safe", "writeScopes": ["src/elsewhere"], "priority": 2},
            ]
        )
        decision = module.decide(bundle, make_state(bundle), make_evidence(bundle))
        self.assertEqual(
            ["parent", "safe"],
            [item["storyId"] for item in decision["candidates"]],
        )

    def test_shared_state_mutation_is_never_dispatchable(self):
        module = self.require_scheduler()
        bundle = make_bundle(
            [{"id": "alpha", "sharedMode": "mutates"}],
            execution_mode="sequential",
        )
        decision = module.decide(bundle, make_state(bundle), make_evidence(bundle))
        self.assertEqual({"action", "bindings", "reason"}, set(decision))
        self.assertEqual("wait", decision["action"])

    def test_actionable_waiting_and_blocked_gate_readiness(self):
        module = self.require_scheduler()
        bundle = make_bundle([{"id": "alpha"}])
        state = make_state(bundle)
        actionable = module.decide(bundle, state, make_evidence(bundle))
        self.assertEqual("dispatch", actionable["action"])
        waiting = module.decide(
            bundle,
            state,
            make_evidence(bundle, statuses={"alpha": "waiting"}),
        )
        self.assertEqual("wait", waiting["action"])
        blocked = module.decide(
            bundle,
            state,
            make_evidence(bundle, statuses={"alpha": "blocked"}),
        )
        self.assertEqual("block", blocked["action"])
        self.assertEqual(["alpha"], blocked["storyIds"])

    def test_gate_evidence_rejects_missing_mismatched_duplicate_unknown_and_oversized_data(self):
        bundle = make_bundle(
            [{"id": "alpha", "gates": ["gate-one", "gate-two"]}, {"id": "beta"}]
        )
        state = make_state(bundle)
        valid = make_evidence(bundle)
        mutations = {
            "missing": lambda value: value["gateReadiness"].pop(),
            "duplicate": lambda value: value["gateReadiness"].__setitem__(
                1, copy.deepcopy(value["gateReadiness"][0])
            ),
            "unknown": lambda value: value["gateReadiness"][1].update(storyId="unknown"),
            "planDigest": lambda value: value.update(planDigest="sha256:" + "0" * 64),
            "hostEvidenceDigest": lambda value: value.update(
                hostEvidenceDigest="sha256:" + "0" * 64
            ),
            "gatePolicyDigest": lambda value: value.update(
                gatePolicyDigest="sha256:" + "0" * 64
            ),
            "gateIds": lambda value: value["gateReadiness"][0].update(
                requiredOutcomeGateIds=["gate-two", "gate-one"]
            ),
            "missingApproval": lambda value: value["gateReadiness"][0].update(
                approvalDigests=value["gateReadiness"][0]["approvalDigests"][:1]
            ),
            "oversized": lambda value: value["gateReadiness"][0].update(
                approvalDigests=["sha256:" + "a" * 64] * 257
            ),
            "extra": lambda value: value.update(authorizesExecution=True),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                candidate = copy.deepcopy(valid)
                mutate(candidate)
                self.assert_scheduler_error(
                    "decisionEvidence|gateReadiness|approvalDigests|requiredOutcomeGateIds|Digest",
                    lambda candidate=candidate: self.require_scheduler().decide(
                        bundle, state, candidate
                    ),
                )

    def test_waiting_or_blocked_cannot_smuggle_approval_and_ungated_cannot_carry_it(self):
        gated = make_bundle([{"id": "alpha"}])
        for status in ("waiting", "blocked"):
            with self.subTest(status=status):
                evidence = make_evidence(gated, statuses={"alpha": status})
                evidence["gateReadiness"][0]["approvalDigests"] = [
                    _story_digest("alpha", "smuggled")
                ]
                self.assert_scheduler_error(
                    "approvalDigests",
                    lambda evidence=evidence: self.require_scheduler().decide(
                        gated, make_state(gated), evidence
                    ),
                )
        ungated = make_bundle([{"id": "alpha", "gates": []}])
        evidence = make_evidence(ungated)
        evidence["gateReadiness"][0]["approvalDigests"] = [
            _story_digest("alpha", "unbound")
        ]
        self.assert_scheduler_error(
            "approvalDigests",
            lambda: self.require_scheduler().decide(ungated, make_state(ungated), evidence),
        )

    def test_malformed_and_stale_decision_timestamps_fail_closed(self):
        bundle = make_bundle([{"id": "alpha"}])
        state = make_state(bundle)
        malformed = make_evidence(bundle)
        malformed["observedAt"] = "not-a-timestamp"
        self.assert_scheduler_error(
            "observedAt", lambda: self.require_scheduler().decide(bundle, state, malformed)
        )
        expired = make_evidence(
            bundle,
            observed_at="2026-09-01T12:04:01Z",
            valid_until="2026-09-01T12:04:00Z",
        )
        self.assert_scheduler_error(
            "validUntil", lambda: self.require_scheduler().decide(bundle, state, expired)
        )

        host_expired_bundle = make_bundle(
            [{"id": "alpha"}], host_valid_until="2026-09-01T12:02:30Z"
        )
        host_expired_state = make_state(host_expired_bundle)
        host_expired = make_evidence(
            host_expired_bundle,
            observed_at="2026-09-01T12:03:00Z",
            valid_until="2026-09-01T12:04:00Z",
        )
        self.assert_scheduler_error(
            "hostCapabilities.validUntil",
            lambda: self.require_scheduler().decide(
                host_expired_bundle, host_expired_state, host_expired
            ),
        )

    def test_decision_evidence_cannot_predate_its_bound_plan(self):
        bundle = make_bundle([{"id": "alpha"}])
        state = make_state(bundle)
        evidence = make_evidence(
            bundle,
            observed_at="2026-09-01T12:00:59Z",
            valid_until="2026-09-01T12:04:00Z",
        )
        self.assert_scheduler_error(
            "planningTimestamp",
            lambda: self.require_scheduler().decide(bundle, state, evidence),
        )

    def test_public_roots_reject_pair_arrays_instead_of_coercing_them_to_objects(self):
        bundle = make_bundle([{"id": "alpha"}])
        state = make_state(bundle)
        evidence = make_evidence(bundle)
        valid_arguments = [bundle, state, evidence]
        for index, root_name in enumerate(
            ("executionBundle", "pipelineState", "decisionEvidence")
        ):
            with self.subTest(root=root_name):
                arguments = copy.deepcopy(valid_arguments)
                arguments[index] = list(arguments[index].items())
                self.assert_scheduler_error(
                    root_name,
                    lambda arguments=arguments: self.require_scheduler().decide(*arguments),
                )

    def test_integration_precedes_dispatch_and_uses_durable_ordinal_not_priority(self):
        module = self.require_scheduler()
        bundle = make_bundle(
            [
                {"id": "alpha", "priority": 99},
                {"id": "beta", "priority": 0},
                {"id": "gamma", "priority": 1},
            ]
        )
        state = make_state(bundle)
        set_lifecycle(state, bundle, "alpha", "imported-awaiting-integration")
        set_lifecycle(state, bundle, "beta", "imported-awaiting-integration")
        finalize_state(state, bundle)
        decision = module.decide(bundle, state, make_evidence(bundle))
        self.assertEqual(
            {
                "action": "integrate",
                "bindings": decision["bindings"],
                "storyId": "alpha",
                "integrationOrdinal": 1,
            },
            decision,
        )

    def test_durable_block_has_highest_precedence(self):
        module = self.require_scheduler()
        bundle = make_bundle([{"id": "alpha"}])
        state = make_blocked_state(bundle)
        evidence = make_evidence(bundle, statuses={"alpha": "blocked"})
        decision = module.decide(bundle, state, evidence)
        self.assertEqual("block", decision["action"])
        self.assertEqual("durable-blocker", decision["reason"])

    def test_complete_requires_all_integrated_no_owners_and_no_queue(self):
        module = self.require_scheduler()
        bundle = make_bundle([{"id": "alpha"}, {"id": "beta"}])
        state = make_state(bundle)
        set_lifecycle(state, bundle, "alpha", "integration-verified")
        finalize_state(state, bundle)
        set_lifecycle(state, bundle, "beta", "integration-verified")
        finalize_state(state, bundle)
        decision = module.decide(bundle, state, make_evidence(bundle))
        self.assertEqual("complete", decision["action"])
        self.assertEqual(["alpha", "beta"], decision["storyIds"])

    def test_wait_when_no_higher_precedence_action_is_eligible(self):
        module = self.require_scheduler()
        bundle = make_bundle(
            [{"id": "alpha"}, {"id": "beta", "dependsOn": ["alpha"]}],
            execution_mode="sequential",
        )
        state = make_state(bundle)
        set_lifecycle(state, bundle, "alpha", "running")
        finalize_state(state, bundle)
        decision = module.decide(bundle, state, make_evidence(bundle))
        self.assertEqual("wait", decision["action"])
        self.assertEqual("no-eligible-action", decision["reason"])

    def test_dispatch_binds_current_worker_start_sha_and_prerequisite_digests(self):
        module = self.require_scheduler()
        bundle = make_bundle(
            [{"id": "alpha"}, {"id": "beta", "dependsOn": ["alpha"]}]
        )
        state = make_state(bundle)
        set_lifecycle(state, bundle, "alpha", "integration-verified")
        finalize_state(state, bundle)
        evidence = make_evidence(bundle)
        decision = module.decide(bundle, state, evidence)
        candidate = decision["candidates"][0]
        alpha = state["stories"][0]
        self.assertEqual(state["lastVerifiedIntegrationSha"], candidate["workerStartSha"])
        self.assertEqual(
            [
                {
                    "storyId": "alpha",
                    "workerReceiptDigest": alpha["workerReceiptDigest"],
                    "integrationEvidenceDigest": alpha["postCheckEvidenceDigest"],
                    "gateEvidenceDigests": alpha["gateEvidenceDigests"],
                }
            ],
            candidate["prerequisites"],
        )
        self.assertEqual(canonical_digest(bundle), decision["bindings"]["executionBundleDigest"])
        self.assertEqual(canonical_digest(state), decision["bindings"]["pipelineStateDigest"])

    def test_repeated_and_permuted_equivalent_evidence_is_byte_deterministic_and_pure(self):
        module = self.require_scheduler()
        bundle = make_bundle([{"id": "alpha"}, {"id": "beta"}])
        state = make_state(bundle)
        evidence = make_evidence(bundle)
        originals = copy.deepcopy((bundle, state, evidence))
        first = module.decide(bundle, state, evidence)
        second = module.decide(bundle, state, evidence)
        permuted = copy.deepcopy(evidence)
        permuted["gateReadiness"].reverse()
        third = module.decide(bundle, state, permuted)
        self.assertEqual(canonical_data(first), canonical_data(second))
        self.assertEqual(canonical_data(first), canonical_data(third))
        self.assertEqual(originals, (bundle, state, evidence))

    def test_scheduler_has_no_side_effect_imports_or_primitives_and_preserves_contract_versions(self):
        module = self.require_scheduler()
        tree = ast.parse(SCHEDULER_SOURCE.read_text(encoding="utf-8"))
        imported_roots = set()
        called_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called_names.add(node.func.id)
        self.assertTrue(
            imported_roots.isdisjoint(
                {
                    "asyncio",
                    "git",
                    "http",
                    "os",
                    "pathlib",
                    "random",
                    "requests",
                    "shutil",
                    "socket",
                    "subprocess",
                    "tempfile",
                    "time",
                    "urllib",
                }
            )
        )
        self.assertTrue(called_names.isdisjoint({"eval", "exec", "open", "__import__"}))
        self.assertEqual(
            "compass-builder.run-spec.v1", rolling_models.SCHEMA_VERSIONS["run-spec"]
        )
        self.assertEqual(
            "compass-builder.wave-plan.v1", rolling_models.SCHEMA_VERSIONS["wave-plan"]
        )
        self.assertEqual(
            "compass-builder.run-state.v1", rolling_models.SCHEMA_VERSIONS["run-state"]
        )
        self.assertEqual(
            DECISION_EVIDENCE_VERSION, module.DECISION_EVIDENCE_VERSION
        )


if __name__ == "__main__":
    unittest.main()
