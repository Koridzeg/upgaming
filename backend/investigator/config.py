"""Application settings.

Values come from environment variables. In local development they can also come from the
`.env` file at the repository root; real environment variables always take precedence, which
is how secrets are injected in production.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
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
    # "websocket" reaches Neon over port 443 through investigator.tunnel, for hosts that block
    # outgoing connections to Postgres's port 5432 (e.g. Hugging Face Spaces).
    database_transport: Literal["tcp", "websocket"] = "tcp"
    # The production database (Neon), only for `python -m investigator.deploy`. The app itself
    # always uses DATABASE_URL, which the host's secret store sets to the same value.
    deploy_database_url: SecretStr | None = None

    groq_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    # The model chain, as "provider:model", in order of preference; the rest are fallbacks.
    # Measured free-tier limits (2026-10-28): every Groq model has its own budget of 1,000
    # requests/day and 8,000 tokens/min, so a second Groq model adds capacity; Gemini allows
    # only 5 requests/min, so it is the last resort (an independent provider, for outages).
    # Model ids are settings, not code: checked 2026-10-28 with `python -m investigator.llm
    # --models`, Groq had retired llama-3.3-70b-versatile, Gemini returned 404 for
    # gemini-2.5-flash and gemini-3.8-flash timed out; these passed the live tool-call check.
    llm_models: list[str] = [
        "groq:openai/gpt-oss-120b",
        "groq:openai/gpt-oss-20b",
        "gemini:gemini-3.5-flash",
    ]
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    # Longest rate-limit wait accepted before falling back to the next model. Interactive use
    # wants a fast fallback; the evaluation sets it higher to stay on one model throughout.
    llm_max_wait_seconds: float = 20.0

    log_level: str = "INFO"

    # Ingestion and search (Step 7 chose structural chunking)
    chunk_strategies: list[str] = ["structural"]
    max_upload_mb: int = 10

    # Protecting the free LLM quota (200k tokens/day/model) on a public demo: investigations
    # per client IP. An investigation costs 3-18k tokens (median ~9k measured).
    investigations_per_minute: int = 3
    investigations_per_day: int = 25

    # The built Angular app, served by the API at "/" when present.
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist" / "frontend" / "browser"
    cors_origins: list[str] = []  # e.g. ["http://localhost:4200"] without the dev proxy

    @field_validator("llm_models")
    @classmethod
    def _known_providers(cls, models: list[str]) -> list[str]:
        """A typo fails at startup, not at the first LLM call."""
        for entry in models:
            provider, sep, model = entry.partition(":")
            if not sep or provider not in ("groq", "gemini") or not model:
                raise ValueError(f"{entry!r}: expected 'groq:<model>' or 'gemini:<model>'")
        return models


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process. Tests call `get_settings.cache_clear()` to reload."""
    return Settings()
