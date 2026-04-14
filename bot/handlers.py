"""Telegram command handlers for agent lifecycle and task execution."""

import functools
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from config import settings
from agents.manager import agent_manager, AgentStatus
from agents.runner import run_task, get_logs
from agents.queue import queue_manager
from agents.retry import run_with_retry
from git.operations import (
    get_diff,
    get_status,
    commit as git_commit,
    push as git_push,
    get_current_branch,
)
from git.pr import create_pr


def auth_required(func):
    """Reject commands from users outside the configured allowlist."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not settings.is_user_allowed(user_id):
            await update.message.reply_text("Unauthorized.")
            return
        return await func(update, context)
    return wrapper


@auth_required
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the command reference shown to authenticated users."""

    await update.message.reply_text(
        "Codex Agent Bot ready.\n\n"
        "Commands:\n"
        "/agent create <name> - Create agent\n"
        "/agent list - List agents\n"
        "/agent delete <name> - Delete agent\n"
        "/run <agent> <task> - Run task\n"
        "/continue <agent> - Re-run last task\n"
        "/retry <agent> - Retry with auto-retry\n"
        "/queue <agent> - Show task queue\n"
        "/queue clear <agent> - Clear queue\n"
        "/diff <agent> - Show git diff\n"
        "/commit <agent> <msg> - Commit changes\n"
        "/push <agent> - Push current branch\n"
        "/pr <agent> <title> - Create PR\n"
        "/status - Show status\n"
        "/logs <agent> - Show logs"
    )


@auth_required
async def agent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create, list, or delete named agents."""

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /agent <create|list|delete> [name]")
        return

    action = args[0].lower()

    if action == "create":
        if len(args) < 2:
            await update.message.reply_text("Usage: /agent create <name> [repo_path]")
            return
        name = args[1]
        repo_path = Path(args[2]) if len(args) > 2 else None
        try:
            agent = await agent_manager.create_agent(name, repo_path)
            await update.message.reply_text(
                f"Agent '{name}' created.\n"
                f"Repo: {agent.repo_path}\n"
                f"Status: {agent.status.value}"
            )
        except (ValueError, RuntimeError) as e:
            await update.message.reply_text(f"Error: {e}")

    elif action == "list":
        agents = agent_manager.list_agents()
        if not agents:
            await update.message.reply_text("No agents.")
            return
        lines = []
        for a in agents:
            task_info = f" | Task: {a.current_task}" if a.current_task else ""
            lines.append(f"- {a.name} [{a.status.value}]{task_info}")
        await update.message.reply_text("\n".join(lines))

    elif action == "delete":
        if len(args) < 2:
            await update.message.reply_text("Usage: /agent delete <name>")
            return
        name = args[1]
        try:
            await agent_manager.delete_agent(name)
            await update.message.reply_text(f"Agent '{name}' deleted.")
        except ValueError as e:
            await update.message.reply_text(f"Error: {e}")

    else:
        await update.message.reply_text("Unknown action. Use: create, list, delete")


@auth_required
async def run_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run a task immediately or queue it if the agent is busy."""

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text("Usage: /run <agent> <task description>")
        return

    agent_name = args[0]
    task = " ".join(args[1:])

    if not agent_manager.has_agent(agent_name):
        await update.message.reply_text(f"Agent '{agent_name}' not found. Create it first.")
        return

    agent = agent_manager.get_agent(agent_name)

    if agent.current_task:
        # Busy agents keep work in a per-agent FIFO queue instead of rejecting it.
        task_queue = queue_manager.get_or_create(agent)

        async def notify(msg):
            await update.message.reply_text(msg, parse_mode="Markdown")

        task_queue.set_notify_callback(notify)
        position = await task_queue.enqueue(task)
        await update.message.reply_text(
            f"Agent '{agent_name}' is busy. Task queued at position {position}."
        )
        return

    await update.message.reply_text(f"Running on '{agent_name}': {task}")

    output = await run_task(agent, task)

    for chunk in _split_message(output):
        await update.message.reply_text(f"```\n{chunk}\n```", parse_mode="Markdown")


@auth_required
async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Summarize the current state of all registered agents."""

    agents = agent_manager.list_agents()
    if not agents:
        await update.message.reply_text("No agents created.")
        return

    lines = ["Agent Status:"]
    for a in agents:
        icon = {"idle": "○", "running": "●", "error": "✗"}[a.status.value]
        task_info = f"\n  Task: {a.current_task}" if a.current_task else ""
        lines.append(f"{icon} {a.name} [{a.status.value}]{task_info}")

    await update.message.reply_text("\n".join(lines))


@auth_required
async def logs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return the most recent captured log output for an agent."""

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /logs <agent>")
        return

    agent_name = args[0]
    if not agent_manager.has_agent(agent_name):
        await update.message.reply_text(f"Agent '{agent_name}' not found.")
        return

    agent = agent_manager.get_agent(agent_name)
    logs = await get_logs(agent)

    for chunk in _split_message(logs):
        await update.message.reply_text(f"```\n{chunk}\n```", parse_mode="Markdown")


@auth_required
async def continue_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-run the last task sent to an agent."""

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /continue <agent>")
        return

    agent_name = args[0]
    if not agent_manager.has_agent(agent_name):
        await update.message.reply_text(f"Agent '{agent_name}' not found.")
        return

    agent = agent_manager.get_agent(agent_name)

    if not agent.last_task:
        await update.message.reply_text(f"No previous task for '{agent_name}'.")
        return

    if agent.current_task:
        await update.message.reply_text(f"Agent '{agent_name}' is already running.")
        return

    task = agent.last_task
    await update.message.reply_text(f"Continuing on '{agent_name}': {task}")

    output = await run_task(agent, task)

    for chunk in _split_message(output):
        await update.message.reply_text(f"```\n{chunk}\n```", parse_mode="Markdown")


@auth_required
async def retry_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Retry the last task with exponential backoff on failure."""

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /retry <agent>")
        return

    agent_name = args[0]
    if not agent_manager.has_agent(agent_name):
        await update.message.reply_text(f"Agent '{agent_name}' not found.")
        return

    agent = agent_manager.get_agent(agent_name)

    if not agent.last_task:
        await update.message.reply_text(f"No previous task for '{agent_name}'.")
        return

    if agent.current_task:
        await update.message.reply_text(f"Agent '{agent_name}' is already running.")
        return

    task = agent.last_task
    await update.message.reply_text(
        f"Retrying on '{agent_name}' (max {3} attempts): {task}"
    )

    async def notify(msg):
        await update.message.reply_text(msg)

    output = await run_with_retry(agent, task, notify_callback=notify)

    for chunk in _split_message(output):
        await update.message.reply_text(f"```\n{chunk}\n```", parse_mode="Markdown")


@auth_required
async def queue_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show or clear an agent's pending task queue."""

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /queue <agent> or /queue clear <agent>")
        return

    # Support both inspection and destructive clearing through one command.
    if args[0].lower() == "clear":
        if len(args) < 2:
            await update.message.reply_text("Usage: /queue clear <agent>")
            return
        agent_name = args[1]
        if not agent_manager.has_agent(agent_name):
            await update.message.reply_text(f"Agent '{agent_name}' not found.")
            return
        agent = agent_manager.get_agent(agent_name)
        task_queue = queue_manager.get_or_create(agent)
        count = task_queue.clear()
        await update.message.reply_text(f"Cleared {count} tasks from '{agent_name}' queue.")
        return

    agent_name = args[0]
    if not agent_manager.has_agent(agent_name):
        await update.message.reply_text(f"Agent '{agent_name}' not found.")
        return

    agent = agent_manager.get_agent(agent_name)
    task_queue = queue_manager.get_or_create(agent)
    pending = task_queue.pending_tasks()

    if not pending:
        await update.message.reply_text(f"No pending tasks for '{agent_name}'.")
        return

    lines = [f"Queue for '{agent_name}' ({len(pending)} tasks):"]
    for i, prompt in enumerate(pending, 1):
        lines.append(f"  {i}. {prompt[:80]}")
    await update.message.reply_text("\n".join(lines))


def _resolve_agent(update: Update, agent_name: str):
    """Return the agent if it exists, otherwise None after replying with an error."""

    if not agent_manager.has_agent(agent_name):
        return None
    return agent_manager.get_agent(agent_name)


@auth_required
async def diff_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show unstaged git diff for the agent's repo."""

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /diff <agent>")
        return

    agent = _resolve_agent(update, args[0])
    if not agent:
        await update.message.reply_text(f"Agent '{args[0]}' not found.")
        return

    status = await get_status(agent.repo_path)
    diff = await get_diff(agent.repo_path)

    if not diff.ok:
        await update.message.reply_text(f"Error: {diff.stderr}")
        return

    if not diff.stdout.strip():
        await update.message.reply_text(
            f"No unstaged changes in '{agent.name}'.\n\nStatus:\n{status.stdout or '(clean)'}"
        )
        return

    header = f"Diff for '{agent.name}' ({agent.repo_path.name}):"
    await update.message.reply_text(header)
    for chunk in _split_message(diff.stdout):
        await update.message.reply_text(f"```\n{chunk}\n```", parse_mode="Markdown")


@auth_required
async def commit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stage everything and create a commit with the given message."""

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text('Usage: /commit <agent> <message>')
        return

    agent = _resolve_agent(update, args[0])
    if not agent:
        await update.message.reply_text(f"Agent '{args[0]}' not found.")
        return

    message = " ".join(args[1:]).strip('"').strip("'")
    result = await git_commit(agent.repo_path, message)

    if result.ok:
        await update.message.reply_text(f"Committed on '{agent.name}':\n{result.stdout}")
    else:
        await update.message.reply_text(f"Commit failed:\n{result.output}")


@auth_required
async def push_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Push the agent's current branch to origin."""

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /push <agent>")
        return

    agent = _resolve_agent(update, args[0])
    if not agent:
        await update.message.reply_text(f"Agent '{args[0]}' not found.")
        return

    branch = await get_current_branch(agent.repo_path)
    await update.message.reply_text(f"Pushing '{branch}' for '{agent.name}'...")

    result = await git_push(agent.repo_path)
    if result.ok:
        await update.message.reply_text(f"Push ok:\n{result.stderr or result.stdout}")
    else:
        await update.message.reply_text(f"Push failed:\n{result.stderr}")


@auth_required
async def pr_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commit, push, and open a pull request for the agent's repo."""

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text('Usage: /pr <agent> <title>')
        return

    agent = _resolve_agent(update, args[0])
    if not agent:
        await update.message.reply_text(f"Agent '{args[0]}' not found.")
        return

    title = " ".join(args[1:]).strip('"').strip("'")
    await update.message.reply_text(f"Creating PR for '{agent.name}': {title}")

    result = await create_pr(agent.repo_path, title)
    if result.ok:
        await update.message.reply_text(f"PR created: {result.url}")
    else:
        await update.message.reply_text(f"PR failed:\n{result.error}")


def _split_message(text: str, max_len: int = 4000) -> list[str]:
    """Split long output into Telegram-safe message chunks."""

    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks
