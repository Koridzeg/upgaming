"""Search from the command line and see why each result ranked where it did.

Usage:
    python -m investigator.retrieval "question" [--mode hybrid|dense|keyword]
           [--strategy structural|fixed] [--k 10] [--no-rerank] [--source-type synthetic]
    python -m investigator.retrieval --refresh-stats      # recompute BM25 term statistics
"""

import argparse
import asyncio
import time

import asyncpg

from investigator.config import get_settings
from investigator.db import init_connection
from investigator.embedding import get_embedder
from investigator.ingestion.chunking import CHUNKERS
from investigator.migrate import apply_migrations
from investigator.retrieval.keyword import refresh_term_stats
from investigator.retrieval.search import SearchConfig, search


async def main_async(args: argparse.Namespace) -> None:
    url = get_settings().database_url.get_secret_value()
    await apply_migrations(url)
    conn = await asyncpg.connect(url)
    await init_connection(conn)
    try:
        if args.refresh_stats:
            for strategy in CHUNKERS:
                started = time.perf_counter()
                terms = await refresh_term_stats(conn, strategy)
                print(f"{strategy}: {terms:,} terms in {time.perf_counter() - started:.1f}s")
            return

        config = SearchConfig(
            mode=args.mode,
            strategy=args.strategy,
            k=args.k,
            rerank=not args.no_rerank,
            source_types=args.source_type,
        )
        embedder = get_embedder()
        await search(conn, embedder, "warm-up", config)  # first call loads the model
        started = time.perf_counter()
        hits = await search(conn, embedder, args.query, config)
        print(f"{len(hits)} hits in {1000 * (time.perf_counter() - started):.0f} ms\n")
        for hit in hits:
            s = hit.scores
            ranks = f"dense #{s.get('dense_rank', '-')}, keyword #{s.get('keyword_rank', '-')}"
            extra = f", title {s['title_match']}" if "title_match" in s else ""
            print(f"{s['final_rank']:>2}. {hit.title}  ({ranks}{extra})")
            print(f"    {hit.text[:150].replace(chr(10), ' / ')}")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Search the corpus.")
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--mode", default="hybrid", choices=["hybrid", "dense", "keyword"])
    parser.add_argument("--strategy", default="structural", choices=sorted(CHUNKERS))
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument("--source-type", action="append", help="repeatable filter")
    parser.add_argument("--refresh-stats", action="store_true")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
