"""Run specification, wave plan, and host capability contract validation."""

from __future__ import annotations

from typing import Any

from ._limits import (
    MAX_BRANCHES_PER_WAVE, MAX_COMMANDS, MAX_DEPENDENCIES, MAX_REASONS,
    MAX_SCOPES, MAX_STORIES, MAX_SUPPORTED_EFFORTS, MAX_WAVES,
)
from ._validation import (
    EFFORTS, array, boolean, branch, digest, enum, fail, identifier, integer,
    object_, run_id, scope, sha, string, strings, timestamp,
)


MODES = {"auto", "sequential", "parallel"}


def _scopes(value: Any, path: str) -> list[str]:
    result = [scope(item, f"{path}[{index}]") for index, item in enumerate(array(value, path, minimum=1, maximum=MAX_SCOPES))]
    folded = [tuple(part.casefold() for part in item.split("/")) for item in result]
    if len(set(folded)) != len(folded):
        fail(path, "contains duplicate or case-aliased scopes", "keep one normalized scope")
    for left_index, left in enumerate(folded):
        for right_index, right in enumerate(folded):
            if left_index != right_index and len(left) < len(right) and right[:len(left)] == left:
                fail(path, f"scope {result[left_index]!r} is an unsafe ancestor of {result[right_index]!r}", "declare disjoint leaf ownership scopes")
    return result


def _story(value: Any, path: str) -> dict[str, Any]:
    story = object_(value, path, {
        "id", "title", "description", "dependsOn", "writeScopes", "acceptanceChecks",
        "validationCommands", "independentReviewPath", "sharedState", "priority",
        "completionState", "complexity", "ambiguity", "risk", "validationStrength",
    })
    identifier(story["id"], f"{path}.id")
    string(story["title"], f"{path}.title", maximum=160)
    string(story["description"], f"{path}.description", maximum=2000)
    for index, item in enumerate(strings(story["dependsOn"], f"{path}.dependsOn", maximum=64, items_maximum=MAX_DEPENDENCIES)):
        identifier(item, f"{path}.dependsOn[{index}]")
    _scopes(story["writeScopes"], f"{path}.writeScopes")
    checks = strings(story["acceptanceChecks"], f"{path}.acceptanceChecks", items_maximum=MAX_COMMANDS)
    commands = strings(story["validationCommands"], f"{path}.validationCommands", items_maximum=MAX_COMMANDS)
    review = story["independentReviewPath"]
    if review is not None:
        string(review, f"{path}.independentReviewPath", maximum=1000)
    if not ((checks and commands) or review is not None):
        fail(path, "lacks actionable acceptance evidence", "provide acceptanceChecks with validationCommands, or an independentReviewPath")
    shared = object_(story["sharedState"], f"{path}.sharedState", {"mode", "description"})
    enum(shared["mode"], f"{path}.sharedState.mode", {"none", "read-only", "mutates"})
    string(shared["description"], f"{path}.sharedState.description", maximum=500)
    integer(story["priority"], f"{path}.priority")
    enum(story["completionState"], f"{path}.completionState", {"pending", "completed"})
    for name in ("complexity", "ambiguity", "risk"):
        enum(story[name], f"{path}.{name}", {"low", "medium", "high"})
    enum(story["validationStrength"], f"{path}.validationStrength", {"none", "partial", "decisive"})
    return story


def _story_graph(stories: list[Any], path: str) -> None:
    parsed = [_story(story, f"{path}[{index}]") for index, story in enumerate(stories)]
    ids = [story["id"] for story in parsed]
    if len(set(ids)) != len(ids):
        fail(path, "contains duplicate story IDs", "assign each story one unique stable ID")
    known = set(ids)
    graph: dict[str, list[str]] = {}
    for index, story in enumerate(parsed):
        dependencies = story["dependsOn"]
        if len(set(dependencies)) != len(dependencies):
            fail(f"{path}[{index}].dependsOn", "contains duplicate dependencies", "list each dependency once")
        unknown = sorted(set(dependencies) - known)
        if unknown:
            fail(f"{path}[{index}].dependsOn", f"references unknown IDs: {', '.join(unknown)}", "reference only IDs declared in stories")
        if story["id"] in dependencies:
            fail(f"{path}[{index}].dependsOn", "contains a self dependency", "remove the story's own ID")
        graph[story["id"]] = dependencies
    colors = {story_id: 0 for story_id in ids}
    for story_id in ids:
        if colors[story_id] != 0:
            continue
        stack: list[tuple[str, int]] = [(story_id, 0)]
        colors[story_id] = 1
        while stack:
            node, dependency_index = stack[-1]
            dependencies = graph[node]
            if dependency_index == len(dependencies):
                colors[node] = 2
                stack.pop()
                continue
            dependency = dependencies[dependency_index]
            stack[-1] = (node, dependency_index + 1)
            if colors[dependency] == 1:
                fail(path, f"dependency cycle reaches {dependency!r}", "remove at least one dependency edge in the cycle")
            if colors[dependency] == 0:
                colors[dependency] = 1
                stack.append((dependency, 0))
    positions = {story_id: index for index, story_id in enumerate(ids)}
    for index, story in enumerate(parsed):
        later = [dependency for dependency in story["dependsOn"] if positions[dependency] >= index]
        if later:
            fail(f"{path}[{index}].dependsOn", f"is not topological; dependency IDs are not earlier: {', '.join(later)}", "order every dependency before its dependent story")


def validate_run_spec_shape(data: dict[str, Any]) -> None:
    object_(data, "$", {
        "schemaVersion", "runId", "baseRef", "baseSha", "integrationBranch",
        "integrationExpectedSha", "mode", "exactModel", "effortPolicyVersion",
        "hostConcurrencyCeiling", "userConcurrencyCeiling", "validationCommands", "stories",
    })
    run_id(data["runId"], "$.runId")
    string(data["baseRef"], "$.baseRef", maximum=240)
    sha(data["baseSha"], "$.baseSha")
    branch(data["integrationBranch"], "$.integrationBranch")
    sha(data["integrationExpectedSha"], "$.integrationExpectedSha")
    enum(data["mode"], "$.mode", MODES)
    if string(data["exactModel"], "$.exactModel", maximum=160) == "inherit":
        fail("$.exactModel", "must name an exact model", "replace 'inherit' with the selected model ID")
    string(data["effortPolicyVersion"], "$.effortPolicyVersion", maximum=128)
    integer(data["hostConcurrencyCeiling"], "$.hostConcurrencyCeiling", minimum=1)
    integer(data["userConcurrencyCeiling"], "$.userConcurrencyCeiling", minimum=1)
    strings(data["validationCommands"], "$.validationCommands", minimum=1, items_maximum=MAX_COMMANDS)
    _story_graph(array(data["stories"], "$.stories", minimum=1, maximum=MAX_STORIES), "$.stories")


def validate_wave_plan_shape(data: dict[str, Any]) -> None:
    object_(data, "$", {
        "schemaVersion", "runId", "baseSha", "integrationBranch", "integrationExpectedSha",
        "normalizedInputDigest", "hostEvidenceDigest", "effortPolicyVersion", "mode",
        "reasons", "concurrency", "stories", "waves",
    })
    run_id(data["runId"], "$.runId")
    sha(data["baseSha"], "$.baseSha")
    branch(data["integrationBranch"], "$.integrationBranch")
    sha(data["integrationExpectedSha"], "$.integrationExpectedSha")
    digest(data["normalizedInputDigest"], "$.normalizedInputDigest")
    digest(data["hostEvidenceDigest"], "$.hostEvidenceDigest")
    string(data["effortPolicyVersion"], "$.effortPolicyVersion", maximum=128)
    enum(data["mode"], "$.mode", {"sequential", "parallel"})
    strings(data["reasons"], "$.reasons", minimum=1, items_maximum=MAX_REASONS)
    integer(data["concurrency"], "$.concurrency", minimum=1)
    story_ids: list[str] = []
    for index, item in enumerate(array(data["stories"], "$.stories", minimum=1, maximum=MAX_STORIES)):
        path = f"$.stories[{index}]"
        object_(item, path, {"storyId", "branch", "recommendedEffort", "handoffDigest"})
        story_ids.append(identifier(item["storyId"], f"{path}.storyId"))
        branch(item["branch"], f"{path}.branch")
        enum(item["recommendedEffort"], f"{path}.recommendedEffort", EFFORTS)
        digest(item["handoffDigest"], f"{path}.handoffDigest")
    if len(set(story_ids)) != len(story_ids):
        fail("$.stories", "contains duplicate story IDs", "bind each story exactly once")
    seen: list[str] = []
    for wave_index, wave in enumerate(array(data["waves"], "$.waves", minimum=1, maximum=MAX_WAVES)):
        path = f"$.waves[{wave_index}]"
        object_(wave, path, {"waveIndex", "storyIds"})
        if integer(wave["waveIndex"], f"{path}.waveIndex") != wave_index:
            fail(f"{path}.waveIndex", "does not match ordered wave position", f"set it to {wave_index}")
        wave_stories = strings(wave["storyIds"], f"{path}.storyIds", minimum=1, maximum=64, items_maximum=MAX_BRANCHES_PER_WAVE)
        if len(wave_stories) > data["concurrency"]:
            fail(f"{path}.storyIds", "exceeds planned concurrency", "reduce wave size or raise the validated ceiling")
        seen.extend(identifier(item, f"{path}.storyIds") for item in wave_stories)
    if seen != story_ids:
        fail("$.waves", "story accounting is missing, extra, duplicate, or reordered", "place every planned story exactly once in original order")


def validate_plan_safety(spec: dict[str, Any], plan: dict[str, Any]) -> None:
    """Enforce immutable branch identity and parallel-only safety gates."""
    workers = [
        (story["storyId"], tuple(part.casefold() for part in story["branch"].split("/")))
        for story in plan["stories"]
    ]
    for index, (story_id, parts) in enumerate(workers):
        for prior_id, prior in workers[:index]:
            if prior[:len(parts)] == parts or parts[:len(prior)] == prior:
                fail("wavePlan.stories", f"worker branches for {prior_id!r} and {story_id!r} have a Windows D/F ref collision", "use branch names that are neither equal nor ancestor/descendant")
    integration = tuple(part.casefold() for part in spec["integrationBranch"].split("/"))
    for story_id, parts in workers:
        if integration[:len(parts)] == parts or parts[:len(integration)] == integration:
            fail("wavePlan.stories", f"worker branch for {story_id!r} has a D/F ref collision with integrationBranch", "use branch namespaces that are neither equal nor ancestor/descendant")
    if plan["mode"] != "parallel":
        return

    by_id = {story["id"]: story for story in spec["stories"]}
    for wave_index, wave in enumerate(plan["waves"]):
        owned: list[tuple[str, tuple[str, ...]]] = []
        for story_id in wave["storyIds"]:
            story = by_id[story_id]
            if story["sharedState"]["mode"] == "mutates":
                fail(f"runSpec.stories.{story_id}.sharedState.mode", "parallel work may not mutate shared state", "use read-only/none shared state or schedule sequentially")
            actionable = bool(
                (story["acceptanceChecks"] and story["validationCommands"])
                or story["independentReviewPath"]
            )
            if story["validationStrength"] != "decisive" or not actionable:
                fail(f"runSpec.stories.{story_id}.validationStrength", "parallel work requires decisive actionable validation", "provide decisive commands/checks or an independent review path")
            for raw_scope in story["writeScopes"]:
                parts = tuple(part.casefold() for part in raw_scope.split("/"))
                for prior_id, prior in owned:
                    if prior == parts or prior[:len(parts)] == parts or parts[:len(prior)] == prior:
                        fail(f"wavePlan.waves[{wave_index}].storyIds", f"parallel scopes for {prior_id!r} and {story_id!r} overlap under Windows normalization", "use pairwise-disjoint scopes or put the stories in separate waves")
                owned.append((story_id, parts))


def validate_host_shape(data: dict[str, Any]) -> None:
    object_(data, "$", {
        "schemaVersion", "codexVersion", "selectedModel", "supportedEfforts", "captureSource",
        "reasoningConfig",
        "capturedAt", "validUntil", "cliEvidenceDigest", "gitEvidenceDigest", "supports", "os",
        "pythonVersion", "gitVersion", "hostConcurrencyCeiling", "userConcurrencyCeiling",
    })
    for field in ("codexVersion", "captureSource", "os", "pythonVersion", "gitVersion"):
        string(data[field], f"$.{field}", maximum=256)
    if string(data["selectedModel"], "$.selectedModel", maximum=160) == "inherit":
        fail("$.selectedModel", "must name the exact selected model", "record the native model ID")
    for index, effort in enumerate(strings(data["supportedEfforts"], "$.supportedEfforts", minimum=1, maximum=128, items_maximum=MAX_SUPPORTED_EFFORTS)):
        enum(effort, f"$.supportedEfforts[{index}]", EFFORTS)
    reasoning = object_(
        data["reasoningConfig"], "$.reasoningConfig", {"key", "evidenceDigest"}
    )
    if reasoning["key"] != "model_reasoning_effort":
        fail(
            "$.reasoningConfig.key",
            "does not name the supported Codex reasoning config key",
            "capture native proof for exact key 'model_reasoning_effort'",
        )
    digest(reasoning["evidenceDigest"], "$.reasoningConfig.evidenceDigest")
    captured = timestamp(data["capturedAt"], "$.capturedAt")
    valid_until = timestamp(data["validUntil"], "$.validUntil")
    if valid_until <= captured:
        fail("$.validUntil", "must be later than capturedAt", "record a deterministic bounded validity interval")
    digest(data["cliEvidenceDigest"], "$.cliEvidenceDigest")
    digest(data["gitEvidenceDigest"], "$.gitEvidenceDigest")
    supports = object_(data["supports"], "$.supports", {"worktrees", "workingDirectoryBinding", "structuredOutput", "multiAgentDisable"})
    for field in supports:
        boolean(supports[field], f"$.supports.{field}")
    integer(data["hostConcurrencyCeiling"], "$.hostConcurrencyCeiling", minimum=1)
    integer(data["userConcurrencyCeiling"], "$.userConcurrencyCeiling", minimum=1)
