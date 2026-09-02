"""Lease-guarded serial integration and controller verification."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .git_environment import GitEnvironment, validate_git_environment
from .git_objects import GitObjectError, read_raw_commit
from .lease import acquire_lease, inspect_lease, release_lease
from .models import canonical_json
from .process_runner import (
    BoundedProcessError, completed_text, parse_command, run_bounded, run_bounded_text,
)
from .state import StateStore
from .verifier import (
    VerificationError, load_controller_launch_record, verify_worker,
)


class IntegrationError(ValueError):
    """A merge or controller verification stopped without discarding evidence."""


class _IntegrationFinished(Exception):
    pass


CommandRunner = Callable[[Sequence[str], Path, Mapping[str, str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class IntegrationResult:
    state: Mapping[str, object]
    merge_sha: str
    controller_check_digest: str
    check_evidence: tuple[dict[str, object], ...]


def _git(
    repo: Path, environment: GitEnvironment, arguments: Sequence[str], *, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    bundle = validate_git_environment(environment)
    try:
        result = run_bounded(
            ["git", "--no-pager", "-C", str(repo), *arguments],
            environment=bundle.environment,
        )
    except BoundedProcessError as exc:
        raise IntegrationError(f"bounded Git inspection failed: {exc}") from exc
    if check and result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise IntegrationError(f"Git {arguments[0]} failed: {detail}")
    return result


def _head(repo: Path, environment: GitEnvironment) -> str:
    return _git(repo, environment, ["rev-parse", "HEAD"]).stdout.decode("ascii").strip()


def _clean(repo: Path, environment: GitEnvironment) -> bool:
    return not _git(
        repo, environment, ["status", "--porcelain=v1", "-z", "--untracked-files=all"]
    ).stdout


def _require_integration_identity(
    store: StateStore, environment: GitEnvironment, expected_sha: str
) -> None:
    expected_ref = f"refs/heads/{store.spec['integrationBranch']}"
    symbolic = _git(
        store.repository.root, environment, ["symbolic-ref", "--quiet", "HEAD"], check=False
    )
    if symbolic.returncode or symbolic.stdout.decode("utf-8").strip() != expected_ref:
        raise IntegrationError("integration checkout is not on the leased integration branch")
    head = _head(store.repository.root, environment)
    branch_head = _git(
        store.repository.root, environment, ["rev-parse", "--verify", expected_ref]
    ).stdout.decode("ascii").strip()
    if head != expected_sha or branch_head != expected_sha:
        raise IntegrationError("integration HEAD/branch ref is stale")


def _default_runner(
    argv: Sequence[str], cwd: Path, environment: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    return run_bounded_text(list(argv), cwd=cwd, environment=environment)


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def _run_checks(
    repo: Path,
    environment: GitEnvironment,
    commands: Sequence[str],
    runner: CommandRunner,
    expected_head: str,
) -> tuple[list[dict[str, object]], bool, bool]:
    evidence: list[dict[str, object]] = []
    mutated = False
    for command in commands:
        try:
            if _head(repo, environment) != expected_head or not _clean(repo, environment):
                mutated = True
                break
            argv = parse_command(command)
            if not argv:
                raise ValueError("controller check has no executable argv")
            result = completed_text(runner(argv, repo, environment.environment))
            item = {
                "command": command, "returnCode": result.returncode,
                "status": "passed" if result.returncode == 0 else "failed",
                "outputDigest": "sha256:" + hashlib.sha256(json.dumps({
                    "stdout": result.stdout, "stderr": result.stderr,
                }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
            }
            evidence.append(item)
            if _head(repo, environment) != expected_head or not _clean(repo, environment):
                mutated = True
                break
        except Exception as exc:
            evidence.append({
                "command": command, "returnCode": None, "status": "error",
                "errorType": type(exc).__name__, "error": str(exc)[:2000],
            })
            break
    failed = any(item["status"] != "passed" for item in evidence)
    return evidence, failed, mutated


def _block_post_check(
    store: StateStore,
    integrated: Mapping[str, object],
    *,
    story_id: str,
    evidence_digest: str,
    reason: str,
) -> dict[str, object]:
    blocked = copy.deepcopy(dict(integrated))
    blocker = {
        "blockerId": f"check-{story_id}-{evidence_digest[7:15]}",
        "blockedFromState": "wave-integrated-unverified",
        "phase": "post-merge-check",
        "storyId": story_id,
        "reason": reason,
        "evidenceDigest": evidence_digest,
        "resumeState": "wave-integrated-unverified",
    }
    blocked.update(
        previousState="wave-integrated-unverified", state="blocked",
        activeBlocker=blocker,
    )
    blocked["blockerHistory"] = [*blocked["blockerHistory"], blocker]
    entries = blocked["waves"][blocked["currentWaveIndex"]]["branches"]
    target = next(item for item in entries if item["storyId"] == story_id)
    target.update(integrationState="blocked", controllerCheckDigest=evidence_digest)
    return store.write_transition(integrated, blocked)


def _finish_recorded_merge(
    store: StateStore, integrated: Mapping[str, object], story_id: str,
    environment: GitEnvironment, runner: CommandRunner,
) -> IntegrationResult:
    entries = integrated["waves"][integrated["currentWaveIndex"]]["branches"]
    matches = [entry for entry in entries if entry["storyId"] == story_id and entry["integrationState"] == "merged"]
    if len(matches) != 1:
        raise IntegrationError("recorded post-merge recovery does not identify one merged story")
    target, target_index = matches[0], entries.index(matches[0])
    merge_sha = str(target["mergeSha"])
    _require_integration_identity(store, environment, merge_sha)
    evidence, failed, mutated = _run_checks(
        store.repository.root, environment,
        [str(item) for item in store.spec["validationCommands"]], runner, merge_sha,
    )
    final_invalid = _head(store.repository.root, environment) != merge_sha or not _clean(
        store.repository.root, environment
    )
    digest = _digest({
        "schemaVersion": "compass-builder.controller-checks.v1",
        "mergeSha": merge_sha, "checks": evidence, "mutated": mutated,
    })
    if failed or mutated or final_invalid:
        _block_post_check(
            store, integrated, story_id=story_id, evidence_digest=digest,
            reason="One or more controller checks failed during merge recovery.",
        )
        raise IntegrationError("controller checks failed during merge recovery")
    verified = copy.deepcopy(dict(integrated))
    last = target_index + 1 == len(entries)
    verified.update(
        previousState="wave-integrated-unverified",
        state="wave-verified" if last else "wave-merging",
        lastVerifiedIntegrationSha=merge_sha,
    )
    entry = verified["waves"][verified["currentWaveIndex"]]["branches"][target_index]
    entry.update(
        integrationState="integration-verified", controllerCheckDigest=digest,
        postCheckExpectedSha=merge_sha,
    )
    if not last:
        verified["waves"][verified["currentWaveIndex"]]["branches"][target_index + 1]["preMergeExpectedSha"] = merge_sha
    verified = store.write_transition(integrated, verified)
    return IntegrationResult(verified, merge_sha, digest, tuple(evidence))


def integrate_verified_branch(
    store: StateStore,
    state: Mapping[str, object],
    receipt: Mapping[str, object],
    git_environment: GitEnvironment,
    *,
    owner_id: str = "compass-builder-integrator",
    acquired_at: str | None = None,
    expires_at: str | None = None,
    command_runner: CommandRunner | None = None,
) -> IntegrationResult:
    """Freshly verify under lease and merge only the resulting immutable SHA."""

    repo = store.repository.root
    current: Mapping[str, object] = state
    story_id: str | None = None
    handle = None
    result_value: IntegrationResult | None = None
    primary: Exception | None = None
    verified_head_sha: str | None = None
    expected_sha: str | None = None
    bundle = validate_git_environment(git_environment)
    try:
        if not isinstance(receipt, Mapping):
            raise IntegrationError("integration requires a worker receipt, not caller verification authority")
        story_id = str(receipt.get("storyId", ""))
        if (
            store.git_environment is None
            or store.git_environment.digest != bundle.digest
            or store.git_environment.root != bundle.root
        ):
            raise IntegrationError("integration Git environment does not match StateStore ownership")
        now = datetime.now(timezone.utc)
        acquired = acquired_at or now.isoformat().replace("+00:00", "Z")
        expires = expires_at or (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        receipt_digest = "sha256:" + hashlib.sha256(canonical_json(receipt)).hexdigest()
        handle = acquire_lease(
            store.repository.common_git_dir, str(store.spec["integrationBranch"]),
            owner_id=owner_id, evidence_digest=receipt_digest,
            acquired_at=acquired, expires_at=expires,
        )
        current = store.load_durable_state()
        if canonical_json(current, "run-state") != canonical_json(state, "run-state"):
            raise IntegrationError("durable ordered branch ledger changed before integration")
        if current["state"] == "wave-integrated-unverified":
            result_value = _finish_recorded_merge(
                store, current, story_id, bundle, command_runner or _default_runner
            )
            current = result_value.state
            raise _IntegrationFinished()
        if current["state"] != "wave-merging":
            raise IntegrationError("serial integration requires wave-merging state")
        entries = current["waves"][current["currentWaveIndex"]]["branches"]
        candidates = [item for item in entries if item["integrationState"] == "worker-verified"]
        if not candidates:
            raise IntegrationError("ordered ledger has no worker-verified branch to merge")
        target = candidates[0]
        target_index = entries.index(target)
        if target["storyId"] != story_id:
            raise IntegrationError("receipt is not the next ordered ledger story")
        expected_sha = str(current["expectedIntegrationSha"])
        if target["preMergeExpectedSha"] != expected_sha:
            raise IntegrationError("branch ledger pre-merge SHA is stale")
        observed = _head(repo, bundle)
        if observed != expected_sha:
            intents = [item for item in store.merge_intents() if (
                item["storyId"] == story_id and item["expectedSha"] == expected_sha
            )]
            symbolic = _git(repo, bundle, ["symbolic-ref", "--quiet", "HEAD"], check=False)
            branch_ref = f"refs/heads/{store.spec['integrationBranch']}"
            ref_head = _git(repo, bundle, ["rev-parse", "--verify", branch_ref]).stdout.decode("ascii").strip()
            exact = False
            if len(intents) == 1 and not symbolic.returncode and symbolic.stdout.decode().strip() == branch_ref and ref_head == observed:
                try:
                    proof = read_raw_commit(
                        repo, observed, bundle.environment, expected_parent_count=2
                    )
                    exact = proof.parents == (
                        str(intents[0]["expectedSha"]), str(intents[0]["verifiedHeadSha"]),
                    )
                except GitObjectError:
                    exact = False
            if exact:
                current = store.record_integration_merge(
                    current, story_id=story_id, merge_sha=observed
                )
                digest = _digest({"recoveredMerge": observed, "storyId": story_id})
                blocked = store.record_blocker(
                    current, reason="exact prior merge adopted; resume controller checks",
                    evidence_digest=digest, story_id=story_id,
                )
                if blocked is not None:
                    current = blocked
                raise IntegrationError("recovered exact prior merge; explicit resume is required")
            digest = _digest({"observedHead": observed, "storyId": story_id})
            store.record_failure_evidence(
                blocked_from_state=str(current["state"]),
                reason="manual merge recovery required: intent/HEAD mismatch",
                evidence_digest=digest, story_id=story_id, observed_head=observed,
            )
            raise IntegrationError("manual merge recovery required; integration HEAD is unproven")
        _require_integration_identity(store, bundle, expected_sha)
        if not _clean(repo, bundle):
            raise IntegrationError("integration checkout changed after lease acquisition")
        launch = load_controller_launch_record(store, story_id)
        try:
            verified = verify_worker(
                repo, store.spec, store.plan, receipt, launch, bundle,
            )
        except VerificationError as exc:
            raise IntegrationError(f"fresh worker verification failed: {exc}") from exc
        if verified.story_id != target["storyId"] or verified.branch != target["branch"]:
            raise IntegrationError("fresh verification does not bind the next ledger branch")
        source_ref = f"refs/heads/{verified.branch}"
        source_head = _git(repo, bundle, ["rev-parse", "--verify", source_ref]).stdout.decode("ascii").strip()
        if source_head != verified.head_sha:
            raise IntegrationError("source branch advanced or changed after fresh verification")
        verified_head_sha = verified.head_sha
        _require_integration_identity(store, bundle, expected_sha)
        store.record_merge_intent(
            current, story_id=story_id, expected_sha=expected_sha,
            verified_head_sha=verified.head_sha,
        )
        merge = _git(
            repo, bundle,
            ["merge", "--no-ff", "--no-edit", "--no-gpg-sign", verified.head_sha],
            check=False,
        )
        if merge.returncode:
            raise IntegrationError("merge conflict or merge failure; evidence retained")
        merge_sha = _head(repo, bundle)
        _require_integration_identity(store, bundle, merge_sha)
        try:
            parents = read_raw_commit(
                repo, merge_sha, bundle.environment, expected_parent_count=2
            ).parents
        except GitObjectError as exc:
            raise IntegrationError(f"post-merge proof is invalid: {exc}") from exc
        if parents != (expected_sha, verified.head_sha):
            raise IntegrationError("post-merge proof has unexpected ordered parents")
        integrated = store.record_integration_merge(
            current, story_id=story_id, merge_sha=merge_sha
        )
        current = integrated

        evidence, failed, mutated = _run_checks(
            repo, git_environment, [str(item) for item in store.spec["validationCommands"]],
            command_runner or _default_runner, merge_sha,
        )
        try:
            final_invalid = (
                _head(repo, git_environment) != merge_sha or not _clean(repo, git_environment)
            )
        except Exception as exc:
            evidence.append({
                "command": "controller-final-git-inspection", "returnCode": None,
                "status": "error", "errorType": type(exc).__name__,
                "error": str(exc)[:2000],
            })
            final_invalid = True
            failed = True
        check_digest = _digest({
            "schemaVersion": "compass-builder.controller-checks.v1",
            "mergeSha": merge_sha, "checks": evidence, "mutated": mutated,
        })
        if failed or mutated or final_invalid:
            reason = (
                "Controller validation mutated the integration checkout."
                if mutated else "One or more controller checks failed."
            )
            current = _block_post_check(
                store, integrated, story_id=verified.story_id,
                evidence_digest=check_digest, reason=reason,
            )
            raise IntegrationError(reason + " Merge and worktree evidence retained.")

        verified_state = copy.deepcopy(integrated)
        last = target_index + 1 == len(entries)
        verified_state.update(
            previousState="wave-integrated-unverified",
            state="wave-verified" if last else "wave-merging",
            lastVerifiedIntegrationSha=merge_sha,
        )
        final_entry = verified_state["waves"][verified_state["currentWaveIndex"]]["branches"][target_index]
        final_entry.update(
            integrationState="integration-verified",
            controllerCheckDigest=check_digest,
            postCheckExpectedSha=merge_sha,
        )
        if not last:
            verified_state["waves"][verified_state["currentWaveIndex"]]["branches"][target_index + 1]["preMergeExpectedSha"] = merge_sha
        verified_state = store.write_transition(integrated, verified_state)
        current = verified_state
        result_value = IntegrationResult(
            state=verified_state, merge_sha=merge_sha,
            controller_check_digest=check_digest, check_evidence=tuple(evidence),
        )
    except _IntegrationFinished:
        pass
    except Exception as exc:
        primary = exc if isinstance(exc, IntegrationError) else IntegrationError(str(exc))
    failure_recorded = False
    if primary is not None and handle is not None:
        digest = _digest({"reason": str(primary), "storyId": story_id})
        if (
            current.get("state") == "wave-merging" and expected_sha is not None
            and verified_head_sha is not None and story_id is not None
        ):
            try:
                observed = _head(repo, bundle)
                if observed != expected_sha:
                    proof = read_raw_commit(
                        repo, observed, bundle.environment, expected_parent_count=2
                    )
                    if proof.parents == (expected_sha, verified_head_sha):
                        current = store.record_integration_merge(
                            current, story_id=story_id, merge_sha=observed
                        )
                    else:
                        raise IntegrationError("observed post-merge HEAD has unproven parents")
            except Exception as recovery_error:
                try:
                    store.record_failure_evidence(
                        blocked_from_state=str(current.get("state", "unknown")),
                        reason=f"manual merge recovery required: {recovery_error}",
                        evidence_digest=digest, story_id=story_id,
                        observed_head=locals().get("observed"),
                    )
                    failure_recorded = True
                except Exception:
                    pass
        try:
            if not failure_recorded:
                store.record_blocker(
                    current, reason=str(primary), evidence_digest=digest, story_id=story_id
                )
                failure_recorded = True
        except Exception:
            pass
    if handle is not None:
        try:
            release_lease(handle)
        except Exception as exc:
            if primary is None:
                primary = IntegrationError(f"integration lease release failed: {exc}")
                digest = _digest({"reason": str(primary), "storyId": story_id})
                try:
                    owned = inspect_lease(
                        store.repository.common_git_dir,
                        str(store.spec["integrationBranch"]), now=None,
                    )
                    if canonical_json(owned) == canonical_json(handle.record):
                        store.record_blocker(
                            current, reason=str(primary), evidence_digest=digest,
                            story_id=story_id,
                        )
                        failure_recorded = True
                except Exception:
                    pass
    if primary is not None:
        if not failure_recorded:
            digest = _digest({"reason": str(primary), "storyId": story_id})
            try:
                store.record_failure_evidence(
                    blocked_from_state=str(current.get("state", "unknown")),
                    reason=str(primary), evidence_digest=digest, story_id=story_id,
                )
            except Exception:
                pass
        raise primary
    assert result_value is not None
    return result_value


__all__ = ["IntegrationError", "IntegrationResult", "integrate_verified_branch"]
