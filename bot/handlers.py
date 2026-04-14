"""Telegram command handlers for the chat-first hybrid bot."""

import functools
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from agents.manager import AgentStatus, agent_manager
from agents.runner import get_logs
from bot.chat_state import chat_state
from bot.confirmations import confirmations
from bot.git_actions import (
    describe_pr,
    describe_push,
    render_diff,
    run_commit,
)
from config import settings
from tmux.controller import tmux


HELP_TEXT = (
    "Codex Agent Bot\n\n"
    "Plain text is forwarded to the active agent's Codex session.\n"
    "Fast-paths (exact phrases):\n"
    "  show me the diff\n"
    "  commit it: <message>\n"
    "  push it\n"
    "  open a pr: <title>\n\n"
    "Commands:\n"
    "/new <name> [repo_path] - Create agent\n"
    "/use <name> - Set active agent for this chat\n"
    "/agents - List agents (tap to select)\n"
    "/delete <name> - Delete agent\n"
    "/status - Show all agents\n"
    "/logs - Tail active agent log\n"
    "/stop - Cancel running task on active agent\n"
    "/diff - Git diff (active agent)\n"
    "/commit <message> - Commit (active agent)\n"
    "/push - Git push (active agent)\n"
    "/pr <title> - Create PR (active agent)\n"
)


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


# -------------------- helpers --------------------

def _resolve_active(chat_id: int):
    """Return the active agent for a chat, falling back to lone-agent auto-select."""

    name = chat_state.get_active(chat_id)
    if name and agent_manager.has_agent(name):
        return agent_manager.get_agent(name)

    if name:
        chat_state.clear_active(chat_id)

    all_agents = agent_manager.list_agents()
    if len(all_agents) == 1:
        only = all_agents[0]
        chat_state.set_active(chat_id, only.name)
        return only
    return None


async def _require_active(update: Update):
    """Ensure an active agent exists or explain how to set one."""

    agent = _resolve_active(update.effective_chat.id)
    if agent is not None:
        return agent

    all_agents = agent_manager.list_agents()
    if not all_agents:
        await update.message.reply_text(
            "No agents yet. Create one with /new <name> [repo_path]."
        )
    else:
        names = ", ".join(a.name for a in all_agents)
        await update.message.reply_text(
            f"No active agent. Pick one with /use <name> or /agents. Available: {names}"
        )
    return None


def _split_plain(text: str, max_len: int = 3500) -> list[str]:
    """Split long text into Telegram-safe chunks."""

    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks


# -------------------- commands --------------------

@auth_required
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message."""

    await update.message.reply_text(HELP_TEXT)


@auth_required
async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command reference."""

    await update.message.reply_text(HELP_TEXT)


@auth_required
async def new_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a new agent and offer a quick-select button."""

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /new <name> [repo_path]")
        return

    name = args[0]
    repo_path = Path(args[1]) if len(args) > 1 else None

    try:
        agent = await agent_manager.create_agent(name, repo_path)
    except (ValueError, RuntimeError) as e:
        await update.message.reply_text(f"Error: {e}")
        return

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Use this agent", callback_data=f"use:{name}")]]
    )
    await update.message.reply_text(
        f"Agent '{name}' created.\nRepo: {agent.repo_path}\nStatus: {agent.status.value}",
        reply_markup=keyboard,
    )


@auth_required
async def use_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the active agent for this chat."""

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /use <name>")
        return

    name = args[0]
    if not agent_manager.has_agent(name):
        await update.message.reply_text(f"Agent '{name}' not found.")
        return

    chat_state.set_active(update.effective_chat.id, name)
    await update.message.reply_text(f"Active agent set to '{name}'.")


@auth_required
async def agents_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List agents with inline select buttons."""

    agents = agent_manager.list_agents()
    if not agents:
        await update.message.reply_text("No agents. Create one with /new <name>.")
        return

    active_name = chat_state.get_active(update.effective_chat.id)
    buttons = []
    for a in agents:
        marker = " ✓" if a.name == active_name else ""
        buttons.append(
            [InlineKeyboardButton(f"{a.name}{marker}", callback_data=f"use:{a.name}")]
        )

    await update.message.reply_text(
        "Agents (tap to select):",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@auth_required
async def delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete an agent and clear its active-agent pointers."""

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /delete <name>")
        return

    name = args[0]
    try:
        await agent_manager.delete_agent(name)
    except ValueError as e:
        await update.message.reply_text(f"Error: {e}")
        return

    chat_state.clear_agent_everywhere(name)
    await update.message.reply_text(f"Agent '{name}' deleted.")


@auth_required
async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Summarize every agent and highlight the active one."""

    agents = agent_manager.list_agents()
    if not agents:
        await update.message.reply_text("No agents.")
        return

    active_name = chat_state.get_active(update.effective_chat.id)
    lines = ["Agents:"]
    icons = {"idle": "○", "running": "●", "error": "✗"}
    for a in agents:
        marker = " ← active" if a.name == active_name else ""
        task_info = f"\n    Task: {a.current_task}" if a.current_task else ""
        lines.append(f"{icons[a.status.value]} {a.name} [{a.status.value}]{marker}{task_info}")

    await update.message.reply_text("\n".join(lines))


@auth_required
async def logs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tail the active agent's log file."""

    agent = await _require_active(update)
    if agent is None:
        return

    logs = await get_logs(agent)
    for chunk in _split_plain(logs):
        await update.message.reply_text(f"```\n{chunk}\n```", parse_mode="Markdown")


@auth_required
async def stop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send Ctrl-C to the active agent's tmux session."""

    agent = await _require_active(update)
    if agent is None:
        return

    if not agent.current_task:
        await update.message.reply_text(f"Agent '{agent.name}' is not running anything.")
        return

    # C-c sends SIGINT to whatever Codex is doing in the pane.
    await tmux.send_command(agent.name, "C-c")
    agent.status = AgentStatus.ERROR
    task = agent.current_task
    agent.current_task = None
    await update.message.reply_text(f"Stop signal sent to '{agent.name}'. Task was: {task}")


# -------------------- git backup commands --------------------

@auth_required
async def diff_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command backup for `show me the diff`."""

    agent = await _require_active(update)
    if agent is None:
        return

    messages = await render_diff(agent)
    for msg in messages:
        if msg.startswith("```"):
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            await update.message.reply_text(msg)


@auth_required
async def commit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command backup for `commit it: <msg>`."""

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /commit <message>")
        return

    agent = await _require_active(update)
    if agent is None:
        return

    if agent.current_task:
        await update.message.reply_text(
            f"⚠️ Agent '{agent.name}' is busy. Try again when done."
        )
        return

    message = " ".join(args).strip('"').strip("'")
    result = await run_commit(agent, message)
    await update.message.reply_text(result)


@auth_required
async def push_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command backup for `push it` — posts inline confirmation."""

    agent = await _require_active(update)
    if agent is None:
        return

    if agent.current_task:
        await update.message.reply_text(
            f"⚠️ Agent '{agent.name}' is busy. Try again when done."
        )
        return

    description = await describe_push(agent)
    token = confirmations.create("push", agent.name, update.effective_chat.id)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm push", callback_data=f"confirm:{token}"),
                InlineKeyboardButton("Cancel", callback_data=f"cancel:{token}"),
            ]
        ]
    )
    await update.message.reply_text(description, reply_markup=keyboard)


@auth_required
async def pr_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command backup for `open a pr: <title>` — posts inline confirmation."""

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /pr <title>")
        return

    agent = await _require_active(update)
    if agent is None:
        return

    if agent.current_task:
        await update.message.reply_text(
            f"⚠️ Agent '{agent.name}' is busy. Try again when done."
        )
        return

    title = " ".join(args).strip('"').strip("'")
    description = describe_pr(agent, title)
    token = confirmations.create(
        "pr", agent.name, update.effective_chat.id, payload=title
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm PR", callback_data=f"confirm:{token}"),
                InlineKeyboardButton("Cancel", callback_data=f"cancel:{token}"),
            ]
        ]
    )
    await update.message.reply_text(description, reply_markup=keyboard)
