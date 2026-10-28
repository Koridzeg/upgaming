"""Deployment of the search index: the local database, one chunking strategy, to Neon.

    python -m investigator.deploy                    # local index -> DEPLOY_DATABASE_URL
    python -m investigator.deploy --verify-only      # just query the target

The app itself is deployed by Render from the repository (render.yaml).

Why copy the index instead of loading the data again in production: the embeddings took ~26
minutes of CPU to compute. A database copy moves them as they are, and the embedding_model
table travels with them, so the app verifies it runs the model that made them.

The verification connects through the WebSocket tunnel (port 443), so it also proves the path
for hosts that block Postgres's port.
"""

import argparse
import asyncio
import os
import subprocess
from urllib.parse import urlsplit

import asyncpg

from investigator.config import REPO_ROOT, Settings, get_settings
from investigator.tunnel import asyncpg_url, local_port, start_bridge, tunnelled_url, websocket_url

SNAPSHOT = "investigator_snapshot"


def _on_database(url: str, name: str) -> str:
    return urlsplit(url)._replace(path=f"/{name}").geturl()


async def make_snapshot(source_url: str, strategies: list[str]) -> int:
    """A copy of the local database with only `strategies`, compacted; returns its size."""
    source_db = urlsplit(source_url).path.lstrip("/")
    admin = await asyncpg.connect(_on_database(source_url, "postgres"))
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {SNAPSHOT}")
        # A file-level copy: seconds. It needs no other session on the source database.
        # FILE_COPY copies the data files after a checkpoint; the default since Postgres 15
        # (WAL_LOG) writes the whole 740 MB through the WAL, and crashed the local server once.
        await admin.execute(
            f'CREATE DATABASE {SNAPSHOT} TEMPLATE "{source_db}" STRATEGY = FILE_COPY'
        )
    finally:
        await admin.close()

    conn = await asyncpg.connect(_on_database(source_url, SNAPSHOT))
    try:
        for table in ("chunks", "term_stats", "corpus_stats"):
            await conn.execute(f"DELETE FROM {table} WHERE strategy <> ALL($1::text[])", strategies)
        indexes = await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE tablename = 'chunks' AND indexdef LIKE '%USING hnsw%'"
        )
        for index in indexes:  # one partial HNSW index per strategy
            if not any(f"'{s}'" in index["indexdef"] for s in strategies):
                await conn.execute(f'DROP INDEX "{index["indexname"]}"')
        # Deleted rows still occupy their pages until the table is rewritten.
        await conn.execute("VACUUM FULL")
        await conn.execute("ANALYZE")
        return await conn.fetchval("SELECT pg_database_size(current_database())")
    finally:
        await conn.close()


def restore_snapshot(target_url: str) -> None:
    """pg_dump the snapshot straight into pg_restore on the target. Both run inside the local
    database container, which has the Postgres 17 tools; the target URL is passed as an
    environment variable, so it never appears on a command line."""
    script = (
        f"pg_dump -U investigator --format=custom {SNAPSHOT} | pg_restore --no-owner "
        '--no-acl --clean --if-exists --exit-on-error --dbname="$TARGET_URL"'
    )
    subprocess.run(
        ["docker", "compose", "exec", "-T", "-e", "TARGET_URL", "-e", "PGOPTIONS",
         "db", "sh", "-c", script],
        cwd=REPO_ROOT,
        # More memory lets the target build the HNSW graph in RAM instead of spilling to disk.
        env=os.environ | {"TARGET_URL": target_url, "PGOPTIONS": "-c maintenance_work_mem=256MB"},
        check=True,
    )  # fmt: skip


async def verify_through_tunnel(target_url: str) -> dict:
    """Query the target over the WebSocket tunnel (port 443)."""
    url = asyncpg_url(target_url)
    bridge = await start_bridge(websocket_url(url))
    try:
        conn = await asyncpg.connect(tunnelled_url(url, local_port(bridge)))
        try:
            row = await conn.fetchrow(
                "SELECT (SELECT count(*) FROM documents) AS documents, "
                "(SELECT count(*) FROM chunks) AS chunks, "
                "(SELECT array_agg(DISTINCT strategy) FROM chunks) AS strategies, "
                "pg_size_pretty(pg_database_size(current_database())) AS size"
            )
            return dict(row)
        finally:
            await conn.close()
    finally:
        bridge.close()


async def publish_database(settings: Settings, verify_only: bool, reuse_snapshot: bool) -> None:
    if settings.deploy_database_url is None:
        raise SystemExit("Set DEPLOY_DATABASE_URL in .env (Neon's direct connection string).")
    target = settings.deploy_database_url.get_secret_value()
    if not verify_only:
        if not reuse_snapshot:
            size = await make_snapshot(settings.database_url.get_secret_value(),
                                       settings.chunk_strategies)  # fmt: skip
            print(f"snapshot ({', '.join(settings.chunk_strategies)}): {size / 2**20:.0f} MB")
        print("restoring into the target (several minutes: upload, then index builds)...")
        restore_snapshot(target)
    print("through the WebSocket tunnel:", await verify_through_tunnel(target))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verify-only", action="store_true", help="only query the target")
    parser.add_argument(
        "--reuse-snapshot",
        action="store_true",
        help="restore the existing snapshot (making one needs no other session on the source)",
    )
    args = parser.parse_args()
    asyncio.run(publish_database(get_settings(), args.verify_only, args.reuse_snapshot))


if __name__ == "__main__":
    main()
