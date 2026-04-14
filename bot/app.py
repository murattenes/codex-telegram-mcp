"""Telegram bot application wiring and startup helpers."""

import logging

from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from agents.watchdog import watchdog
from bot.handlers import (
    agents_handler,
    auth_required,
    commit_handler,
    delete_handler,
    diff_handler,
    help_handler,
    logs_handler,
    new_handler,
    pr_handler,
    push_handler,
    start_handler,
    status_handler,
    stop_handler,
    use_handler,
)
from bot.router import handle_callback, handle_text
from config import settings

logger = logging.getLogger(__name__)


# Wrap router entry points with auth so unauthorized users never reach them.
text_handler = auth_required(handle_text)
callback_handler = auth_required(handle_callback)


def create_bot():
    """Build the Telegram application and register handlers."""

    app = ApplicationBuilder().token(settings.telegram_bot_token).build()

    # Session / control commands
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("new", new_handler))
    app.add_handler(CommandHandler("use", use_handler))
    app.add_handler(CommandHandler("agents", agents_handler))
    app.add_handler(CommandHandler("delete", delete_handler))
    app.add_handler(CommandHandler("status", status_handler))
    app.add_handler(CommandHandler("logs", logs_handler))
    app.add_handler(CommandHandler("stop", stop_handler))

    # Git backup commands
    app.add_handler(CommandHandler("diff", diff_handler))
    app.add_handler(CommandHandler("commit", commit_handler))
    app.add_handler(CommandHandler("push", push_handler))
    app.add_handler(CommandHandler("pr", pr_handler))

    # Inline button callbacks (use:, confirm:, cancel:)
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Plain text → MessageRouter
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )

    logger.info("Bot handlers registered")
    return app


def run_bot():
    """Start background services and begin Telegram long polling."""

    app = create_bot()
    watchdog.start()
    logger.info("Starting bot with long polling...")
    app.run_polling(drop_pending_updates=True)
