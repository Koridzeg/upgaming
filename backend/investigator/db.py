"""PostgreSQL connection pool shared by the whole application."""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from pgvector.asyncpg import register_vector

from investigator.config import Settings
from investigator.tunnel import (
    asyncpg_url,
    describe_url,
    local_port,
    start_bridge,
    tunnelled_url,
    websocket_url,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def database_url(settings: Settings) -> AsyncIterator[str]:
    """The URL the app connects with: the configured one, or a local bridge to it over a
    WebSocket when `database_transport` is "websocket". The bridge lives as long as the app."""
    url = asyncpg_url(settings.database_url.get_secret_value())
    # A wrong DATABASE_URL on the host otherwise shows up only as "Connection refused".
    logger.info("database: %s over %s", describe_url(url), settings.database_transport)
    if settings.database_transport == "tcp":
        yield url
        return
    bridge = await start_bridge(websocket_url(url))
    try:
        yield tunnelled_url(url, local_port(bridge))
    finally:
        bridge.close()


async def init_connection(conn: asyncpg.Connection) -> None:
    """Runs once for every new pool connection; scripts and tests call it on their own ones."""
    # Teach asyncpg to convert pgvector columns to/from numpy arrays.
    await register_vector(conn)
    # By default asyncpg returns jsonb as a raw string; decode it to Python objects instead.
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(settings: Settings, url: str | None = None) -> asyncpg.Pool:
    """A pool on `url` (from `database_url`), or straight to the configured database."""
    return await asyncpg.create_pool(
        dsn=url or asyncpg_url(settings.database_url.get_secret_value()),
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        init=init_connection,
        # Neon suspends idle databases and drops their connections. Recycling idle connections
        # after 60s means the pool rarely hands out a connection the server has already closed.
        max_inactive_connection_lifetime=60,
    )
