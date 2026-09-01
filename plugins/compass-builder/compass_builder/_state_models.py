"""Durable run-state, blocker-history, and integration-HEAD validation."""

from __future__ import annotations

from typing import Any

from ._limits import MAX_BLOCKERS, MAX_BRANCHES_PER_WAVE, MAX_WAVES
from ._validation import array, branch, digest, enum, fail, identifier, integer, object_, run_id, sha, string


ACTIVE_STATES = {
    "planned", "dispatching", "wave-workers-complete", "wave-merging",
    "wave-integrated-unverified", "wave-verified",
}
RUN_STATES = ACTIVE_STATES | {"completed", "blocked"}
TRANSITIONS = {
    None: {"planned"},
    "planned": {"dispatching", "blocked"},
    "dispatching": {"wave-workers-complete", "blocked"},
    "wave-workers-complete": {"wave-merging", "blocked"},
    "wave-merging": {"wave-integrated-unverified", "blocked"},
    "wave-integrated-unverified": {"wave-merging", "wave-verified", "blocked"},
    "wave-verified": {"dispatching", "completed", "blocked"},
    "completed": set(),
    "blocked": ACTIVE_STATES,
}
BLOCKER_PHASES = {
    "pre-dispatch", "dispatch", "worker", "verification", "pre-merge",
    "post-merge-check", "controller",
}
STORY_PHASES = {"worker", "verification", "pre-merge", "post-merge-check"}
PHASE_STATE = {
    "pre-dispatch": "planned",
    "dispatch": "dispatching",
    "worker": "dispatching",
    "verification": "wave-workers-complete",
    "pre-merge": "wave-merging",
    "post-merge-check": "wave-integrated-unverified",
}
ENTRY_COMBINATIONS = {
    ("pending", "pending", "pending"),
    ("running", "pending", "pending"),
    ("complete", "pending", "pending"),
    ("complete", "verified", "worker-verified"),
    ("complete", "verified", "merged"),
    ("complete", "verified", "integration-verified"),
    ("complete", "verified", "blocked"),
    ("complete", "failed", "blocked"),
    ("blocked", "pending", "blocked"),
    ("blocked", "failed", "blocked"),
}


def _blocker(value: Any, path: str) -> dict[str, Any]:
    record = object_(value, path, {
        "blockerId", "blockedFromState", "phase", "storyId", "reason",
        "evidenceDigest", "resumeState",
    })
    identifier(record["blockerId"], f"{path}.blockerId")
    blocked_from = enum(record["blockedFromState"], f"{path}.blockedFromState", ACTIVE_STATES)
    phase = enum(record["phase"], f"{path}.phase", BLOCKER_PHASES)
    story_id = record["storyId"]
    if story_id is not None:
        identifier(story_id, f"{path}.storyId")
    string(record["reason"], f"{path}.reason", maximum=2000)
    digest(record["evidenceDigest"], f"{path}.evidenceDigest")
    resume = enum(record["resumeState"], f"{path}.resumeState", ACTIVE_STATES)
    if phase == "controller":
        if resume != blocked_from:
            fail(f"{path}.resumeState", "controller blockers must resume their blockedFromState", "resume the exact interrupted controller phase")
    else:
        expected = PHASE_STATE[phase]
        if blocked_from != expected or resume != expected:
            fail(path, f"phase {phase!r} is incoherent with blocked/resume states", f"set blockedFromState and resumeState to {expected!r}")
    if phase in STORY_PHASES and story_id is None:
        fail(f"{path}.storyId", f"is required for {phase} blockers", "record the affected current-wave story ID")
    if phase == "pre-dispatch" and story_id is not None:
        fail(f"{path}.storyId", "must be null for a pre-dispatch blocker", "clear the story ID")
    return record


def _state_invariants(state: str, entries: list[dict[str, Any]], current: int) -> None:
    if state == "planned":
        if current != 0 or any((entry["workerState"], entry["verificationState"], entry["integrationState"]) != ("pending", "pending", "pending") for entry in entries):
            fail("$.state", "planned must contain only the first all-pending wave", "initialize one pending wave without worker evidence")
    elif state == "dispatching":
        if any(entry["verificationState"] != "pending" or entry["integrationState"] != "pending" for entry in entries) or all(entry["workerState"] == "complete" for entry in entries):
            fail("$.state", "dispatching must have an unfinished worker and no verification/integration progress", "keep dispatching while at least one worker is pending or running")
    elif state == "wave-workers-complete":
        if any((entry["workerState"], entry["verificationState"], entry["integrationState"]) != ("complete", "pending", "pending") for entry in entries):
            fail("$.state", "wave-workers-complete must contain only completed unverified workers", "finish every worker before controller verification")
    elif state == "wave-merging":
        states = [entry["integrationState"] for entry in entries]
        if any(entry["workerState"] != "complete" or entry["verificationState"] != "verified" for entry in entries):
            fail("$.state", "wave-merging contains unverified worker work", "verify every worker before entering the merge loop")
        prefix = 0
        while prefix < len(states) and states[prefix] == "integration-verified":
            prefix += 1
        if prefix == len(states) or any(item != "worker-verified" for item in states[prefix:]):
            fail("$.state", "wave-merging must contain a verified prefix and a worker-verified suffix", "verify the prior merge, then resume at the next branch")
    elif state == "wave-integrated-unverified":
        states = [entry["integrationState"] for entry in entries]
        merged = [index for index, item in enumerate(states) if item == "merged"]
        if len(merged) != 1:
            fail("$.state", "wave-integrated-unverified must contain exactly one unchecked merge", "record the just-merged branch once")
        merge_index = merged[0]
        if any(item != "integration-verified" for item in states[:merge_index]) or any(item != "worker-verified" for item in states[merge_index + 1:]):
            fail("$.state", "wave-integrated-unverified must contain a verified prefix, one merged branch, and a worker-verified suffix", "preserve later branches while checking the just-merged branch")
    elif state in {"wave-verified", "completed"} and any(entry["integrationState"] != "integration-verified" for entry in entries):
        fail("$.state", f"{state} has unverified integrations", "verify every current-wave branch first")


def _blocked_invariants(blocker: dict[str, Any], entries: list[dict[str, Any]], current: int) -> None:
    phase = blocker["phase"]
    story_id = blocker["storyId"]
    if phase in {"controller", "pre-dispatch"} or (phase == "dispatch" and story_id is None):
        _state_invariants(blocker["blockedFromState"], entries, current)
        return
    matches = [index for index, entry in enumerate(entries) if entry["storyId"] == story_id]
    if len(matches) != 1:
        fail("$.activeBlocker.storyId", "does not identify exactly one current-wave ledger entry", "use the affected current-wave story ID")
    entry_index = matches[0]
    entry = entries[entry_index]
    if phase in {"dispatch", "worker"}:
        expected = ("blocked", "pending", "blocked")
    elif phase == "verification":
        expected = ("complete", "failed", "blocked")
    elif phase == "pre-merge":
        expected = ("complete", "verified", "blocked")
    else:
        expected = ("complete", "verified", "blocked")
    actual = (entry["workerState"], entry["verificationState"], entry["integrationState"])
    if actual != expected:
        fail("$.activeBlocker.phase", f"{phase} blocker conflicts with ledger state {actual}", f"record ledger state {expected}")
    if phase in {"dispatch", "worker"}:
        if any(other["verificationState"] != "pending" or other["integrationState"] not in {"pending", "blocked"} for other in entries):
            fail("$.activeBlocker.phase", "worker blocker contains later verification/integration progress", "retain only dispatch-phase worker evidence")
    elif phase == "verification":
        if any(other["workerState"] != "complete" or (index != entry_index and (other["verificationState"], other["integrationState"]) != ("pending", "pending")) for index, other in enumerate(entries)):
            fail("$.activeBlocker.phase", "verification blocker conflicts with current-wave worker evidence", "retain completed workers and only the failed story verification")
    elif phase == "pre-merge":
        states = [other["integrationState"] for other in entries]
        if any(other["workerState"] != "complete" or other["verificationState"] != "verified" for other in entries) or any(item != "integration-verified" for item in states[:entry_index]) or any(item != "worker-verified" for item in states[entry_index + 1:]):
            fail("$.activeBlocker.phase", "pre-merge blocker must interrupt a verified-prefix/worker-verified-suffix ledger", "retain one blocked merge target between the verified prefix and untouched suffix")
    elif phase == "post-merge-check":
        if entry["mergeSha"] is None or entry["controllerCheckDigest"] is None or entry["postCheckExpectedSha"] is not None:
            fail("$.activeBlocker.phase", "post-merge-check blocker lacks retained merge/check evidence", "retain mergeSha and controllerCheckDigest with null postCheckExpectedSha")
        if entry["controllerCheckDigest"] != blocker["evidenceDigest"]:
            fail("$.activeBlocker.evidenceDigest", "does not equal the failed controllerCheckDigest", "bind blocker history to the exact failed check evidence")
        states = [other["integrationState"] for other in entries]
        if any(item != "integration-verified" for item in states[:entry_index]) or any(item != "worker-verified" for item in states[entry_index + 1:]):
            fail("$.activeBlocker.phase", "post-merge-check blocker must retain a verified prefix and worker-verified suffix", "block only the just-merged branch while retaining its merge evidence")


def validate_run_state_shape(data: dict[str, Any]) -> None:
    object_(data, "$", {
        "schemaVersion", "runId", "baseSha", "integrationBranch", "initialIntegrationSha",
        "expectedIntegrationSha", "lastVerifiedIntegrationSha", "runBindingDigest",
        "previousState", "state", "currentWaveIndex", "activeBlocker", "blockerHistory", "waves",
    })
    run_id(data["runId"], "$.runId")
    sha(data["baseSha"], "$.baseSha")
    branch(data["integrationBranch"], "$.integrationBranch")
    sha(data["initialIntegrationSha"], "$.initialIntegrationSha")
    sha(data["expectedIntegrationSha"], "$.expectedIntegrationSha")
    sha(data["lastVerifiedIntegrationSha"], "$.lastVerifiedIntegrationSha")
    digest(data["runBindingDigest"], "$.runBindingDigest")
    previous = data["previousState"]
    if previous is not None:
        enum(previous, "$.previousState", RUN_STATES)
    state = enum(data["state"], "$.state", RUN_STATES)

    history = [_blocker(item, f"$.blockerHistory[{index}]") for index, item in enumerate(array(data["blockerHistory"], "$.blockerHistory", maximum=MAX_BLOCKERS))]
    blocker_ids = [item["blockerId"] for item in history]
    if len(set(blocker_ids)) != len(blocker_ids):
        fail("$.blockerHistory", "contains duplicate blocker IDs", "append each stable blocker record once")
    active = data["activeBlocker"]
    if active is not None:
        active = _blocker(active, "$.activeBlocker")
    if state == "blocked":
        if not history or active != history[-1]:
            fail("$.activeBlocker", "blocked requires the exact last blockerHistory record", "append the blocker and copy that closed record to activeBlocker")
        if previous != active["blockedFromState"]:
            fail("$.previousState", "does not match activeBlocker.blockedFromState", "record the phase interrupted by this blocker")
    elif active is not None:
        fail("$.activeBlocker", "must be null outside blocked state", "clear the active blocker while retaining blockerHistory")
    if previous == "blocked":
        if not history or state != history[-1]["resumeState"]:
            fail("$.state", "does not match the last blocker resumeState", "resume only the explicitly recorded target state")
    elif state not in TRANSITIONS.get(previous, set()):
        fail("$.state", f"undeclared transition {previous!r} -> {state!r}", "use the documented sequence, a blocker, or its recorded resume target")
    if previous is None and history:
        fail("$.blockerHistory", "initial planned state cannot contain blocker history", "start with an empty history")

    waves = array(data["waves"], "$.waves", minimum=1, maximum=MAX_WAVES)
    current = integer(data["currentWaveIndex"], "$.currentWaveIndex")
    if current != len(waves) - 1:
        fail("$.currentWaveIndex", "must identify the last materialized wave ledger", "append ledgers only when a wave becomes current")

    verified_head = data["initialIntegrationSha"]
    actual_head = verified_head
    parsed_waves: list[list[dict[str, Any]]] = []
    failed_checks: dict[str, set[str]] = {}
    for item in history:
        if item["phase"] == "post-merge-check":
            failed_checks.setdefault(item["storyId"], set()).add(item["evidenceDigest"])
    all_failed_checks = set().union(*failed_checks.values()) if failed_checks else set()
    active_post_merge_story = (
        active["storyId"]
        if state == "blocked" and active["phase"] == "post-merge-check"
        else None
    )
    for wave_index, wave in enumerate(waves):
        path = f"$.waves[{wave_index}]"
        object_(wave, path, {"waveIndex", "startExpectedSha", "branches"})
        if integer(wave["waveIndex"], f"{path}.waveIndex") != wave_index:
            fail(f"{path}.waveIndex", "does not match ordered position", f"set it to {wave_index}")
        sha(wave["startExpectedSha"], f"{path}.startExpectedSha")
        if wave["startExpectedSha"] != verified_head:
            fail(f"{path}.startExpectedSha", "breaks the verified wave SHA chain", "use the prior verified post-check SHA")
        entries = array(wave["branches"], f"{path}.branches", minimum=1, maximum=MAX_BRANCHES_PER_WAVE)
        story_ids: list[str] = []
        prior_integrated = True
        known_wave_heads = {wave["startExpectedSha"]}
        for branch_index, entry in enumerate(entries):
            entry_path = f"{path}.branches[{branch_index}]"
            object_(entry, entry_path, {
                "storyId", "branch", "workerState", "verificationState", "integrationState",
                "preMergeExpectedSha", "mergeSha", "controllerCheckDigest", "postCheckExpectedSha",
            })
            story_id = identifier(entry["storyId"], f"{entry_path}.storyId")
            story_ids.append(story_id)
            branch(entry["branch"], f"{entry_path}.branch")
            worker = enum(entry["workerState"], f"{entry_path}.workerState", {"pending", "running", "complete", "blocked"})
            verification = enum(entry["verificationState"], f"{entry_path}.verificationState", {"pending", "verified", "failed"})
            integration = enum(entry["integrationState"], f"{entry_path}.integrationState", {"pending", "worker-verified", "merged", "integration-verified", "blocked"})
            if (worker, verification, integration) not in ENTRY_COMBINATIONS:
                fail(entry_path, f"incoherent worker/verification/integration combination {worker}/{verification}/{integration}", "use a declared monotonic branch-ledger combination")
            pre_merge = sha(entry["preMergeExpectedSha"], f"{entry_path}.preMergeExpectedSha")
            merge = sha(entry["mergeSha"], f"{entry_path}.mergeSha", nullable=True)
            check = digest(entry["controllerCheckDigest"], f"{entry_path}.controllerCheckDigest", nullable=True)
            post_check = sha(entry["postCheckExpectedSha"], f"{entry_path}.postCheckExpectedSha", nullable=True)
            retained_blocked_merge = (
                integration == "blocked" and story_id == active_post_merge_story
            )
            cas_is_current = prior_integrated or integration in {"merged", "integration-verified"} or retained_blocked_merge
            if cas_is_current and pre_merge != verified_head:
                direction = "use the preceding verified post-check SHA for the current/verified branch; later suffix entries advance only when next"
                fail(f"{entry_path}.preMergeExpectedSha", "breaks the verified branch SHA chain", direction)
            if not cas_is_current and pre_merge not in known_wave_heads:
                fail(f"{entry_path}.preMergeExpectedSha", "is not a retained verified wave head", "retain wave-start or a previously verified branch SHA until this branch becomes next")
            if integration in {"pending", "worker-verified"} or (integration == "blocked" and not retained_blocked_merge):
                if any(value is not None for value in (merge, check, post_check)):
                    fail(entry_path, "contains merge/check evidence before a merge", "leave merge and controller evidence null")
            if retained_blocked_merge:
                if merge is None or check is None or post_check is not None:
                    fail(entry_path, "post-merge blocker lacks retained merge/check evidence", "retain mergeSha and controllerCheckDigest with null postCheckExpectedSha")
                actual_head = merge
            if integration == "merged":
                if merge is None or post_check is not None:
                    fail(entry_path, "merged requires mergeSha and no verified post-check SHA", "retain mergeSha, optional failed-check digest, and null postCheckExpectedSha")
                if check is not None and check not in failed_checks.get(story_id, set()):
                    fail(f"{entry_path}.controllerCheckDigest", "has failed-check evidence absent from blocker history", "append a post-merge-check blocker whose evidenceDigest exactly matches")
                actual_head = merge
            if integration == "integration-verified":
                if merge is None or check is None or post_check is None:
                    fail(entry_path, "integration-verified lacks merge or controller-check evidence", "record mergeSha, controllerCheckDigest, and postCheckExpectedSha")
                if post_check != merge:
                    fail(f"{entry_path}.postCheckExpectedSha", "must equal mergeSha after a clean non-mutating controller check", "record the verified merge head unchanged")
                if check in all_failed_checks:
                    fail(f"{entry_path}.controllerCheckDigest", "reuses known failed controller-check evidence as passing", "record fresh passing check evidence distinct from blocker history")
                verified_head = post_check
                actual_head = post_check
                known_wave_heads.add(post_check)
            if not prior_integrated and integration in {"merged", "integration-verified"}:
                fail(f"{entry_path}.integrationState", "advances before the preceding branch is integration-verified", "integrate branches serially in ledger order")
            prior_integrated = integration == "integration-verified"
        if len(set(story_ids)) != len(story_ids):
            fail(f"{path}.branches", "contains duplicate story IDs", "record each branch once")
        if wave_index < current and any(entry["integrationState"] != "integration-verified" for entry in entries):
            fail(path, "a prior wave is not fully integration-verified", "finish every prior ledger entry before materializing the next wave")
        parsed_waves.append(entries)

    if data["expectedIntegrationSha"] != actual_head:
        fail("$.expectedIntegrationSha", "does not equal the actual integration CAS HEAD", "record the merged HEAD even while controller verification is pending")
    if data["lastVerifiedIntegrationSha"] != verified_head:
        fail("$.lastVerifiedIntegrationSha", "does not equal the last clean controller-verified HEAD", "retain the prior verified SHA until post-merge checks pass")
    current_entries = parsed_waves[current]
    if state == "blocked":
        _blocked_invariants(active, current_entries, current)
    else:
        _state_invariants(state, current_entries, current)
    if state == "completed":
        for entries in parsed_waves:
            if any(entry["workerState"] != "complete" or entry["verificationState"] != "verified" or entry["integrationState"] != "integration-verified" for entry in entries):
                fail("$.state", "completed contains blocked or incomplete ledger states", "complete and verify every worker and integration")


STATUS_FIELDS = ("workerState", "verificationState", "integrationState")
SHA_FIELDS = ("preMergeExpectedSha", "mergeSha", "postCheckExpectedSha")


def _target_status_change(
    before: dict[str, Any], after: dict[str, Any], phase: str, entering: bool, path: str
) -> None:
    old = tuple(before[field] for field in STATUS_FIELDS)
    new = tuple(after[field] for field in STATUS_FIELDS)
    if entering:
        allowed = {
            "verification": (("complete", "pending", "pending"), ("complete", "failed", "blocked")),
            "pre-merge": (("complete", "verified", "worker-verified"), ("complete", "verified", "blocked")),
            "post-merge-check": (("complete", "verified", "merged"), ("complete", "verified", "blocked")),
        }
        valid = (
            phase in {"dispatch", "worker"}
            and old in {("pending", "pending", "pending"), ("running", "pending", "pending")}
            and new == ("blocked", "pending", "blocked")
        ) or (phase in allowed and (old, new) == allowed[phase])
    else:
        allowed = {
            "verification": (("complete", "failed", "blocked"), ("complete", "pending", "pending")),
            "pre-merge": (("complete", "verified", "blocked"), ("complete", "verified", "worker-verified")),
            "post-merge-check": (("complete", "verified", "blocked"), ("complete", "verified", "merged")),
        }
        valid = (
            phase in {"dispatch", "worker"}
            and old == ("blocked", "pending", "blocked")
            and new in {("pending", "pending", "pending"), ("running", "pending", "pending")}
        ) or (phase in allowed and (old, new) == allowed[phase])
    if not valid:
        direction = "apply only the phase-specific monotonic blocked/resume status change"
        fail(path, f"rewrites status fields outside the {phase!r} blocker transition", direction)


def validate_transition_evidence(previous: dict[str, Any], current: dict[str, Any]) -> None:
    """Protect durable ledger evidence across a normalized state transition."""
    entering = current["state"] == "blocked"
    exiting = previous["state"] == "blocked"
    if entering or exiting:
        for field in ("currentWaveIndex", "expectedIntegrationSha", "lastVerifiedIntegrationSha"):
            if current[field] != previous[field]:
                fail(f"currentState.{field}", "changed while entering or resuming a blocker", "preserve the exact durable HEAD evidence across blocking")
        if len(current["waves"]) != len(previous["waves"]):
            fail("currentState.waves", "changed wave accounting while entering or resuming a blocker", "preserve every materialized wave exactly")
        blocker = current["activeBlocker"] if entering else previous["blockerHistory"][-1]
        phase = blocker["phase"]
        target_story = blocker["storyId"] if phase not in {"controller", "pre-dispatch"} else None
        target_seen = target_story is None
        for wave_index, (old_wave, new_wave) in enumerate(zip(previous["waves"], current["waves"])):
            wave_path = f"currentState.waves[{wave_index}]"
            if new_wave["startExpectedSha"] != old_wave["startExpectedSha"]:
                fail(f"{wave_path}.startExpectedSha", "rewrites the blocked transition SHA chain", "preserve the exact wave start SHA")
            if len(new_wave["branches"]) != len(old_wave["branches"]):
                fail(f"{wave_path}.branches", "changes branch accounting while blocked", "preserve every branch in order")
            for branch_index, (before, after) in enumerate(zip(old_wave["branches"], new_wave["branches"])):
                path = f"{wave_path}.branches[{branch_index}]"
                if (after["storyId"], after["branch"]) != (before["storyId"], before["branch"]):
                    fail(path, "rewrites immutable branch identity while blocked", "preserve the exact story and branch")
                for field in SHA_FIELDS:
                    if after[field] != before[field]:
                        fail(f"{path}.{field}", "rewrites durable SHA-chain evidence while blocked", "preserve the exact recorded SHA")
                is_target = (
                    target_story is not None
                    and wave_index == previous["currentWaveIndex"]
                    and before["storyId"] == target_story
                )
                if not is_target:
                    if after != before:
                        fail(path, "rewrites an unaffected branch while blocked", "preserve every unaffected branch field exactly")
                    continue
                target_seen = True
                if phase == "post-merge-check":
                    if not entering and after["controllerCheckDigest"] != before["controllerCheckDigest"]:
                        fail(f"{path}.controllerCheckDigest", "rewrites the retained failed-check digest on resume", "rerun from merged while retaining the attempted check digest")
                    if entering and after["controllerCheckDigest"] is None:
                        fail(f"{path}.controllerCheckDigest", "does not record the attempted post-merge check", "record the failed controller-check evidence")
                elif after["controllerCheckDigest"] != before["controllerCheckDigest"]:
                    fail(f"{path}.controllerCheckDigest", "rewrites check evidence for a non-check blocker", "preserve controller-check evidence exactly")
                _target_status_change(before, after, phase, entering, path)
        if not target_seen:
            fail("currentState.blockerHistory", "target story is absent from the current wave", "retain the affected branch in the current ledger")
        return

    pair = (previous["state"], current["state"])
    appending = pair == ("wave-verified", "dispatching")
    expected_wave_count = len(previous["waves"]) + (1 if appending else 0)
    expected_index = previous["currentWaveIndex"] + (1 if appending else 0)
    if len(current["waves"]) != expected_wave_count or current["currentWaveIndex"] != expected_index:
        direction = "append exactly one initial tail wave only for wave-verified -> dispatching"
        fail("currentState.waves", "changes wave count/current index outside the declared next-wave transition", direction)
    next_pre_merge_advance: tuple[int, int] | None = None
    verification_target: tuple[int, int] | None = None
    if pair == ("wave-integrated-unverified", "wave-merging"):
        current_wave = previous["currentWaveIndex"]
        entries = previous["waves"][current_wave]["branches"]
        merged = [index for index, entry in enumerate(entries) if entry["integrationState"] == "merged"]
        if len(merged) == 1 and merged[0] + 1 < len(entries):
            next_pre_merge_advance = (current_wave, merged[0] + 1)
    if pair in {("wave-integrated-unverified", "wave-merging"), ("wave-integrated-unverified", "wave-verified")}:
        current_wave = previous["currentWaveIndex"]
        merged = [index for index, entry in enumerate(previous["waves"][current_wave]["branches"]) if entry["integrationState"] == "merged"]
        if len(merged) == 1:
            verification_target = (current_wave, merged[0])

    for wave_index, old_wave in enumerate(previous["waves"]):
        new_wave = current["waves"][wave_index]
        path = f"currentState.waves[{wave_index}]"
        if new_wave["startExpectedSha"] != old_wave["startExpectedSha"]:
            fail(f"{path}.startExpectedSha", "rewrites a historical wave SHA", "preserve the original wave start")
        if len(new_wave["branches"]) != len(old_wave["branches"]):
            fail(f"{path}.branches", "rewrites historical branch accounting", "retain the existing ordered ledger")
        for branch_index, (before, after) in enumerate(zip(old_wave["branches"], new_wave["branches"])):
            branch_path = f"{path}.branches[{branch_index}]"
            if (after["storyId"], after["branch"]) != (before["storyId"], before["branch"]):
                fail(branch_path, "rewrites historical branch identity", "retain the existing story and branch")
            if after["preMergeExpectedSha"] != before["preMergeExpectedSha"]:
                permitted_advance = (
                    next_pre_merge_advance == (wave_index, branch_index)
                    and after["preMergeExpectedSha"] == current["lastVerifiedIntegrationSha"]
                    and before["integrationState"] == after["integrationState"] == "worker-verified"
                )
                if not permitted_advance:
                    direction = "preserve it, except advance only the immediately next worker-verified branch after verifying its predecessor"
                    fail(f"{branch_path}.preMergeExpectedSha", "rewrites branch CAS evidence outside the next-branch advance", direction)
            for field in ("mergeSha", "controllerCheckDigest", "postCheckExpectedSha"):
                if before[field] is not None and after[field] != before[field]:
                    fresh_check = field == "controllerCheckDigest" and verification_target == (wave_index, branch_index) and after[field] is not None
                    if not fresh_check:
                        fail(f"{branch_path}.{field}", "rewrites already-recorded evidence", "preserve every non-null evidence value")

    if appending:
        if current["expectedIntegrationSha"] != previous["expectedIntegrationSha"] or current["lastVerifiedIntegrationSha"] != previous["lastVerifiedIntegrationSha"]:
            fail("currentState.expectedIntegrationSha", "changes the verified head while opening a next wave", "carry the prior verified head forward unchanged")
        if previous["waves"] != current["waves"][:-1]:
            fail("currentState.waves", "rewrites a prior wave while appending", "append one new tail without changing existing ledgers")
        tail = current["waves"][-1]
        if tail["startExpectedSha"] != previous["expectedIntegrationSha"]:
            fail("currentState.waves[-1].startExpectedSha", "does not start at the prior verified head", "bind the new tail to the prior actual/verified SHA")
        for index, entry in enumerate(tail["branches"]):
            if tuple(entry[field] for field in STATUS_FIELDS) != ("pending", "pending", "pending") or any(entry[field] is not None for field in ("mergeSha", "controllerCheckDigest", "postCheckExpectedSha")):
                fail(f"currentState.waves[-1].branches[{index}]", "is not an initial pending branch", "append only pending/pending/pending entries without merge or check evidence")
        return

    old_entries = previous["waves"][previous["currentWaveIndex"]]["branches"]
    new_entries = current["waves"][current["currentWaveIndex"]]["branches"]

    def exact_except(before: dict[str, Any], after: dict[str, Any], fields: set[str], path: str) -> None:
        changed = {field for field in before if before[field] != after[field]}
        if not changed <= fields:
            fail(path, f"rewrites fields outside the {pair[0]} -> {pair[1]} phase: {sorted(changed - fields)}", "change only the declared monotonic phase fields")

    if pair == ("planned", "dispatching"):
        changed = 0
        for index, (before, after) in enumerate(zip(old_entries, new_entries)):
            exact_except(before, after, {"workerState"}, f"currentState.waves[-1].branches[{index}]")
            if before["workerState"] != "pending" or after["workerState"] not in {"pending", "running"}:
                fail("currentState.waves[-1].branches", "contains a non-start worker change", "change only pending workers to running")
            changed += after["workerState"] == "running"
        if not changed:
            fail("currentState.waves[-1].branches", "starts no worker", "mark at least one pending worker running")
    elif pair == ("dispatching", "wave-workers-complete"):
        for index, (before, after) in enumerate(zip(old_entries, new_entries)):
            exact_except(before, after, {"workerState"}, f"currentState.waves[-1].branches[{index}]")
            if before["workerState"] not in {"pending", "running", "complete"} or after["workerState"] != "complete":
                fail("currentState.waves[-1].branches", "contains a non-completion worker change", "finish every dispatched worker")
    elif pair == ("wave-workers-complete", "wave-merging"):
        for index, (before, after) in enumerate(zip(old_entries, new_entries)):
            exact_except(before, after, {"verificationState", "integrationState"}, f"currentState.waves[-1].branches[{index}]")
            if tuple(before[field] for field in STATUS_FIELDS) != ("complete", "pending", "pending") or tuple(after[field] for field in STATUS_FIELDS) != ("complete", "verified", "worker-verified"):
                fail("currentState.waves[-1].branches", "contains a non-verification change", "verify every completed worker before merging")
    elif pair == ("wave-merging", "wave-integrated-unverified"):
        changed = []
        for index, (before, after) in enumerate(zip(old_entries, new_entries)):
            if before != after:
                changed.append((index, before, after))
        if len(changed) != 1:
            fail("currentState.waves[-1].branches", "does not record exactly one next merge", "change only the next worker-verified branch")
        index, before, after = changed[0]
        exact_except(before, after, {"integrationState", "mergeSha"}, f"currentState.waves[-1].branches[{index}]")
        if before["integrationState"] != "worker-verified" or after["integrationState"] != "merged" or before["mergeSha"] is not None or after["mergeSha"] is None:
            fail("currentState.waves[-1].branches", "contains an invalid merge delta", "add one merge SHA to the next worker-verified branch")
        if current["expectedIntegrationSha"] != after["mergeSha"] or current["lastVerifiedIntegrationSha"] != previous["lastVerifiedIntegrationSha"]:
            fail("currentState.expectedIntegrationSha", "does not advance only the actual head to the new merge", "set actual head to mergeSha and retain the verified head")
    elif pair in {("wave-integrated-unverified", "wave-merging"), ("wave-integrated-unverified", "wave-verified")}:
        merged = [index for index, entry in enumerate(old_entries) if entry["integrationState"] == "merged"]
        if len(merged) != 1:
            fail("previousState.waves[-1].branches", "does not identify one pending controller verification", "resume from exactly one merged branch")
        target = merged[0]
        for index, (before, after) in enumerate(zip(old_entries, new_entries)):
            if index == target + 1 and pair[1] == "wave-merging":
                exact_except(before, after, {"preMergeExpectedSha"}, f"currentState.waves[-1].branches[{index}]")
            elif index != target and after != before:
                direction = "change only the merged branch and the immediate next branch's preMergeExpectedSha"
                fail(f"currentState.waves[-1].branches[{index}]", "rewrites a non-target branch during controller verification", direction)
        before, after = old_entries[target], new_entries[target]
        exact_except(before, after, {"integrationState", "controllerCheckDigest", "postCheckExpectedSha"}, f"currentState.waves[-1].branches[{target}]")
        if after["integrationState"] != "integration-verified" or after["controllerCheckDigest"] is None or after["postCheckExpectedSha"] != before["mergeSha"]:
            fail("currentState.waves[-1].branches", "contains an invalid controller-verification delta", "verify the merged branch without changing its merge SHA")
        known_failed = {
            item["evidenceDigest"] for item in previous["blockerHistory"]
            if item["phase"] == "post-merge-check"
        }
        if after["controllerCheckDigest"] in known_failed:
            fail(f"currentState.waves[-1].branches[{target}].controllerCheckDigest", "reuses known failed evidence", "record a fresh passing controller-check digest")
        if current["expectedIntegrationSha"] != previous["expectedIntegrationSha"] or current["lastVerifiedIntegrationSha"] != before["mergeSha"]:
            fail("currentState.lastVerifiedIntegrationSha", "does not advance only the verified head", "retain actual head and advance verified head to mergeSha")
    elif pair == ("wave-verified", "completed"):
        if current["waves"] != previous["waves"] or current["expectedIntegrationSha"] != previous["expectedIntegrationSha"] or current["lastVerifiedIntegrationSha"] != previous["lastVerifiedIntegrationSha"]:
            fail("currentState", "rewrites evidence while marking completed", "change only the state labels")
    else:
        fail("currentState.state", f"has no phase-aware transition contract for {pair}", "use a documented state transition")
