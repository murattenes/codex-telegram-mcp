"""Application entry point for the Telegram-driven Codex runner."""

import logging
import sys

from dotenv import load_dotenv

# Load environment variables before importing modules that read settings.
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from bot.app import run_bot

if __name__ == "__main__":
    run_bot()
