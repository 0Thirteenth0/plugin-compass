"""Executable paired benchmark runner with fresh repositories and durable evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping

from .benchmark import GENESIS_HASH
from .cleanup import cleanup_run
from ._validation import canonical_data
from .controller import (
    CONTROLLER_VERSION, PROMPT_VERSION, ControllerError, ControllerResult,
    WorkerTransport, codex_worker_transport, empty_metrics, execute_run,
)
from .git_environment import load_git_environment, prepare_git_environment
from .models import (
    canonical_json, validate_benchmark_aggregate_receipts,
    validate_benchmark_receipt, validate_benchmark_workloads,
)
from .state import StateStore, build_execution_bundle, validate_execution_bundle


RunExecutor = Callable[..., ControllerResult]


class BenchmarkRunnerError(RuntimeError):
    """The paired benchmark could not produce complete comparable evidence."""


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_data(value)).hexdigest()


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


class EventLedger:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.previous = GENESIS_HASH

    def append(self, kind: str, details: Mapping[str, object]) -> dict[str, object]:
        event = {
            "sequence": len(self.events) + 1, "previousHash": self.previous,
            "kind": kind, "details": copy.deepcopy(dict(details)),
        }
        event["eventHash"] = _digest(event)
        self.events.append(event)
        self.previous = str(event["eventHash"])
        return event


def _git(
    repository: Path, environment: Mapping[str, str], *arguments: str,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", "--no-pager", "-C", str(repository), *arguments],
        check=False, capture_output=True, shell=False, env=dict(environment),
    )
    if result.returncode:
        raise BenchmarkRunnerError(result.stderr.decode("utf-8", errors="replace").strip())
    return result


def _fixture_digest(repository: Path, sha: str) -> str:
    result = subprocess.run(
        ["git", "--no-pager", "-C", str(repository), "archive", "--format=tar", sha],
        check=False, capture_output=True, shell=False,
    )
    if result.returncode:
        raise BenchmarkRunnerError("recorded fixture SHA cannot be archived")
    return "sha256:" + hashlib.sha256(result.stdout).hexdigest()


def _clone_fixture(source: Path, destination: Path, sha: str) -> Path:
    isolation = prepare_git_environment(destination.parent / f"{destination.name}-git")
    result = subprocess.run(
        ["git", "clone", "--no-local", "--no-hardlinks", str(source), str(destination)],
        check=False, capture_output=True, shell=False, env=dict(isolation.environment),
    )
    if result.returncode:
        raise BenchmarkRunnerError(
            "fresh fixture clone failed: "
            + result.stderr.decode("utf-8", errors="replace").strip()
        )
    _git(destination, isolation.environment, "checkout", "-B", "main", sha)
    _git(destination, isolation.environment, "reset", "--hard", sha)
    actual = _git(destination, isolation.environment, "rev-parse", "HEAD").stdout.decode().strip()
    if actual != sha:
        raise BenchmarkRunnerError("fresh fixture clone did not start at the recorded SHA")
    if _git(
        destination, isolation.environment, "status", "--porcelain=v1", "-z",
        "--untracked-files=all",
    ).stdout:
        raise BenchmarkRunnerError("fresh fixture clone is not clean")
    return destination


def _manifest(workload_id: str, fixture: str, base_sha: str, spec: str, pairs: int):
    pair_records = []
    for pair in range(1, pairs + 1):
        order = ("sequential", "parallel") if pair % 2 else ("parallel", "sequential")
        pair_records.append({
            "pairNumber": pair,
            "arms": [{
                "arm": arm,
                "attemptId": f"{workload_id}-p{pair}-{'seq' if arm == 'sequential' else 'par'}",
            } for arm in order],
        })
    return validate_benchmark_workloads({
        "schemaVersion": "compass-builder.benchmark-workloads.v1",
        "pairCount": pairs,
        "workloads": [{
            "workloadId": workload_id, "fixtureDigest": fixture,
            "baseSha": base_sha, "specDigest": spec,
            "warmups": [
                {"arm": "sequential", "attemptId": f"{workload_id}-warm-seq"},
                {"arm": "parallel", "attemptId": f"{workload_id}-warm-par"},
            ],
            "pairs": pair_records,
        }],
    })


def _controls(
    bundle: Mapping[str, object], fixture_digest: str, timeout_ms: int,
) -> dict[str, object]:
    spec, plan, host = bundle["runSpec"], bundle["wavePlan"], bundle["hostCapabilities"]
    stories = [str(item["id"]) for item in spec["stories"]]
    checks = [
        str(check) for story in spec["stories"] for check in story["acceptanceChecks"]
    ]
    non_mode = {
        key: value for key, value in plan.items()
        if key not in {"mode", "reasons", "concurrency", "waves"}
    }
    toolchain = {
        "codex": host["codexVersion"], "python": host["pythonVersion"],
        "git": host["gitVersion"],
    }
    environment = {
        "os": host["os"], "cpuCount": os.cpu_count() or 1,
        "supports": host["supports"],
    }
    return {
        "fixtureDigest": fixture_digest, "specDigest": _digest(spec),
        "startSha": spec["baseSha"], "orderedStories": stories,
        "orderedStorySetDigest": _digest(stories), "acceptanceChecks": checks,
        "acceptanceCheckDigest": _digest(checks), "exactModel": spec["exactModel"],
        "effortPolicyVersion": spec["effortPolicyVersion"],
        "initialEfforts": [{
            "storyId": item["storyId"], "effort": item["recommendedEffort"],
        } for item in plan["stories"]],
        "handoffDigests": [{
            "storyId": item["storyId"], "digest": item["handoffDigest"],
        } for item in plan["stories"]],
        "nonModePlanDigest": _digest(non_mode),
        "controllerVersion": CONTROLLER_VERSION, "promptVersion": PROMPT_VERSION,
        "codexVersion": host["codexVersion"], "pythonVersion": host["pythonVersion"],
        "gitVersion": host["gitVersion"], "os": host["os"],
        "cpuCount": os.cpu_count() or 1,
        "concurrencyCeiling": min(
            spec["hostConcurrencyCeiling"], spec["userConcurrencyCeiling"]
        ),
        "timeoutMs": timeout_ms, "toolchainDigest": _digest(toolchain),
        "environmentDigest": _digest(environment),
    }


def _attempts(manifest: Mapping[str, object]):
    workload = manifest["workloads"][0]
    for warmup in workload["warmups"]:
        yield warmup["arm"], warmup["attemptId"], 0, True
    for pair in workload["pairs"]:
        for arm in pair["arms"]:
            yield arm["arm"], arm["attemptId"], pair["pairNumber"], False


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(canonical_data(value))


def _remove_tree(path: Path, root: Path) -> None:
    target = Path(path).resolve(strict=False)
    boundary = Path(root).resolve(strict=True)
    try:
        target.relative_to(boundary)
    except ValueError as exc:
        raise BenchmarkRunnerError("temporary cleanup target escapes benchmark staging") from exc

    def make_writable(function, name, _error):
        os.chmod(name, stat.S_IWRITE)
        function(name)

    if target.exists():
        shutil.rmtree(target, onerror=make_writable)


def run_benchmark(
    fixture_repository: Path,
    sequential_bundle: Mapping[str, object],
    parallel_bundle: Mapping[str, object],
    output_directory: Path,
    *,
    pairs: int,
    timeout_ms: int,
    workload_id: str = "calibration",
    worker_transport: WorkerTransport = codex_worker_transport,
    run_executor: RunExecutor = execute_run,
) -> dict[str, object]:
    """Run every planned arm in a fresh clone and atomically publish evidence."""

    if type(pairs) is not int or pairs < 5:
        raise BenchmarkRunnerError("benchmark requires --pairs >= 5")
    if type(timeout_ms) is not int or timeout_ms < 1:
        raise BenchmarkRunnerError("benchmark timeout must be a positive integer")
    source = Path(fixture_repository).resolve(strict=True)
    sequential = validate_execution_bundle(sequential_bundle)
    parallel = validate_execution_bundle(parallel_bundle)
    if sequential["wavePlan"]["mode"] != "sequential" or parallel["wavePlan"]["mode"] != "parallel":
        raise BenchmarkRunnerError("benchmark templates must be sequential and parallel")
    if canonical_json(sequential["runSpec"]) != canonical_json(parallel["runSpec"]):
        raise BenchmarkRunnerError("benchmark arms must share one byte-identical run spec")
    base_sha = str(sequential["runSpec"]["baseSha"])
    fixture_digest = _fixture_digest(source, base_sha)
    spec_digest = _digest(sequential["runSpec"])
    manifest = _manifest(workload_id, fixture_digest, base_sha, spec_digest, pairs)
    seq_controls = _controls(sequential, fixture_digest, timeout_ms)
    par_controls = _controls(parallel, fixture_digest, timeout_ms)
    if canonical_json(seq_controls) != canonical_json(par_controls):
        raise BenchmarkRunnerError("benchmark templates differ outside scheduling mode")
    output = Path(output_directory).absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise BenchmarkRunnerError("benchmark output target already exists")
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    ledger = EventLedger()
    receipts: list[dict[str, object]] = []
    try:
        for index, (arm, attempt_id, pair_number, warmup) in enumerate(_attempts(manifest)):
            first_sequence = len(ledger.events) + 1
            ledger.append("attempt-start", {"attemptId": attempt_id, "arm": arm})
            template = sequential if arm == "sequential" else parallel
            started = datetime.now(timezone.utc)
            terminal_status, final_sha = "green", None
            metrics = empty_metrics()
            clone_parent = staging / "repositories" / f"attempt-{index + 1}"
            clone_parent.mkdir(parents=True)
            repository = _clone_fixture(source, clone_parent / "repo", base_sha)

            def sink(kind: str, details: Mapping[str, object]) -> None:
                ledger.append(kind, {"attemptId": attempt_id, **dict(details)})

            try:
                bundle = build_execution_bundle(
                    template["runSpec"], template["wavePlan"],
                    template["hostCapabilities"], template["planningTimestamp"], repository,
                )
                result = run_executor(
                    repository, bundle, worker_transport=worker_transport,
                    timeout_ms=timeout_ms, event_sink=sink,
                )
                started_text, ended_text, elapsed = (
                    result.started_at, result.ended_at, result.elapsed_ms
                )
                metrics.update(result.metrics)
                final_sha = result.final_green_sha
                run_root = repository / ".compass-builder" / "runs" / result.run_id
                if (run_root / "git-environment").is_dir():
                    git_environment = load_git_environment(run_root / "git-environment")
                    cleanup_run(
                        StateStore(
                            repository, bundle["runSpec"], bundle["wavePlan"],
                            git_environment,
                        ),
                        git_environment,
                    )
            except ControllerError as exc:
                terminal_status = exc.terminal_status
                metrics.update(exc.metrics)
                ended = datetime.now(timezone.utc)
                started_wire = started.replace(microsecond=(started.microsecond // 1000) * 1000)
                ended_wire = ended.replace(microsecond=(ended.microsecond // 1000) * 1000)
                elapsed = int((ended_wire - started_wire).total_seconds() * 1000)
                if elapsed < 1:
                    ended_wire = started_wire + timedelta(milliseconds=1)
                    elapsed = 1
                started_text, ended_text = _timestamp(started_wire), _timestamp(ended_wire)
                if terminal_status == "timed-out" and metrics["timeouts"] == 0:
                    metrics["timeouts"] += 1
            ledger.append("attempt-completion", {
                "attemptId": attempt_id, "arm": arm, "terminalStatus": terminal_status,
            })
            receipt = validate_benchmark_receipt({
                "schemaVersion": "compass-builder.benchmark-receipt.v1",
                "workloadId": workload_id, "attemptId": attempt_id, "arm": arm,
                "pairNumber": pair_number,
                "trialNumber": 0 if warmup else pair_number, "warmup": warmup,
                "controls": seq_controls, "startedAt": started_text, "endedAt": ended_text,
                "elapsedMs": elapsed, "metrics": metrics,
                "eventLedger": {
                    "firstSequence": first_sequence, "lastSequence": len(ledger.events),
                    "terminalHash": ledger.previous,
                },
                "finalGreenSha": final_sha if terminal_status == "green" else None,
                "terminalStatus": terminal_status,
            })
            receipts.append(receipt)
            _write_json(staging / f"receipt-{index + 1:03d}.json", receipt)
            _remove_tree(clone_parent, staging)
        _remove_tree(staging / "repositories", staging)
        workload_controls = [{"workloadId": workload_id, "controls": seq_controls}]
        attempts = [{
            "workloadId": item["workloadId"], "attemptId": item["attemptId"],
            "arm": item["arm"], "pairNumber": item["pairNumber"],
            "receiptDigest": _digest(item), "terminalStatus": item["terminalStatus"],
        } for item in receipts]
        aggregate = {
            "schemaVersion": "compass-builder.benchmark-aggregate.v1",
            "workloadManifest": manifest, "workloadManifestDigest": _digest(manifest),
            "workloadControls": workload_controls,
            "controlsDigest": _digest(workload_controls),
            "eventLedger": {
                "firstSequence": 1, "lastSequence": len(ledger.events),
                "terminalHash": ledger.previous,
            },
            "attempts": attempts,
        }
        aggregate, _ = validate_benchmark_aggregate_receipts(aggregate, receipts)
        _write_json(staging / "aggregate.json", aggregate)
        _write_json(staging / "sequential.json", [
            item for item in receipts if item["arm"] == "sequential"
        ])
        _write_json(staging / "parallel.json", [
            item for item in receipts if item["arm"] == "parallel"
        ])
        lines = b"".join(canonical_json(event) for event in ledger.events)
        (staging / "events.jsonl").write_bytes(lines)
        os.rename(staging, output)
        return aggregate
    except BaseException:
        try:
            _remove_tree(staging, staging.parent)
        except Exception:
            pass
        raise


__all__ = ["BenchmarkRunnerError", "EventLedger", "run_benchmark"]
