"""Message router that classifies plain text and dispatches to bot or Codex."""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from agents.manager import Agent, AgentStatus, agent_manager
from agents.queue import queue_manager
from agents.runner import run_task
from bot.chat_state import chat_state
from bot.classifier import Intent, IntentKind, classify
from bot.confirmations import confirmations
from bot.git_actions import (
    describe_pr,
    describe_push,
    render_diff,
    run_commit,
    run_pr,
    run_push,
)

logger = logging.getLogger(__name__)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route a plain-text message to a fast-path or to Codex."""

    text = update.message.text or ""
    chat_id = update.effective_chat.id

    agent = _resolve_active_agent(chat_id)
    if agent is None:
        await _reply_no_active_agent(update)
        return

    intent = classify(text)

    # Read-only fast-paths run even when the agent is busy.
    if intent.kind == IntentKind.DIFF:
        await _handle_diff(update, agent)
        return

    # Destructive fast-paths are rejected while a task is running.
    if intent.kind in (IntentKind.COMMIT, IntentKind.PUSH, IntentKind.PR):
        if agent.current_task:
            await update.message.reply_text(
                f"⚠️ Agent '{agent.name}' is busy. Try again when done."
            )
            return
        await _handle_destructive(update, agent, intent)
        return

    # Passthrough: forward the message to Codex.
    await _handle_passthrough(update, agent, text)


def _resolve_active_agent(chat_id: int) -> Agent | None:
    """Return the agent active in this chat, applying one-agent auto-select."""

    name = chat_state.get_active(chat_id)
    if name and agent_manager.has_agent(name):
        return agent_manager.get_agent(name)

    # Clear stale pointer if the agent was deleted elsewhere.
    if name:
        chat_state.clear_active(chat_id)

    all_agents = agent_manager.list_agents()
    if len(all_agents) == 1:
        only = all_agents[0]
        chat_state.set_active(chat_id, only.name)
        return only

    return None


async def _reply_no_active_agent(update: Update):
    """Explain why nothing ran and how to pick an agent."""

    all_agents = agent_manager.list_agents()
    if not all_agents:
        await update.message.reply_text(
            "No agents yet. Create one with /new <name> [repo_path]."
        )
        return

    names = ", ".join(a.name for a in all_agents)
    await update.message.reply_text(
        f"Multiple agents available ({names}). "
        f"Pick one with /use <name> or /agents, then send your message again."
    )


async def _handle_diff(update: Update, agent: Agent):
    """Render the agent's git diff in Telegram-safe chunks."""

    messages = await render_diff(agent)
    for msg in messages:
        if msg.startswith("```"):
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            await update.message.reply_text(msg)


async def _handle_destructive(update: Update, agent: Agent, intent: Intent):
    """Execute commit immediately or post confirmation for push/PR."""

    if intent.kind == IntentKind.COMMIT:
        result = await run_commit(agent, intent.payload)
        await update.message.reply_text(result)
        return

    if intent.kind == IntentKind.PUSH:
        description = await describe_push(agent)
        token = confirmations.create("push", agent.name, update.effective_chat.id)
        await update.message.reply_text(
            description,
            reply_markup=_confirm_keyboard("push", token),
        )
        return

    if intent.kind == IntentKind.PR:
        description = describe_pr(agent, intent.payload)
        token = confirmations.create(
            "pr", agent.name, update.effective_chat.id, payload=intent.payload
        )
        await update.message.reply_text(
            description,
            reply_markup=_confirm_keyboard("pr", token),
        )
        return


async def _handle_passthrough(update: Update, agent: Agent, text: str):
    """Queue the text as a Codex task, or run it immediately if idle."""

    if agent.current_task:
        task_queue = queue_manager.get_or_create(agent)
        chat_id = update.effective_chat.id

        async def notify(msg: str):
            # Queue consumer uses this to ship final results back to the user.
            await context_bot_send(update, chat_id, msg)

        task_queue.set_notify_callback(notify)
        position = await task_queue.enqueue(text)
        await update.message.reply_text(
            f"📥 Queued on '{agent.name}' (position {position})."
        )
        return

    await update.message.reply_text(
        f"⏳ Running on '{agent.name}': {text[:100]}"
    )

    output = await run_task(agent, text)

    status_icon = "❌ Failed" if agent.status == AgentStatus.ERROR else "✅ Done"
    for chunk in _split_plain(output):
        await update.message.reply_text(
            f"{status_icon}\n```\n{chunk}\n```", parse_mode="Markdown"
        )


async def context_bot_send(update: Update, chat_id: int, text: str):
    """Send a message through the bot object attached to the Update."""

    await update.get_bot().send_message(chat_id=chat_id, text=text)


def _confirm_keyboard(action: str, token: str) -> InlineKeyboardMarkup:
    """Build a two-button [Confirm] [Cancel] keyboard."""

    confirm_label = "Confirm push" if action == "push" else "Confirm PR"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(confirm_label, callback_data=f"confirm:{token}"),
                InlineKeyboardButton("Cancel", callback_data=f"cancel:{token}"),
            ]
        ]
    )


def _split_plain(text: str, max_len: int = 3500) -> list[str]:
    """Split long text into Telegram-safe chunks."""

    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dispatch inline-keyboard button presses."""

    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data.startswith("confirm:") or data.startswith("cancel:"):
        await _handle_confirm_callback(update, data)
        return

    if data.startswith("use:"):
        await _handle_use_callback(update, data)
        return

    await query.edit_message_text("Unknown action.")


async def _handle_confirm_callback(update: Update, data: str):
    """Handle push/PR confirmation or cancellation."""

    action_label, token = data.split(":", 1)
    pending = confirmations.pop(token)

    query = update.callback_query

    if pending is None:
        await query.edit_message_text("Confirmation expired, try again.")
        return

    if action_label == "cancel":
        await query.edit_message_text(f"Cancelled {pending.action}.")
        return

    if not agent_manager.has_agent(pending.agent_name):
        await query.edit_message_text(
            f"Agent '{pending.agent_name}' no longer exists."
        )
        return

    agent = agent_manager.get_agent(pending.agent_name)

    if agent.current_task:
        await query.edit_message_text(
            f"⚠️ Agent '{agent.name}' is busy. Try again when done."
        )
        return

    if pending.action == "push":
        await query.edit_message_text(f"Pushing '{agent.name}'...")
        result = await run_push(agent)
        await query.edit_message_text(result)
        return

    if pending.action == "pr":
        await query.edit_message_text(f"Creating PR on '{agent.name}'...")
        result = await run_pr(agent, pending.payload)
        await query.edit_message_text(result)
        return


async def _handle_use_callback(update: Update, data: str):
    """Set the active agent from an inline button press."""

    _, name = data.split(":", 1)
    query = update.callback_query

    if not agent_manager.has_agent(name):
        await query.edit_message_text(f"Agent '{name}' no longer exists.")
        return

    chat_state.set_active(update.effective_chat.id, name)
    await query.edit_message_text(f"Active agent set to '{name}'.")
