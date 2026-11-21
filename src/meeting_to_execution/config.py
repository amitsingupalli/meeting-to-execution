from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Meeting to Execution"
    app_env: str = "development"
    database_url: str = "sqlite:///./data/mte.db"
    checkpoint_db: str = "./data/checkpoints.db"
    vector_backend: str = "local"
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    llm_api_key: str | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 384

    tracker_backend: str = "mock"
    github_token: str | None = None
    github_repo: str | None = None
    github_api_url: str = "https://api.github.com"

    enable_scheduler: bool = True
    reminder_hours_before: int = 48
    digest_day_of_week: str = "mon"
    digest_hour: int = 9
    digest_minute: int = 0
    notification_backend: str = "log"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def use_llm(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def sqlite_path(self) -> Path:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("This project currently supports SQLite DATABASE_URL values.")
        return Path(self.database_url.removeprefix(prefix)).expanduser().resolve()

    def ensure_directories(self) -> None:
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        Path(self.checkpoint_db).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
