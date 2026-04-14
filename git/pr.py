"""Pull request creation via the GitHub CLI."""

import asyncio
import re
import subprocess
from pathlib import Path
from dataclasses import dataclass

from git.operations import (
    GitResult,
    commit,
    create_branch,
    get_current_branch,
    push,
)


@dataclass
class PRResult:
    """Result of a PR creation attempt."""

    ok: bool
    url: str
    error: str


def _slugify(title: str) -> str:
    """Build a branch-friendly slug from a PR title."""

    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")
    return slug[:40] or "codex-task"


async def _run_gh(repo_path: Path, *args: str) -> GitResult:
    """Run a gh CLI command inside the target repo."""

    def _call() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["gh", *args],
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


async def create_pr(repo_path: Path, title: str, body: str = "") -> PRResult:
    """Create a branch, commit, push, and open a PR for the current changes."""

    current_branch = await get_current_branch(repo_path)

    # Only create a new branch if we're still on a common base branch.
    if current_branch in ("main", "master", ""):
        branch_name = f"codex/{_slugify(title)}"
        branch_result = await create_branch(repo_path, branch_name)
        if not branch_result.ok:
            return PRResult(ok=False, url="", error=f"Branch creation failed: {branch_result.stderr}")

    commit_result = await commit(repo_path, title)
    # Allow "nothing to commit" so PRs can be opened from already-committed work.
    if not commit_result.ok and "nothing to commit" not in commit_result.stdout.lower():
        return PRResult(ok=False, url="", error=f"Commit failed: {commit_result.output}")

    push_result = await push(repo_path)
    if not push_result.ok:
        return PRResult(ok=False, url="", error=f"Push failed: {push_result.stderr}")

    gh_result = await _run_gh(
        repo_path,
        "pr", "create",
        "--title", title,
        "--body", body or title,
    )
    if not gh_result.ok:
        return PRResult(ok=False, url="", error=f"PR creation failed: {gh_result.stderr}")

    url = gh_result.stdout.strip().split("\n")[-1]
    return PRResult(ok=True, url=url, error="")
