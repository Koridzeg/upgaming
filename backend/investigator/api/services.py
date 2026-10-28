"""Everything a request needs, built once at startup and shared by all requests."""

import asyncio
import logging
from dataclasses import dataclass, field

import asyncpg
from fastapi import HTTPException, Request

from investigator.api.ratelimit import RateLimiter
from investigator.config import Settings
from investigator.embedding import Embedder, get_embedder
from investigator.ingestion.tokenizer import Tokenizer, get_tokenizer
from investigator.llm.client import LLMClient, client_from_settings
from investigator.retrieval.keyword import refresh_term_stats

logger = logging.getLogger(__name__)


class StatsRefresher:
    """Recomputes BM25 term statistics after uploads, in the background.

    One refresh at a time: uploads arriving during a refresh set a flag, and one more refresh
    runs afterwards, instead of one per upload. Until it finishes, new words are still found
    (keyword search treats unknown terms as rare), so staleness is harmless.
    """

    def __init__(self, pool: asyncpg.Pool, strategies: list[str]):
        self.pool, self.strategies = pool, strategies
        self._lock = asyncio.Lock()
        self._again = False

    async def refresh(self) -> None:
        if self._lock.locked():
            self._again = True
            return
        async with self._lock:
            while True:
                self._again = False
                async with self.pool.acquire() as conn:
                    for strategy in self.strategies:
                        await refresh_term_stats(conn, strategy)
                if not self._again:
                    return


@dataclass
class Services:
    settings: Settings
    pool: asyncpg.Pool
    embedder: Embedder
    tokenizer: Tokenizer
    llm: LLMClient | None  # None when no API key is configured: search still works
    limiter: RateLimiter
    stats: StatsRefresher
    embedding_model_matches: bool = True
    notes: list[str] = field(default_factory=list)

    async def aclose(self) -> None:
        if self.llm is not None:
            await self.llm.aclose()


async def build_services(settings: Settings, pool: asyncpg.Pool) -> Services:
    embedder = get_embedder()
    notes = []
    try:
        llm = client_from_settings(settings)
    except ValueError as exc:  # no key at all: the app still serves documents and search
        llm = None
        notes.append(str(exc))
    async with pool.acquire() as conn:
        stored = await conn.fetchval("SELECT sha256 FROM embedding_model")
    matches = stored is None or stored == embedder.sha256
    if not matches:
        # Queries embedded by another model than the stored vectors would rank nonsense.
        logger.error("embedding model mismatch: index %s, app %s", stored, embedder.sha256)
    return Services(
        settings=settings,
        pool=pool,
        embedder=embedder,
        tokenizer=get_tokenizer(),
        llm=llm,
        limiter=RateLimiter(settings.investigations_per_minute, settings.investigations_per_day),
        stats=StatsRefresher(pool, settings.chunk_strategies),
        embedding_model_matches=matches,
        notes=notes,
    )


def get_services(request: Request) -> Services:
    return request.app.state.services


def require_llm(services: Services) -> LLMClient:
    if services.llm is None:
        raise HTTPException(503, "No LLM provider is configured (set GROQ_API_KEY).")
    return services.llm
