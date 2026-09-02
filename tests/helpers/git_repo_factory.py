"""Deterministic, ambient-Git-proof disposable repositories for builder tests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "plugins" / "compass-builder"

from compass_builder.git_environment import GitEnvironment, prepare_git_environment  # noqa: E402


class GitRepoFactory:
    """Create local-only Git fixtures without changing HOME or user configuration."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.repo = self.root / "repo"
        self.isolation = self.root / "git-isolation"
        self.environment: GitEnvironment = prepare_git_environment(self.isolation)

    def git(
        self, *arguments: str, cwd: Path | None = None, check: bool = True,
        input_data: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        target = self.repo if cwd is None else cwd
        result = subprocess.run(
            ["git", "--no-pager", "-C", str(target), *arguments], check=False,
            capture_output=True, shell=False, env=dict(self.environment.environment),
            input=input_data,
        )
        if check and result.returncode:
            raise AssertionError(result.stderr.decode("utf-8", errors="replace"))
        return result

    def literal_object(
        self, kind: str, payload: bytes, *, cwd: Path | None = None
    ) -> str:
        return self.git(
            "hash-object", "--literally", "-t", kind, "-w", "--stdin",
            cwd=cwd, input_data=payload,
        ).stdout.decode("ascii").strip()

    def init(self, files: dict[str, str] | None = None) -> str:
        self.repo.mkdir(parents=True)
        self.git("init", "--initial-branch=main")
        self.git("config", "--local", "user.name", "Compass Builder Fixture")
        self.git("config", "--local", "user.email", "fixture@localhost.invalid")
        self.git("config", "--local", "core.autocrlf", "false")
        self.git("config", "--local", "core.filemode", "false")
        self.git("config", "--local", "commit.gpgSign", "false")
        self.git("config", "--local", "tag.gpgSign", "false")
        self.git("config", "--local", "core.hooksPath", str(self.environment.hooks_directory))
        self.git("config", "--local", "core.attributesFile", str(self.environment.global_attributes))
        seeded = {".gitignore": "/.compass-builder/\n", "seed.txt": "seed\n"}
        seeded.update(files or {})
        for name, content in seeded.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        self.git("add", "--all")
        self.git("commit", "-m", "deterministic seed")
        return self.sha("HEAD")

    def sha(self, ref: str, *, cwd: Path | None = None) -> str:
        return self.git("rev-parse", ref, cwd=cwd).stdout.decode("ascii").strip()

    def commit(self, files: dict[str, str], message: str, *, cwd: Path | None = None) -> str:
        target = self.repo if cwd is None else cwd
        for name, content in files.items():
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        self.git("add", "--all", cwd=target)
        self.git("commit", "-m", message, cwd=target)
        return self.sha("HEAD", cwd=target)

    def worktree(self, branch: str, path: Path, start: str = "HEAD") -> Path:
        target = Path(path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        self.git("worktree", "add", "-b", branch, str(target), start)
        return target


__all__ = ["GitRepoFactory"]
