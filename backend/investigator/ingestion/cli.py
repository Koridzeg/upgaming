"""Ingest files from the command line (the API will call the same functions).

Usage:
    python -m investigator.ingestion.cli FILE [FILE ...] [--strategies fixed,structural]
"""

import argparse
import asyncio
from pathlib import Path

import asyncpg

from investigator.config import get_settings
from investigator.db import init_connection
from investigator.ingestion.loaders import load_document
from investigator.ingestion.models import DocumentError
from investigator.ingestion.store import ingest_document
from investigator.ingestion.tokenizer import get_tokenizer
from investigator.migrate import apply_migrations


async def ingest_files(paths: list[Path], strategies: list[str]) -> None:
    url = get_settings().database_url.get_secret_value()
    await apply_migrations(url)
    conn = await asyncpg.connect(url)
    await init_connection(conn)
    tokenizer = get_tokenizer()
    try:
        for path in paths:
            try:
                document = load_document(path.name, path.read_bytes())
            except DocumentError as exc:
                print(f"{'rejected':<10} {path.name}: {exc}")
                continue
            result = await ingest_document(
                conn,
                document,
                source_id=f"upload:{path.name}",
                source_type="upload",
                strategies=strategies,
                tokenizer=tokenizer,
            )
            detail = f"v{result.version}, {result.chunk_count} chunks" if result.version else ""
            if result.duplicate_of:
                detail = f"same content as {result.duplicate_of}"
            if result.near_duplicates:
                detail += f", near-duplicate of {result.near_duplicates}"
            print(f"{result.status:<10} {path.name}  ({detail})")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--strategies", default="structural", help="comma-separated")
    args = parser.parse_args()
    asyncio.run(ingest_files(args.files, args.strategies.split(",")))


if __name__ == "__main__":
    main()
