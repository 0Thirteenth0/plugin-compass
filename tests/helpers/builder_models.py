"""Small deterministic builders used by Compass Builder contract tests."""

from __future__ import annotations

import copy
import re


def resolved_schema_node(schema: dict, node: dict) -> dict:
    result = node
    while "$ref" in result:
        reference = result["$ref"]
        if not reference.startswith("#/"):
            raise AssertionError(f"test helper supports local references only: {reference}")
        result = schema
        for part in reference[2:].split("/"):
            result = result[part]
    return result


def schema_allows_string(schema: dict, node: dict, value: str) -> bool:
    rule = resolved_schema_node(schema, node)
    declared_type = rule.get("type")
    if declared_type is not None:
        allowed = {declared_type} if isinstance(declared_type, str) else set(declared_type)
        if "string" not in allowed:
            return False
    if len(value) < rule.get("minLength", 0) or len(value) > rule.get("maxLength", len(value)):
        return False
    pattern = rule.get("pattern")
    return pattern is None or re.search(re.compile(pattern), value) is not None


def state_entry(story_id: str, branch: str, integration: str, pre_merge: str, merge: str | None = None, checked: bool = False) -> dict:
    return {
        "storyId": story_id, "branch": branch, "workerState": "complete",
        "verificationState": "verified", "integrationState": integration,
        "preMergeExpectedSha": pre_merge, "mergeSha": merge,
        "controllerCheckDigest": "sha256:" + "d" * 64 if checked else None,
        "postCheckExpectedSha": merge if checked else None,
    }


def pending_state_entry(story_id: str, branch: str, expected: str) -> dict:
    return {
        "storyId": story_id, "branch": branch, "workerState": "pending",
        "verificationState": "pending", "integrationState": "pending",
        "preMergeExpectedSha": expected, "mergeSha": None,
        "controllerCheckDigest": None, "postCheckExpectedSha": None,
    }


def passing_resume(state: dict, passing_digest: str) -> dict:
    value = copy.deepcopy(state)
    branches = value["waves"][value["currentWaveIndex"]]["branches"]
    target = next(index for index, entry in enumerate(branches) if entry["integrationState"] == "merged")
    merge_sha = branches[target]["mergeSha"]
    branches[target].update({
        "integrationState": "integration-verified", "controllerCheckDigest": passing_digest,
        "postCheckExpectedSha": merge_sha,
    })
    if target + 1 < len(branches):
        branches[target + 1]["preMergeExpectedSha"] = merge_sha
    value.update({
        "previousState": "wave-integrated-unverified",
        "state": "wave-merging" if target + 1 < len(branches) else "wave-verified",
        "lastVerifiedIntegrationSha": merge_sha,
    })
    return value


def state_snapshot(base: dict, previous: str, state: str, entries: list[dict], expected: str, verified: str) -> dict:
    value = copy.deepcopy(base)
    value.update({
        "previousState": previous, "state": state,
        "expectedIntegrationSha": expected, "lastVerifiedIntegrationSha": verified,
    })
    value["waves"][0]["branches"] = entries
    return value


def three_branch_advance(base: dict) -> tuple[dict, dict]:
    initial, first_merge, second_merge = "a" * 40, "c" * 40, "e" * 40
    before = state_snapshot(base, "wave-merging", "wave-integrated-unverified", [
        state_entry("alpha", "cb/run/alpha", "integration-verified", initial, first_merge, True),
        state_entry("beta", "cb/run/beta", "merged", first_merge, second_merge),
        state_entry("gamma", "cb/run/gamma", "worker-verified", initial),
    ], second_merge, first_merge)
    after = state_snapshot(base, "wave-integrated-unverified", "wave-merging", [
        state_entry("alpha", "cb/run/alpha", "integration-verified", initial, first_merge, True),
        state_entry("beta", "cb/run/beta", "integration-verified", first_merge, second_merge, True),
        state_entry("gamma", "cb/run/gamma", "worker-verified", second_merge),
    ], second_merge, second_merge)
    return before, after
