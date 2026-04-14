"""Git helpers invoked against an agent's bound repository."""

import asyncio
import subprocess
from pathlib import Path
from dataclasses import dataclass


@dataclass
class GitResult:
    """Result of a git command invocation."""

    ok: bool
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return self.stdout if self.ok else self.stderr


async def _run_git(repo_path: Path, *args: str) -> GitResult:
    """Run a git command in the target repo off the event loop thread."""

    def _call() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
        )

    result = await asyncio.to_thread(_call)
    return GitResult(
        ok=result.returncode == 0,
        stdout=result.stdout,
        stderr=result.stderr,
    )


async def get_status(repo_path: Path) -> GitResult:
    """Return short git status for the repo."""

    return await _run_git(repo_path, "status", "--short")


async def get_diff(repo_path: Path) -> GitResult:
    """Return the unstaged working-tree diff."""

    return await _run_git(repo_path, "diff")


async def get_staged_diff(repo_path: Path) -> GitResult:
    """Return the staged diff."""

    return await _run_git(repo_path, "diff", "--staged")


async def get_current_branch(repo_path: Path) -> str:
    """Return the currently checked-out branch name."""

    result = await _run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    return result.stdout.strip() if result.ok else ""


async def stage_all(repo_path: Path) -> GitResult:
    """Stage all tracked and untracked changes."""

    return await _run_git(repo_path, "add", "-A")


async def commit(repo_path: Path, message: str) -> GitResult:
    """Stage everything then create a commit with the given message."""

    staged = await stage_all(repo_path)
    if not staged.ok:
        return staged
    return await _run_git(repo_path, "commit", "-m", message)


async def push(repo_path: Path, branch: str | None = None) -> GitResult:
    """Push the current or specified branch to origin."""

    if branch is None:
        branch = await get_current_branch(repo_path)
    if not branch:
        return GitResult(ok=False, stdout="", stderr="Could not determine branch")
    return await _run_git(repo_path, "push", "-u", "origin", branch)


async def create_branch(repo_path: Path, name: str) -> GitResult:
    """Create and check out a new branch."""

    return await _run_git(repo_path, "checkout", "-b", name)
