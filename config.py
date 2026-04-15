"""Centralized settings loaded from environment variables and defaults."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Runtime configuration for the bot and local agent sessions."""

    telegram_bot_token: str
    allowed_user_ids: list[int] = Field(default_factory=list)
    default_repo_path: Path = Path.home()
    log_dir: Path = Path(__file__).parent / "logs"
    state_dir: Path = Path(__file__).parent / "state"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    def parse_allowed_user_ids(self) -> list[int]:
        return self.allowed_user_ids

    def is_user_allowed(self, user_id: int) -> bool:
        if not self.allowed_user_ids:
            return True
        return user_id in self.allowed_user_ids


settings = Settings()
# Ensure log output and persisted state always have a writable destination.
settings.log_dir.mkdir(exist_ok=True)
settings.state_dir.mkdir(exist_ok=True)
