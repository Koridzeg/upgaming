"""PostgreSQL connection pool shared by the whole application."""

import json

import asyncpg
from pgvector.asyncpg import register_vector

from investigator.config import Settings


async def init_connection(conn: asyncpg.Connection) -> None:
    """Runs once for every new pool connection; scripts and tests call it on their own ones."""
    # Teach asyncpg to convert pgvector columns to/from numpy arrays.
    await register_vector(conn)
    # By default asyncpg returns jsonb as a raw string; decode it to Python objects instead.
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(settings: Settings) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=settings.database_url.get_secret_value(),
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        init=init_connection,
        # Neon suspends idle databases and drops their connections. Recycling idle connections
        # after 60s means the pool rarely hands out a connection the server has already closed.
        max_inactive_connection_lifetime=60,
    )
