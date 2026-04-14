"""Bot-side git operations with Telegram-friendly rendering."""

from pathlib import Path

from agents.manager import Agent
from git.operations import (
    get_diff,
    get_status,
    commit as git_commit,
    push as git_push,
    get_current_branch,
)
from git.pr import create_pr


MAX_DIFF_BYTES = 50_000  # hard cap so we don't try to post 10MB of churn


async def render_diff(agent: Agent) -> list[str]:
    """Return Telegram-ready message chunks for the agent's unstaged diff."""

    status = await get_status(agent.repo_path)
    diff = await get_diff(agent.repo_path)

    if not diff.ok:
        return [f"Error running git diff:\n{diff.stderr}"]

    if not diff.stdout.strip():
        status_body = status.stdout.strip() or "(clean)"
        return [f"No unstaged changes in '{agent.name}'.\n\nStatus:\n{status_body}"]

    body = diff.stdout
    truncated = False
    if len(body) > MAX_DIFF_BYTES:
        body = body[:MAX_DIFF_BYTES]
        truncated = True

    chunks = _chunk_code(body)
    header = f"Diff for '{agent.name}' ({agent.repo_path.name}):"
    messages = [header] + chunks
    if truncated:
        messages.append("(diff truncated — use /logs for full tail)")
    return messages


async def run_commit(agent: Agent, message: str) -> str:
    """Stage everything then commit with the given message."""

    result = await git_commit(agent.repo_path, message)
    if result.ok:
        return f"Committed on '{agent.name}':\n{result.stdout.strip()}"
    return f"Commit failed:\n{result.output.strip()}"


async def run_push(agent: Agent) -> str:
    """Push the agent's current branch to origin."""

    branch = await get_current_branch(agent.repo_path)
    result = await git_push(agent.repo_path)
    if result.ok:
        # git push writes human-readable progress to stderr, not stdout.
        body = (result.stderr or result.stdout).strip()
        return f"Pushed '{branch}' on '{agent.name}':\n{body}"
    return f"Push failed:\n{result.stderr.strip()}"


async def run_pr(agent: Agent, title: str) -> str:
    """Commit, push, and open a PR in one step."""

    result = await create_pr(agent.repo_path, title)
    if result.ok:
        return f"PR created: {result.url}"
    return f"PR failed:\n{result.error}"


async def describe_push(agent: Agent) -> str:
    """Return a human-readable description of what a push would do."""

    branch = await get_current_branch(agent.repo_path)
    return f"Push branch '{branch}' on agent '{agent.name}' to origin?"


def describe_pr(agent: Agent, title: str) -> str:
    """Return a human-readable description of what a PR would do."""

    return f"Open PR on '{agent.name}' with title:\n{title}"


def _chunk_code(text: str, max_len: int = 3500) -> list[str]:
    """Split a long code block into Telegram-safe fenced chunks."""

    if len(text) <= max_len:
        return [f"```\n{text}\n```"]

    chunks = []
    start = 0
    while start < len(text):
        chunks.append(f"```\n{text[start:start + max_len]}\n```")
        start += max_len
    return chunks
