"""Worker receipt validation."""

from __future__ import annotations

from typing import Any

from ._limits import MAX_CHANGED_FILES, MAX_CHECKS
from ._validation import EFFORTS, array, branch, digest, enum, fail, identifier, integer, object_, run_id, scope, sha, string


def validate_worker_receipt_shape(data: dict[str, Any]) -> None:
    object_(data, "$", {
        "schemaVersion", "runId", "storyId", "branch", "worktree", "exactModel", "effort",
        "baseSha", "headSha", "commitSha", "changedFiles", "checks", "elapsedMs", "status", "blocker",
    })
    run_id(data["runId"], "$.runId")
    identifier(data["storyId"], "$.storyId")
    branch(data["branch"], "$.branch")
    string(data["worktree"], "$.worktree", maximum=1024)
    if string(data["exactModel"], "$.exactModel", maximum=160) == "inherit":
        fail("$.exactModel", "must name an exact model", "record the launch model ID")
    enum(data["effort"], "$.effort", EFFORTS)
    sha(data["baseSha"], "$.baseSha")
    head_sha = sha(data["headSha"], "$.headSha", nullable=True)
    commit_sha = sha(data["commitSha"], "$.commitSha", nullable=True)
    if head_sha != commit_sha:
        fail("$.commitSha", "does not match worker headSha", "record the exact one-commit worker head")
    changed_paths: list[str] = []
    changed_files = array(data["changedFiles"], "$.changedFiles", maximum=MAX_CHANGED_FILES)
    for index, item in enumerate(changed_files):
        path = f"$.changedFiles[{index}]"
        object_(item, path, {"path", "sourcePath", "changeType"})
        changed_paths.append(scope(item["path"], f"{path}.path"))
        if item["sourcePath"] is not None:
            scope(item["sourcePath"], f"{path}.sourcePath")
        change_type = enum(item["changeType"], f"{path}.changeType", {"added", "modified", "deleted", "renamed"})
        if change_type == "renamed" and item["sourcePath"] is None:
            fail(f"{path}.sourcePath", "is required for a rename", "record the Git-derived source path")
        if change_type != "renamed" and item["sourcePath"] is not None:
            fail(f"{path}.sourcePath", "is only valid for a rename", "set sourcePath to null")
    if len({item.casefold() for item in changed_paths}) != len(changed_paths):
        fail("$.changedFiles", "contains duplicate/case-aliased paths", "record each Git-derived path once")
    check_statuses: list[str] = []
    checks = array(data["checks"], "$.checks", maximum=MAX_CHECKS)
    for index, item in enumerate(checks):
        path = f"$.checks[{index}]"
        object_(item, path, {"name", "command", "status", "evidenceDigest"})
        string(item["name"], f"{path}.name", maximum=200)
        string(item["command"], f"{path}.command")
        check_statuses.append(enum(item["status"], f"{path}.status", {"passed", "failed", "blocked"}))
        digest(item["evidenceDigest"], f"{path}.evidenceDigest")
    integer(data["elapsedMs"], "$.elapsedMs", minimum=1)
    status = enum(data["status"], "$.status", {"succeeded", "failed", "blocked", "timed-out"})
    blocker = data["blocker"]
    if blocker is not None:
        string(blocker, "$.blocker", maximum=2000)
    if status == "succeeded":
        if head_sha is None or commit_sha is None:
            fail("$.headSha", "succeeded requires a committed worker head", "record matching non-null headSha and commitSha")
        if not changed_files:
            fail("$.changedFiles", "succeeded requires Git-derived changed files", "record at least one committed changed path")
        if not checks:
            fail("$.checks", "succeeded requires validation evidence", "record at least one passing check")
        if blocker is not None:
            fail("$.blocker", "must be null for a succeeded worker", "clear the blocker")
        if any(check != "passed" for check in check_statuses):
            fail("$.checks", "succeeded requires every check to pass", "use a non-success status or provide passing evidence")
    else:
        if blocker is None:
            fail("$.blocker", "is required for a non-success terminal status", "record the concrete blocker")
        if changed_files and head_sha is None:
            fail("$.headSha", "changedFiles cannot be Git-derived without a worker head", "record matching head/commit SHAs or clear unavailable changes")
