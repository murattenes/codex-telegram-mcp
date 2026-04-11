from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path


class Settings(BaseSettings):
    telegram_bot_token: str
    allowed_user_ids: list[int] = Field(default_factory=list)
    default_repo_path: Path = Path.home()
    log_dir: Path = Path(__file__).parent / "logs"

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
settings.log_dir.mkdir(exist_ok=True)
