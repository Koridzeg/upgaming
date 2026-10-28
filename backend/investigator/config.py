"""Application settings.

Values come from environment variables. In local development they can also come from the
`.env` file at the repository root; real environment variables always take precedence, which
is how secrets are injected in production.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # SecretStr hides the value in repr()/logs; call .get_secret_value() only where it's needed.
    database_url: SecretStr
    db_pool_min_size: int = 1
    db_pool_max_size: int = 5

    groq_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    # Model ids are settings, not code: free-tier model line-ups change often.
    groq_model: str = "llama-3.3-70b-versatile"
    gemini_model: str = "gemini-2.5-flash"
    # First = preferred, the rest = fallback. A typo fails at startup, not at the first call.
    llm_provider_order: list[Literal["groq", "gemini"]] = ["groq", "gemini"]
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process. Tests call `get_settings.cache_clear()` to reload."""
    return Settings()
