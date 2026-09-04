"""Closed v2 rolling-pipeline shapes and side-effect-free semantic validators."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from ._limits import (
    MAX_BLOCKERS,
    MAX_COMMANDS,
    MAX_DEPENDENCIES,
    MAX_REASONS,
    MAX_SCOPES,
    MAX_STORIES,
)
from ._run_models import _scopes, _story_graph, validate_host_shape
from ._validation import (
    ContractValidationError,
    EFFORTS,
    array,
    boolean,
    branch,
    canonical_data,
    canonical_digest,
    digest,
    enum,
    fail,
    identifier,
    integer,
    object_,
    run_id,
    sha,
    string,
    strings,
    timestamp,
)


MAX_PIPELINE_EVENTS = 16_384
MAX_ACTIVE_OWNERS = 128
MAX_GATE_EVIDENCE = 256


RUN_SPEC_V2_VERSION = "compass-builder.run-spec.v2"
PIPELINE_PLAN_VERSION = "compass-builder.pipeline-plan.v2"
PIPELINE_STATE_VERSION = "compass-builder.pipeline-state.v2"
PIPELINE_EVENT_VERSION = "compass-builder.pipeline-event.v2"
EXECUTION_BUNDLE_V2_VERSION = "compass-builder.execution-bundle.v2"
DISPATCH_RECORD_VERSION = "compass-builder.dispatch-record.v2"

EXECUTION_MODES = {"sequential", "parallel"}
DISPATCH_STRATEGIES = {"wave-barrier", "rolling"}
PIPELINE_RUN_STATES = {"planned", "running", "draining", "completed", "blocked"}
STORY_LIFECYCLES = {
    "never-launched",
    "running",
    "process-unknown",
    "worker-complete-unverified",
    "verified-unimported",
    "imported-awaiting-integration",
    "merged-awaiting-post-check",
    "integration-verified",
    "blocked",
}
NON_BLOCKED_LIFECYCLES = STORY_LIFECYCLES - {"blocked"}
EVENT_TYPES = {
    "dispatch",
    "completion",
    "verification",
    "import",
    "merge-intent",
    "merge",
    "post-check",
    "gate-result",
    "block",
}
EVENT_TRANSITIONS = {
    "dispatch": ("never-launched", "running"),
    "completion": ("running", "worker-complete-unverified"),
    "verification": ("worker-complete-unverified", "verified-unimported"),
    "import": ("verified-unimported", "imported-awaiting-integration"),
    "merge-intent": (
        "imported-awaiting-integration",
        "imported-awaiting-integration",
    ),
    "merge": (
        "imported-awaiting-integration",
        "merged-awaiting-post-check",
    ),
    "post-check": ("merged-awaiting-post-check", "integration-verified"),
}


def _exact_version(data: Mapping[str, Any], expected: str, path: str = "$") -> None:
    if data.get("schemaVersion") != expected:
        fail(
            f"{path}.schemaVersion",
            f"expected immutable version {expected!r}",
            f"use {expected!r} and migrate the complete shape",
        )


def _gate_ids(value: Any, path: str) -> list[str]:
    result = strings(
        value,
        path,
        maximum=64,
        items_maximum=MAX_GATE_EVIDENCE,
    )
    for index, item in enumerate(result):
        identifier(item, f"{path}[{index}]")
    return result


def _core_story(story: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in story.items() if key != "requiredOutcomeGateIds"}


def _rolling_story(value: Any, path: str) -> dict[str, Any]:
    story = object_(
        value,
        path,
        {
            "id",
            "title",
            "description",
            "dependsOn",
            "writeScopes",
            "acceptanceChecks",
            "validationCommands",
            "independentReviewPath",
            "sharedState",
            "priority",
            "completionState",
            "complexity",
            "ambiguity",
            "risk",
            "validationStrength",
            "requiredOutcomeGateIds",
        },
    )
    _gate_ids(story["requiredOutcomeGateIds"], f"{path}.requiredOutcomeGateIds")
    return story


def validate_run_spec_v2_shape(data: dict[str, Any]) -> None:
    object_(
        data,
        "$",
        {
            "schemaVersion",
            "runId",
            "baseRef",
            "baseSha",
            "integrationBranch",
            "integrationExpectedSha",
            "executionMode",
            "dispatchStrategy",
            "experimentalRollingAuthorized",
            "exactModel",
            "effortPolicyVersion",
            "hostConcurrencyCeiling",
            "userConcurrencyCeiling",
            "calibratedConcurrencyCeiling",
            "validationCommands",
            "stories",
        },
    )
    run_id(data["runId"], "$.runId")
    string(data["baseRef"], "$.baseRef", maximum=240)
    sha(data["baseSha"], "$.baseSha")
    branch(data["integrationBranch"], "$.integrationBranch")
    sha(data["integrationExpectedSha"], "$.integrationExpectedSha")
    enum(data["executionMode"], "$.executionMode", EXECUTION_MODES)
    strategy = enum(data["dispatchStrategy"], "$.dispatchStrategy", DISPATCH_STRATEGIES)
    authorized = boolean(
        data["experimentalRollingAuthorized"], "$.experimentalRollingAuthorized"
    )
    if strategy == "rolling" and not authorized:
        fail(
            "$.experimentalRollingAuthorized",
            "rolling dispatch lacks explicit experimental authorization",
            "set true only after the user authorizes the experimental v2 route",
        )
    if string(data["exactModel"], "$.exactModel", maximum=160) == "inherit":
        fail("$.exactModel", "must name an exact model", "bind the selected model ID")
    string(data["effortPolicyVersion"], "$.effortPolicyVersion", maximum=128)
    for field in (
        "hostConcurrencyCeiling",
        "userConcurrencyCeiling",
        "calibratedConcurrencyCeiling",
    ):
        integer(data[field], f"$.{field}", minimum=1)
    strings(
        data["validationCommands"],
        "$.validationCommands",
        minimum=1,
        items_maximum=MAX_COMMANDS,
    )
    stories = array(data["stories"], "$.stories", minimum=1, maximum=MAX_STORIES)
    parsed = [_rolling_story(item, f"$.stories[{index}]") for index, item in enumerate(stories)]
    _story_graph([_core_story(story) for story in parsed], "$.stories")


def _plan_story(value: Any, path: str, expected_index: int) -> dict[str, Any]:
    story = object_(
        value,
        path,
        {
            "storyId",
            "specificationOrder",
            "integrationOrdinal",
            "dependsOn",
            "branch",
            "recommendedEffort",
            "handoffDigest",
            "writeScopes",
            "requiredOutcomeGateIds",
        },
    )
    identifier(story["storyId"], f"{path}.storyId")
    if integer(story["specificationOrder"], f"{path}.specificationOrder") != expected_index:
        fail(
            f"{path}.specificationOrder",
            "does not match immutable array order",
            f"set it to {expected_index}",
        )
    integer(story["integrationOrdinal"], f"{path}.integrationOrdinal", minimum=1)
    dependencies = strings(
        story["dependsOn"],
        f"{path}.dependsOn",
        maximum=64,
        items_maximum=MAX_DEPENDENCIES,
    )
    for index, dependency in enumerate(dependencies):
        identifier(dependency, f"{path}.dependsOn[{index}]")
    branch(story["branch"], f"{path}.branch")
    enum(story["recommendedEffort"], f"{path}.recommendedEffort", EFFORTS)
    digest(story["handoffDigest"], f"{path}.handoffDigest")
    _scopes(story["writeScopes"], f"{path}.writeScopes")
    _gate_ids(story["requiredOutcomeGateIds"], f"{path}.requiredOutcomeGateIds")
    return story


def validate_pipeline_plan_shape(data: dict[str, Any]) -> None:
    object_(
        data,
        "$",
        {
            "schemaVersion",
            "runId",
            "baseSha",
            "integrationBranch",
            "integrationExpectedSha",
            "normalizedInputDigest",
            "hostEvidenceDigest",
            "effortPolicyVersion",
            "executionMode",
            "dispatchStrategy",
            "schedulingPolicyDigest",
            "gatePolicyDigest",
            "reasons",
            "concurrency",
            "initialReadyStoryIds",
            "stories",
        },
    )
    run_id(data["runId"], "$.runId")
    sha(data["baseSha"], "$.baseSha")
    branch(data["integrationBranch"], "$.integrationBranch")
    sha(data["integrationExpectedSha"], "$.integrationExpectedSha")
    for field in (
        "normalizedInputDigest",
        "hostEvidenceDigest",
        "schedulingPolicyDigest",
        "gatePolicyDigest",
    ):
        digest(data[field], f"$.{field}")
    string(data["effortPolicyVersion"], "$.effortPolicyVersion", maximum=128)
    mode = enum(data["executionMode"], "$.executionMode", EXECUTION_MODES)
    enum(data["dispatchStrategy"], "$.dispatchStrategy", DISPATCH_STRATEGIES)
    strings(data["reasons"], "$.reasons", minimum=1, items_maximum=MAX_REASONS)
    concurrency = integer(data["concurrency"], "$.concurrency", minimum=1)
    if mode == "sequential" and concurrency != 1:
        fail("$.concurrency", "sequential execution requires concurrency 1", "set concurrency to 1")
    if mode == "parallel" and concurrency < 2:
        fail("$.concurrency", "parallel execution requires concurrency >= 2", "raise the bounded ceiling or select sequential")
    initial = strings(
        data["initialReadyStoryIds"],
        "$.initialReadyStoryIds",
        minimum=1,
        maximum=64,
        items_maximum=MAX_STORIES,
    )
    for index, story_id in enumerate(initial):
        identifier(story_id, f"$.initialReadyStoryIds[{index}]")
    stories = [
        _plan_story(item, f"$.stories[{index}]", index)
        for index, item in enumerate(
            array(data["stories"], "$.stories", minimum=1, maximum=MAX_STORIES)
        )
    ]
    ids = [story["storyId"] for story in stories]
    if len(set(ids)) != len(ids):
        fail("$.stories", "contains duplicate story IDs", "bind each story once")
    ordinals = [story["integrationOrdinal"] for story in stories]
    if ordinals != list(range(1, len(stories) + 1)):
        fail(
            "$.stories.integrationOrdinal",
            "must be the immutable contiguous integration order",
            "assign ordinals 1 through the story count in specification order",
        )
    known: set[str] = set()
    for index, story in enumerate(stories):
        unknown = [item for item in story["dependsOn"] if item not in known]
        if unknown:
            fail(
                f"$.stories[{index}].dependsOn",
                f"is not topological or references unknown IDs: {', '.join(unknown)}",
                "bind only earlier specification-order stories",
            )
        known.add(story["storyId"])
    expected_initial = [story["storyId"] for story in stories if not story["dependsOn"]]
    if initial != expected_initial:
        fail(
            "$.initialReadyStoryIds",
            "does not equal the ordered dependency-free story set",
            "derive it from stories with no prerequisites",
        )
    branch_owners = [("integrationBranch", data["integrationBranch"])] + [
        (f"stories[{index}].branch", story["branch"])
        for index, story in enumerate(stories)
    ]
    for index, (left_path, left_branch) in enumerate(branch_owners):
        for right_path, right_branch in branch_owners[index + 1 :]:
            if _branches_collide(left_branch, right_branch):
                fail(
                    f"$.{right_path}",
                    f"has a Windows D/F collision with {left_path}",
                    "use branches that are neither equal nor ancestor/descendant under case folding",
                )


def _pipeline_blocker(value: Any, path: str) -> dict[str, Any]:
    blocker = object_(
        value,
        path,
        {
            "blockerId",
            "eventId",
            "storyId",
            "phase",
            "reason",
            "evidenceDigest",
            "resumeState",
        },
    )
    identifier(blocker["blockerId"], f"{path}.blockerId")
    identifier(blocker["eventId"], f"{path}.eventId")
    if blocker["storyId"] is not None:
        identifier(blocker["storyId"], f"{path}.storyId")
    enum(
        blocker["phase"],
        f"{path}.phase",
        {"dispatch", "worker", "verification", "import", "merge", "post-check", "gate", "controller"},
    )
    string(blocker["reason"], f"{path}.reason", maximum=2000)
    digest(blocker["evidenceDigest"], f"{path}.evidenceDigest")
    enum(blocker["resumeState"], f"{path}.resumeState", PIPELINE_RUN_STATES - {"completed", "blocked"})
    return blocker


def _state_story(value: Any, path: str) -> dict[str, Any]:
    story = object_(
        value,
        path,
        {
            "storyId",
            "integrationOrdinal",
            "lifecycle",
            "blockedFromLifecycle",
            "attempt",
            "workerStartSha",
            "branch",
            "registeredCloneDigest",
            "workerReceiptDigest",
            "verificationEvidenceDigest",
            "importEvidenceDigest",
            "mergeIntentDigest",
            "integrationSha",
            "postCheckEvidenceDigest",
            "gateEvidenceDigests",
        },
    )
    identifier(story["storyId"], f"{path}.storyId")
    integer(story["integrationOrdinal"], f"{path}.integrationOrdinal", minimum=1)
    lifecycle = enum(story["lifecycle"], f"{path}.lifecycle", STORY_LIFECYCLES)
    blocked_from = story["blockedFromLifecycle"]
    if lifecycle == "blocked":
        enum(blocked_from, f"{path}.blockedFromLifecycle", NON_BLOCKED_LIFECYCLES - {"integration-verified"})
    elif blocked_from is not None:
        fail(f"{path}.blockedFromLifecycle", "is only valid for a blocked story", "set it to null")
    attempt = integer(story["attempt"], f"{path}.attempt")
    sha(story["workerStartSha"], f"{path}.workerStartSha", nullable=True)
    branch(story["branch"], f"{path}.branch")
    for field in (
        "registeredCloneDigest",
        "workerReceiptDigest",
        "verificationEvidenceDigest",
        "importEvidenceDigest",
        "mergeIntentDigest",
        "postCheckEvidenceDigest",
    ):
        digest(story[field], f"{path}.{field}", nullable=True)
    sha(story["integrationSha"], f"{path}.integrationSha", nullable=True)
    evidence = strings(
        story["gateEvidenceDigests"],
        f"{path}.gateEvidenceDigests",
        maximum=80,
        items_maximum=MAX_GATE_EVIDENCE,
    )
    for index, item in enumerate(evidence):
        digest(item, f"{path}.gateEvidenceDigests[{index}]")

    effective = blocked_from if lifecycle == "blocked" else lifecycle
    ordered_evidence = (
        "workerStartSha",
        "registeredCloneDigest",
        "workerReceiptDigest",
        "verificationEvidenceDigest",
        "importEvidenceDigest",
        "mergeIntentDigest",
        "integrationSha",
        "postCheckEvidenceDigest",
    )
    ordered_requirements = {
        "running": ("workerStartSha", "registeredCloneDigest"),
        "process-unknown": ("workerStartSha", "registeredCloneDigest"),
        "worker-complete-unverified": (
            "workerStartSha",
            "registeredCloneDigest",
            "workerReceiptDigest",
        ),
        "verified-unimported": (
            "workerStartSha",
            "registeredCloneDigest",
            "workerReceiptDigest",
            "verificationEvidenceDigest",
        ),
        "imported-awaiting-integration": (
            "workerStartSha",
            "registeredCloneDigest",
            "workerReceiptDigest",
            "verificationEvidenceDigest",
            "importEvidenceDigest",
        ),
        "merged-awaiting-post-check": (
            "workerStartSha",
            "registeredCloneDigest",
            "workerReceiptDigest",
            "verificationEvidenceDigest",
            "importEvidenceDigest",
            "mergeIntentDigest",
            "integrationSha",
        ),
        "integration-verified": (
            "workerStartSha",
            "registeredCloneDigest",
            "workerReceiptDigest",
            "verificationEvidenceDigest",
            "importEvidenceDigest",
            "mergeIntentDigest",
            "integrationSha",
            "postCheckEvidenceDigest",
        ),
    }
    if effective == "never-launched":
        if attempt != 0:
            fail(f"{path}.attempt", "never-launched requires attempt 0", "reset unlaunched state")
        present = [field for field in ordered_evidence if story[field] is not None]
        if present:
            fail(path, f"never-launched contains premature evidence: {', '.join(present)}", "clear launch and completion evidence")
        if evidence:
            fail(f"{path}.gateEvidenceDigests", "never-launched contains premature gate evidence", "clear gate evidence before launch")
    else:
        if attempt < 1:
            fail(f"{path}.attempt", "launched lifecycle requires attempt >= 1", "record the active attempt")
        for field in ordered_requirements.get(effective, ()):
            if story[field] is None:
                fail(f"{path}.{field}", f"is required for lifecycle {effective!r}", "retain phase evidence before advancing")
        allowed_counts = {
            "running": 2,
            "process-unknown": 2,
            "worker-complete-unverified": 3,
            "verified-unimported": 4,
            # A merge intent may be durably recorded immediately before integration.
            "imported-awaiting-integration": 6,
            "merged-awaiting-post-check": 7,
            "integration-verified": 8,
        }
        for field in ordered_evidence[allowed_counts[effective] :]:
            if story[field] is not None:
                fail(f"{path}.{field}", f"is premature for lifecycle {effective!r}", "clear evidence from a future lifecycle phase")
        if effective in {"running", "process-unknown", "worker-complete-unverified"} and evidence:
            fail(f"{path}.gateEvidenceDigests", f"is premature for lifecycle {effective!r}", "record gate results only after verification")
    return story


def _active_owner(value: Any, path: str) -> dict[str, Any]:
    owner = object_(
        value,
        path,
        {"storyId", "ownerId", "writeScopes", "workerStartSha", "registeredCloneDigest"},
    )
    identifier(owner["storyId"], f"{path}.storyId")
    identifier(owner["ownerId"], f"{path}.ownerId")
    _scopes(owner["writeScopes"], f"{path}.writeScopes")
    sha(owner["workerStartSha"], f"{path}.workerStartSha")
    digest(owner["registeredCloneDigest"], f"{path}.registeredCloneDigest")
    return owner


def _folded_scope(raw: str) -> tuple[str, ...]:
    return tuple(part.casefold() for part in raw.split("/"))


def _scopes_overlap(left: str, right: str) -> bool:
    a = _folded_scope(left)
    b = _folded_scope(right)
    return a == b or a[: len(b)] == b or b[: len(a)] == a


def _branch_parts(raw: str) -> tuple[str, ...]:
    return tuple(part.casefold() for part in raw.split("/"))


def _branches_collide(left: str, right: str) -> bool:
    a = _branch_parts(left)
    b = _branch_parts(right)
    return a == b or a[: len(b)] == b or b[: len(a)] == a


def validate_pipeline_state_shape(data: dict[str, Any]) -> None:
    object_(
        data,
        "$",
        {
            "schemaVersion",
            "runId",
            "planDigest",
            "baseSha",
            "integrationBranch",
            "initialIntegrationSha",
            "currentIntegrationSha",
            "lastVerifiedIntegrationSha",
            "previousState",
            "state",
            "lastEventSequence",
            "lastEventDigest",
            "activeOwners",
            "integrationQueue",
            "activeBlocker",
            "blockerHistory",
            "stories",
        },
    )
    run_id(data["runId"], "$.runId")
    digest(data["planDigest"], "$.planDigest")
    sha(data["baseSha"], "$.baseSha")
    branch(data["integrationBranch"], "$.integrationBranch")
    for field in (
        "initialIntegrationSha",
        "currentIntegrationSha",
        "lastVerifiedIntegrationSha",
    ):
        sha(data[field], f"$.{field}")
    if data["previousState"] is not None:
        enum(data["previousState"], "$.previousState", PIPELINE_RUN_STATES)
    run_state = enum(data["state"], "$.state", PIPELINE_RUN_STATES)
    sequence = integer(data["lastEventSequence"], "$.lastEventSequence")
    digest(data["lastEventDigest"], "$.lastEventDigest", nullable=True)
    if (sequence == 0) != (data["lastEventDigest"] is None):
        fail("$.lastEventDigest", "must be null exactly when lastEventSequence is zero", "bind the latest event or clear both fields")
    owners = [
        _active_owner(item, f"$.activeOwners[{index}]")
        for index, item in enumerate(
            array(data["activeOwners"], "$.activeOwners", maximum=MAX_ACTIVE_OWNERS)
        )
    ]
    owner_ids = [owner["storyId"] for owner in owners]
    if len(set(owner_ids)) != len(owner_ids):
        fail("$.activeOwners", "contains duplicate story ownership", "retain one durable owner per story")
    for left_index, left in enumerate(owners):
        for right in owners[left_index + 1 :]:
            for left_scope in left["writeScopes"]:
                for right_scope in right["writeScopes"]:
                    if _scopes_overlap(left_scope, right_scope):
                        fail("$.activeOwners", f"active scopes overlap for {left['storyId']!r} and {right['storyId']!r}", "serialize overlapping ownership")
    queue = strings(
        data["integrationQueue"],
        "$.integrationQueue",
        maximum=64,
        items_maximum=MAX_STORIES,
    )
    for index, story_id in enumerate(queue):
        identifier(story_id, f"$.integrationQueue[{index}]")
    blocker = data["activeBlocker"]
    if blocker is not None:
        blocker = _pipeline_blocker(blocker, "$.activeBlocker")
    history = [
        _pipeline_blocker(item, f"$.blockerHistory[{index}]")
        for index, item in enumerate(
            array(data["blockerHistory"], "$.blockerHistory", maximum=MAX_BLOCKERS)
        )
    ]
    blocker_ids = [item["blockerId"] for item in history]
    if len(set(blocker_ids)) != len(blocker_ids):
        fail("$.blockerHistory", "contains duplicate blocker IDs", "append each immutable blocker once")
    stories = [
        _state_story(item, f"$.stories[{index}]")
        for index, item in enumerate(
            array(data["stories"], "$.stories", minimum=1, maximum=MAX_STORIES)
        )
    ]
    story_ids = [story["storyId"] for story in stories]
    if len(set(story_ids)) != len(story_ids):
        fail("$.stories", "contains duplicate story IDs", "retain one lifecycle ledger per story")
    ordinals = [story["integrationOrdinal"] for story in stories]
    if ordinals != list(range(1, len(stories) + 1)):
        fail("$.stories.integrationOrdinal", "does not preserve contiguous integration order", "retain immutable ordinals 1 through story count")
    by_id = {story["storyId"]: story for story in stories}
    expected_owners = [
        story["storyId"]
        for story in stories
        if story["lifecycle"] in {"running", "process-unknown"}
    ]
    if owner_ids != expected_owners:
        fail("$.activeOwners", "does not exactly match running/process-unknown stories", "derive ownership in immutable story order")
    for owner in owners:
        story = by_id[owner["storyId"]]
        if owner["workerStartSha"] != story["workerStartSha"] or owner["registeredCloneDigest"] != story["registeredCloneDigest"]:
            fail("$.activeOwners", "does not bind story launch identity", "copy the immutable start SHA and clone digest")
    expected_queue = [
        story["storyId"]
        for story in stories
        if story["lifecycle"] == "imported-awaiting-integration"
    ]
    if queue != expected_queue:
        fail("$.integrationQueue", "does not exactly match imported stories in ordinal order", "derive the queue from lifecycle ledgers")
    if run_state == "completed" and any(story["lifecycle"] != "integration-verified" for story in stories):
        fail("$.state", "completed overclaims unfinished stories", "complete only after every story is integration-verified")
    if any(story["lifecycle"] == "blocked" for story in stories) and run_state != "blocked":
        fail("$.state", "must be blocked while any story lifecycle is blocked", "record the run-level blocker before persisting blocked story state")
    if run_state == "blocked":
        if blocker is None or not history or blocker != history[-1]:
            fail("$.activeBlocker", "blocked state must expose the latest immutable blocker", "append and select one blocker")
    elif blocker is not None:
        fail("$.activeBlocker", "is only valid while the run is blocked", "clear it after an authorized resume")
    allowed_transitions = {
        None: {"planned"},
        "planned": {"planned", "running", "blocked"},
        "running": {"running", "draining", "completed", "blocked"},
        "draining": {"draining", "completed", "blocked"},
        "blocked": {"blocked", "running", "draining"},
        "completed": {"completed"},
    }
    if run_state not in allowed_transitions[data["previousState"]]:
        fail(
            "$.previousState",
            f"cannot transition to state {run_state!r}",
            "record a permitted durable predecessor",
        )
    if run_state == "planned":
        if (
            any(story["lifecycle"] != "never-launched" for story in stories)
            or owners
            or queue
            or sequence != 0
            or data["currentIntegrationSha"] != data["initialIntegrationSha"]
            or data["lastVerifiedIntegrationSha"] != data["initialIntegrationSha"]
        ):
            fail(
                "$.state",
                "planned state contains launch, event, queue, or integration progress",
                "retain the pristine initialized snapshot or advance the run state",
            )


def validate_pipeline_event_shape(data: dict[str, Any]) -> None:
    object_(
        data,
        "$",
        {
            "schemaVersion",
            "runId",
            "eventId",
            "sequence",
            "previousEventDigest",
            "eventType",
            "storyId",
            "occurredAt",
            "stateBefore",
            "stateAfter",
            "evidenceDigest",
            "payloadDigest",
        },
    )
    run_id(data["runId"], "$.runId")
    identifier(data["eventId"], "$.eventId")
    sequence = integer(data["sequence"], "$.sequence", minimum=1)
    digest(data["previousEventDigest"], "$.previousEventDigest", nullable=True)
    if sequence == 1 and data["previousEventDigest"] is not None:
        fail("$.previousEventDigest", "first event must not claim a predecessor", "set it to null")
    if sequence > 1 and data["previousEventDigest"] is None:
        fail("$.previousEventDigest", "non-initial event lacks predecessor binding", "record the prior canonical event digest")
    event_type = enum(data["eventType"], "$.eventType", EVENT_TYPES)
    story_id = data["storyId"]
    if story_id is not None:
        identifier(story_id, "$.storyId")
    if event_type not in {"block", "gate-result"} and story_id is None:
        fail("$.storyId", f"event {event_type!r} requires a story", "bind the affected story ID")
    timestamp(data["occurredAt"], "$.occurredAt")
    before = enum(data["stateBefore"], "$.stateBefore", STORY_LIFECYCLES)
    after = enum(data["stateAfter"], "$.stateAfter", STORY_LIFECYCLES)
    if event_type in EVENT_TRANSITIONS and (before, after) != EVENT_TRANSITIONS[event_type]:
        fail("$.stateAfter", f"event {event_type!r} does not match its lifecycle transition", f"use {EVENT_TRANSITIONS[event_type]!r}")
    if event_type == "gate-result" and before != after:
        fail("$.stateAfter", "gate-result evidence cannot advance lifecycle by itself", "retain the current lifecycle")
    if event_type == "block" and (before in {"blocked", "integration-verified"} or after != "blocked"):
        fail("$.stateAfter", "block must transition an active lifecycle to blocked", "record the actual active predecessor and blocked successor")
    digest(data["evidenceDigest"], "$.evidenceDigest")
    digest(data["payloadDigest"], "$.payloadDigest")


def _dispatch_prerequisite(value: Any, path: str) -> dict[str, Any]:
    item = object_(
        value,
        path,
        {"storyId", "workerReceiptDigest", "integrationEvidenceDigest", "gateEvidenceDigests"},
    )
    identifier(item["storyId"], f"{path}.storyId")
    digest(item["workerReceiptDigest"], f"{path}.workerReceiptDigest")
    digest(item["integrationEvidenceDigest"], f"{path}.integrationEvidenceDigest")
    evidence = strings(
        item["gateEvidenceDigests"],
        f"{path}.gateEvidenceDigests",
        maximum=80,
        items_maximum=MAX_GATE_EVIDENCE,
    )
    for index, value in enumerate(evidence):
        digest(value, f"{path}.gateEvidenceDigests[{index}]")
    return item


def validate_dispatch_record_shape(data: dict[str, Any]) -> None:
    object_(
        data,
        "$",
        {
            "schemaVersion",
            "dispatchId",
            "runId",
            "storyId",
            "attempt",
            "planDigest",
            "workerStartSha",
            "prerequisites",
            "exactModel",
            "recommendedEffort",
            "writeScopes",
            "requiredOutcomeGateIds",
            "gateApprovalDigests",
            "handoffDigest",
            "registeredClone",
        },
    )
    identifier(data["dispatchId"], "$.dispatchId")
    run_id(data["runId"], "$.runId")
    identifier(data["storyId"], "$.storyId")
    integer(data["attempt"], "$.attempt", minimum=1)
    digest(data["planDigest"], "$.planDigest")
    sha(data["workerStartSha"], "$.workerStartSha")
    prerequisites = [
        _dispatch_prerequisite(item, f"$.prerequisites[{index}]")
        for index, item in enumerate(
            array(data["prerequisites"], "$.prerequisites", maximum=MAX_DEPENDENCIES)
        )
    ]
    prerequisite_ids = [item["storyId"] for item in prerequisites]
    if len(set(prerequisite_ids)) != len(prerequisite_ids):
        fail("$.prerequisites", "contains duplicate story evidence", "bind each prerequisite once")
    if string(data["exactModel"], "$.exactModel", maximum=160) == "inherit":
        fail("$.exactModel", "must name an exact model", "bind the selected model ID")
    enum(data["recommendedEffort"], "$.recommendedEffort", EFFORTS)
    _scopes(data["writeScopes"], "$.writeScopes")
    gates = _gate_ids(data["requiredOutcomeGateIds"], "$.requiredOutcomeGateIds")
    approvals = strings(
        data["gateApprovalDigests"],
        "$.gateApprovalDigests",
        maximum=80,
        items_maximum=MAX_GATE_EVIDENCE,
    )
    for index, item in enumerate(approvals):
        digest(item, f"$.gateApprovalDigests[{index}]")
    if len(gates) != len(approvals):
        fail("$.gateApprovalDigests", "does not bind every required outcome gate", "provide one ordered approval digest per gate ID")
    digest(data["handoffDigest"], "$.handoffDigest")
    clone = object_(
        data["registeredClone"],
        "$.registeredClone",
        {"cloneId", "repositoryRootDigest", "gitCommonDirDigest", "branch"},
    )
    identifier(clone["cloneId"], "$.registeredClone.cloneId")
    digest(clone["repositoryRootDigest"], "$.registeredClone.repositoryRootDigest")
    digest(clone["gitCommonDirDigest"], "$.registeredClone.gitCommonDirDigest")
    branch(clone["branch"], "$.registeredClone.branch")


def validate_execution_bundle_v2_shape(data: dict[str, Any]) -> None:
    object_(
        data,
        "$",
        {"schemaVersion", "runSpec", "pipelinePlan", "hostCapabilities", "planningTimestamp"},
    )
    _normalized_nested(data["runSpec"], RUN_SPEC_V2_VERSION, validate_run_spec_v2_shape, "$.runSpec")
    _normalized_nested(data["pipelinePlan"], PIPELINE_PLAN_VERSION, validate_pipeline_plan_shape, "$.pipelinePlan")
    _normalized_nested(data["hostCapabilities"], "compass-builder.host-capabilities.v1", validate_host_shape, "$.hostCapabilities")
    timestamp(data["planningTimestamp"], "$.planningTimestamp")


def _normalized_nested(value: Mapping[str, Any], expected: str, validator: Any, path: str) -> dict[str, Any]:
    try:
        data = copy.deepcopy(dict(value))
    except (TypeError, ValueError):
        fail(path, "must be a mapping", "provide a decoded JSON object")
    _exact_version(data, expected, path)
    try:
        validator(data)
    except ContractValidationError as exc:
        message = str(exc)
        if message.startswith("$"):
            message = path + message[1:]
        raise ContractValidationError(message) from exc
    try:
        canonical_data(data)
    except (TypeError, ValueError) as exc:
        fail(path, f"is not canonical JSON data ({exc})", "use only finite JSON values")
    return data


def validate_rolling_plan_bindings(
    run_spec: Mapping[str, Any], pipeline_plan: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = _normalized_nested(run_spec, RUN_SPEC_V2_VERSION, validate_run_spec_v2_shape, "runSpec")
    plan = _normalized_nested(pipeline_plan, PIPELINE_PLAN_VERSION, validate_pipeline_plan_shape, "pipelinePlan")
    for field in (
        "runId",
        "baseSha",
        "integrationBranch",
        "integrationExpectedSha",
        "effortPolicyVersion",
        "executionMode",
        "dispatchStrategy",
    ):
        if spec[field] != plan[field]:
            fail(f"pipelinePlan.{field}", f"does not match runSpec.{field}", "re-plan from the same immutable v2 spec")
    if plan["normalizedInputDigest"] != canonical_digest(spec):
        fail("pipelinePlan.normalizedInputDigest", "does not bind canonical runSpec", "recompute the canonical SHA-256 digest")
    effective_ceiling = min(
        spec["hostConcurrencyCeiling"],
        spec["userConcurrencyCeiling"],
        spec["calibratedConcurrencyCeiling"],
    )
    if plan["concurrency"] > effective_ceiling:
        fail("pipelinePlan.concurrency", "exceeds the host/user/calibrated ceiling", f"use concurrency <= {effective_ceiling}")
    spec_stories = spec["stories"]
    plan_stories = plan["stories"]
    if [item["id"] for item in spec_stories] != [item["storyId"] for item in plan_stories]:
        fail("pipelinePlan.stories", "does not preserve ordered runSpec story IDs", "bind every story once in specification order")
    for index, (source, planned) in enumerate(zip(spec_stories, plan_stories)):
        for source_field, plan_field in (
            ("dependsOn", "dependsOn"),
            ("writeScopes", "writeScopes"),
            ("requiredOutcomeGateIds", "requiredOutcomeGateIds"),
        ):
            if source[source_field] != planned[plan_field]:
                fail(f"pipelinePlan.stories[{index}].{plan_field}", f"does not bind runSpec.{source_field}", "copy the immutable ordered value")
        if (
            plan["executionMode"] == "parallel"
            and source["sharedState"]["mode"] == "mutates"
        ):
            fail(
                f"runSpec.stories[{index}].sharedState.mode",
                "parallel planning may not schedule shared-state mutation",
                "use read-only/none shared state or select sequential execution",
            )
    return spec, plan


def validate_rolling_state_bindings(
    pipeline_plan: Mapping[str, Any], pipeline_state: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = _normalized_nested(pipeline_plan, PIPELINE_PLAN_VERSION, validate_pipeline_plan_shape, "pipelinePlan")
    state = _normalized_nested(pipeline_state, PIPELINE_STATE_VERSION, validate_pipeline_state_shape, "pipelineState")
    if state["planDigest"] != canonical_digest(plan):
        fail("pipelineState.planDigest", "does not bind canonical pipelinePlan", "recompute the canonical plan digest")
    for state_field, plan_field in (
        ("runId", "runId"),
        ("baseSha", "baseSha"),
        ("integrationBranch", "integrationBranch"),
        ("initialIntegrationSha", "integrationExpectedSha"),
    ):
        if state[state_field] != plan[plan_field]:
            fail(f"pipelineState.{state_field}", f"does not match pipelinePlan.{plan_field}", "load state for the same immutable plan")
    if [item["storyId"] for item in state["stories"]] != [item["storyId"] for item in plan["stories"]]:
        fail("pipelineState.stories", "does not preserve planned story order", "materialize every planned story once")
    for index, (planned, current) in enumerate(zip(plan["stories"], state["stories"])):
        for field in ("integrationOrdinal", "branch"):
            if current[field] != planned[field]:
                fail(f"pipelineState.stories[{index}].{field}", f"does not match pipelinePlan.{field}", "retain the immutable planned identity")
        effective = current["blockedFromLifecycle"] if current["lifecycle"] == "blocked" else current["lifecycle"]
        if effective in {"imported-awaiting-integration", "merged-awaiting-post-check", "integration-verified"}:
            if len(current["gateEvidenceDigests"]) != len(planned["requiredOutcomeGateIds"]):
                fail(
                    f"pipelineState.stories[{index}].requiredOutcomeGateIds",
                    "does not have one ordered evidence digest for every required outcome gate",
                    "persist complete gate evidence before import or integration",
                )
    planned_by_id = {item["storyId"]: item for item in plan["stories"]}
    state_by_id = {item["storyId"]: item for item in state["stories"]}
    for planned in plan["stories"]:
        current = state_by_id[planned["storyId"]]
        effective = (
            current["blockedFromLifecycle"]
            if current["lifecycle"] == "blocked"
            else current["lifecycle"]
        )
        if effective == "never-launched":
            continue
        missing = [
            dependency
            for dependency in planned["dependsOn"]
            if state_by_id[dependency]["lifecycle"] != "integration-verified"
        ]
        if missing:
            fail(
                f"pipelineState.stories.{planned['storyId']}.dependsOn",
                f"launched before integration-verified prerequisites: {', '.join(missing)}",
                "leave the story never-launched until every prerequisite is integration-verified",
            )
    for owner in state["activeOwners"]:
        if owner["writeScopes"] != planned_by_id[owner["storyId"]]["writeScopes"]:
            fail("pipelineState.activeOwners", "writeScopes do not match the immutable plan", "copy the planned scope set")
    verified_prefix: list[dict[str, Any]] = []
    for current in state["stories"]:
        if current["lifecycle"] == "integration-verified" and len(verified_prefix) == current["integrationOrdinal"] - 1:
            verified_prefix.append(current)
        elif current["lifecycle"] == "integration-verified":
            fail("pipelineState.stories", "integration-verified stories do not form an ordinal prefix", "integrate and post-check stories in immutable ordinal order")
    expected_verified_sha = (
        verified_prefix[-1]["integrationSha"] if verified_prefix else state["initialIntegrationSha"]
    )
    if state["lastVerifiedIntegrationSha"] != expected_verified_sha:
        fail("pipelineState.lastVerifiedIntegrationSha", "does not bind the highest integration-verified ordinal", "copy the latest verified story integration SHA or the initial SHA")
    merged = [item for item in state["stories"] if item["lifecycle"] == "merged-awaiting-post-check"]
    if len(merged) > 1 or (merged and merged[0]["integrationOrdinal"] != len(verified_prefix) + 1):
        fail("pipelineState.currentIntegrationSha", "does not represent at most one next ordinal awaiting post-check", "serialize integration in immutable ordinal order")
    expected_current_sha = merged[0]["integrationSha"] if merged else expected_verified_sha
    if state["currentIntegrationSha"] != expected_current_sha:
        fail("pipelineState.currentIntegrationSha", "does not bind the current integrated or last verified SHA", "copy the pending merged SHA or lastVerifiedIntegrationSha")
    return plan, state


def validate_pipeline_event_chain(
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    raw = array(list(events), "pipelineEvents", minimum=1, maximum=MAX_PIPELINE_EVENTS)
    normalized = [
        _normalized_nested(item, PIPELINE_EVENT_VERSION, validate_pipeline_event_shape, f"pipelineEvents[{index}]")
        for index, item in enumerate(raw)
    ]
    event_ids: set[str] = set()
    run = normalized[0]["runId"]
    last_story_state: dict[str, str] = {}
    for index, event in enumerate(normalized):
        expected_sequence = index + 1
        if event["sequence"] != expected_sequence:
            fail(f"pipelineEvents[{index}].sequence", "is not contiguous", f"set it to {expected_sequence}")
        if event["runId"] != run:
            fail(f"pipelineEvents[{index}].runId", "changes within one event chain", "retain one run identity")
        if event["eventId"] in event_ids:
            fail(f"pipelineEvents[{index}].eventId", "duplicates an earlier event", "assign an immutable unique ID")
        event_ids.add(event["eventId"])
        expected_previous = None if index == 0 else canonical_digest(normalized[index - 1])
        if event["previousEventDigest"] != expected_previous:
            fail(f"pipelineEvents[{index}].previousEventDigest", "does not bind the prior canonical event", "recompute the predecessor digest")
        story_id = event["storyId"]
        if story_id is not None:
            if story_id not in last_story_state and event["stateBefore"] != "never-launched":
                fail(f"pipelineEvents[{index}].stateBefore", "does not begin from the story origin", "record the first story event from never-launched")
            if story_id in last_story_state and event["stateBefore"] != last_story_state[story_id]:
                fail(f"pipelineEvents[{index}].stateBefore", "does not continue the story's last recorded lifecycle", "use the prior stateAfter")
        if story_id is not None:
            last_story_state[story_id] = event["stateAfter"]
    return normalized


def validate_dispatch_record_bindings(
    run_spec: Mapping[str, Any],
    pipeline_plan: Mapping[str, Any],
    pipeline_state: Mapping[str, Any],
    dispatch_record: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    spec, plan = validate_rolling_plan_bindings(run_spec, pipeline_plan)
    _, state = validate_rolling_state_bindings(plan, pipeline_state)
    record = _normalized_nested(dispatch_record, DISPATCH_RECORD_VERSION, validate_dispatch_record_shape, "dispatchRecord")
    if record["runId"] != spec["runId"]:
        fail("dispatchRecord.runId", "does not match runSpec.runId", "bind the same run")
    if record["planDigest"] != canonical_digest(plan):
        fail("dispatchRecord.planDigest", "does not bind canonical pipelinePlan", "recompute the plan digest")
    spec_by_id = {item["id"]: item for item in spec["stories"]}
    plan_by_id = {item["storyId"]: item for item in plan["stories"]}
    state_by_id = {item["storyId"]: item for item in state["stories"]}
    story_id = record["storyId"]
    if story_id not in plan_by_id:
        fail("dispatchRecord.storyId", "is not present in the immutable plan", "select a planned story")
    source = spec_by_id[story_id]
    planned = plan_by_id[story_id]
    current = state_by_id[story_id]
    expected = {
        "exactModel": spec["exactModel"],
        "recommendedEffort": planned["recommendedEffort"],
        "writeScopes": planned["writeScopes"],
        "requiredOutcomeGateIds": planned["requiredOutcomeGateIds"],
        "handoffDigest": planned["handoffDigest"],
    }
    for field, expected_value in expected.items():
        if record[field] != expected_value:
            fail(f"dispatchRecord.{field}", "does not match the immutable spec/plan binding", "copy the exact planned value")
    if record["attempt"] != current["attempt"]:
        fail("dispatchRecord.attempt", "does not match the durable story attempt", "copy the immutable launch attempt")
    if record["workerStartSha"] != current["workerStartSha"]:
        fail("dispatchRecord.workerStartSha", "does not bind the immutable story launch SHA", "copy the exact workerStartSha retained for this attempt")
    if record["registeredClone"]["branch"] != planned["branch"]:
        fail("dispatchRecord.registeredClone.branch", "does not match the planned branch", "use the registered planned branch")
    if canonical_digest(record["registeredClone"]) != current["registeredCloneDigest"]:
        fail("dispatchRecord.registeredClone", "does not match the durable canonical clone identity", "bind the complete registered clone object")
    if [item["storyId"] for item in record["prerequisites"]] != source["dependsOn"]:
        fail("dispatchRecord.prerequisites", "does not bind every prerequisite in specification order", "provide exact prerequisite evidence")
    for index, prerequisite in enumerate(record["prerequisites"]):
        prerequisite_state = state_by_id[prerequisite["storyId"]]
        if prerequisite_state["lifecycle"] != "integration-verified":
            fail(
                f"dispatchRecord.prerequisites[{index}].storyId",
                "prerequisite is not integration-verified",
                "wait for durable post-check completion",
            )
        expected_evidence = {
            "workerReceiptDigest": prerequisite_state["workerReceiptDigest"],
            "integrationEvidenceDigest": prerequisite_state["postCheckEvidenceDigest"],
            "gateEvidenceDigests": prerequisite_state["gateEvidenceDigests"],
        }
        for field, expected_value in expected_evidence.items():
            if prerequisite[field] != expected_value:
                fail(
                    f"dispatchRecord.prerequisites[{index}].{field}",
                    "does not match durable prerequisite evidence",
                    "copy the exact controller-owned evidence binding",
                )
    return spec, plan, state, record


def validate_rolling_execution_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _normalized_nested(bundle, EXECUTION_BUNDLE_V2_VERSION, validate_execution_bundle_v2_shape, "executionBundle")
    spec, plan = validate_rolling_plan_bindings(normalized["runSpec"], normalized["pipelinePlan"])
    host = _normalized_nested(
        normalized["hostCapabilities"],
        "compass-builder.host-capabilities.v1",
        validate_host_shape,
        "hostCapabilities",
    )
    planning = timestamp(normalized["planningTimestamp"], "planningTimestamp")
    captured = timestamp(host["capturedAt"], "hostCapabilities.capturedAt")
    valid_until = timestamp(host["validUntil"], "hostCapabilities.validUntil")
    if planning < captured:
        fail("planningTimestamp", "precedes host capability capture", "use evidence captured no later than planning")
    if planning > valid_until:
        fail("planningTimestamp", "host capability evidence is stale", "capture fresh host evidence")
    if plan["hostEvidenceDigest"] != canonical_digest(host):
        fail("pipelinePlan.hostEvidenceDigest", "does not bind canonical hostCapabilities", "recompute the host evidence digest")
    if spec["exactModel"] != host["selectedModel"]:
        fail("hostCapabilities.selectedModel", "does not match runSpec.exactModel", "capture the selected exact model")
    for field in ("hostConcurrencyCeiling", "userConcurrencyCeiling"):
        if spec[field] != host[field]:
            fail(f"hostCapabilities.{field}", f"does not match runSpec.{field}", "plan from the same capability snapshot")
    unsupported = sorted(
        {
            story["recommendedEffort"]
            for story in plan["stories"]
            if story["recommendedEffort"] not in host["supportedEfforts"]
        }
    )
    if unsupported:
        fail("pipelinePlan.stories.recommendedEffort", f"host does not support {unsupported}", "use an effort proven by host evidence")
    if plan["executionMode"] == "parallel":
        missing_controls = sorted(
            name for name, supported in host["supports"].items() if not supported
        )
        if missing_controls:
            fail(
                "hostCapabilities.supports",
                f"parallel plan lacks required native controls: {', '.join(missing_controls)}",
                "select sequential execution or capture a capable host",
            )
    return normalized
