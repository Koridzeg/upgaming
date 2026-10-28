"""Minimal SQL migration runner.

Applies `backend/migrations/NNN_name.sql` files in filename order, each exactly once, and
records what was applied in the `schema_migrations` table. Safe to run on every app start.

Usage: python -m investigator.migrate
"""

import asyncio
import logging
from pathlib import Path

import asyncpg

from investigator.config import get_settings

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

# Arbitrary constant identifying "the migration lock" to pg_advisory_lock.
MIGRATION_LOCK_ID = 727_001


async def apply_migrations(database_url: str) -> list[str]:
    """Apply pending migrations and return the versions that were applied."""
    # A dedicated connection rather than the pool: the pool registers the pgvector types on
    # connect, which fails until the first migration has created the extension.
    conn = await asyncpg.connect(database_url)
    try:
        # Serialise concurrent runners (e.g. two app instances starting at once). The second
        # one waits here, then finds nothing left to apply.
        await conn.execute("SELECT pg_advisory_lock($1)", MIGRATION_LOCK_ID)
        try:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version    text PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            rows = await conn.fetch("SELECT version FROM schema_migrations")
            done = {row["version"] for row in rows}

            applied = []
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                version = path.stem
                if version in done:
                    continue
                # One transaction per file: a failing migration leaves no half-applied schema.
                async with conn.transaction():
                    await conn.execute(path.read_text(encoding="utf-8"))
                    await conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES ($1)", version
                    )
                logger.info("Applied migration %s", version)
                applied.append(version)
            return applied
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK_ID)
    finally:
        await conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    applied = asyncio.run(apply_migrations(settings.database_url.get_secret_value()))
    print(f"Applied: {applied}" if applied else "Database schema is up to date.")


if __name__ == "__main__":
    main()
