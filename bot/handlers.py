import functools
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from config import settings
from agents.manager import agent_manager, AgentStatus
from agents.runner import run_task, get_logs
from agents.queue import queue_manager
from agents.retry import run_with_retry


def auth_required(func):
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
        "/status - Show status\n"
        "/logs <agent> - Show logs"
    )


@auth_required
async def agent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        # Agent is busy — queue the task
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
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /queue <agent> or /queue clear <agent>")
        return

    # /queue clear <agent>
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

    # /queue <agent>
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


def _split_message(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks
