import functools
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from config import settings
from agents.manager import agent_manager
from agents.runner import run_task, get_logs


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
        await update.message.reply_text(
            f"Agent '{agent_name}' is busy with: {agent.current_task}\n"
            "Wait for it to finish or use /queue (Phase 2)."
        )
        return

    await update.message.reply_text(f"Running on '{agent_name}': {task}")

    output = await run_task(agent, task)

    # Split long messages for Telegram's 4096 char limit
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


def _split_message(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks
