import logging

from telegram.ext import ApplicationBuilder, CommandHandler

from config import settings
from bot.handlers import (
    start_handler,
    agent_handler,
    run_handler,
    status_handler,
    logs_handler,
)

logger = logging.getLogger(__name__)


def create_bot():
    app = ApplicationBuilder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("agent", agent_handler))
    app.add_handler(CommandHandler("run", run_handler))
    app.add_handler(CommandHandler("status", status_handler))
    app.add_handler(CommandHandler("logs", logs_handler))

    logger.info("Bot handlers registered")
    return app


def run_bot():
    app = create_bot()
    logger.info("Starting bot with long polling...")
    app.run_polling(drop_pending_updates=True)
