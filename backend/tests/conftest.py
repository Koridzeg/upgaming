"""Shared test fixtures. Database tests run against the separate `investigator_test` database."""

import asyncio
import os

import asyncpg
import pytest

from investigator.config import get_settings

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://investigator:investigator@localhost:5433/investigator_test",
)

# Real environment variables override .env, so this points the app at the test database.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL


async def _reset_schema(url: str) -> None:
    conn = await asyncpg.connect(url)
    try:
        # Dropping the schema also drops the vector extension and schema_migrations,
        # so every test starts from a truly empty database.
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await conn.close()


@pytest.fixture
def empty_db() -> str:
    """URL of a test database with no tables or extensions at all."""
    asyncio.run(_reset_schema(TEST_DATABASE_URL))
    get_settings.cache_clear()
    return TEST_DATABASE_URL
