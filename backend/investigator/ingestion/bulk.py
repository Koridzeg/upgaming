"""Bulk loading of a whole corpus (HotpotQA: 66,581 documents).

Same outcome as calling ingest_document() per document, organised for volume:
- duplicates are found for the whole corpus at once, in memory (a hash dict for exact copies,
  SimHash pigeonhole pairs for near ones), instead of one SQL scan per document;
- chunks are embedded in large batches across many documents, each distinct text once;
- the HNSW vector indexes are dropped first and rebuilt once at the end: building the graph
  in one pass is much faster than inserting ~190k vectors into it one at a time.

Re-running is safe: documents already stored (by source_id) are skipped.

Usage: python -m investigator.ingestion.bulk [--limit N] [--strategies fixed,structural]
"""

import argparse
import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Callable

import asyncpg

from investigator.config import get_settings
from investigator.datasets.hotpotqa import OUT_DIR, read_corpus, to_document
from investigator.db import init_connection
from investigator.embedding import Embedder, ensure_embedding_model, get_embedder
from investigator.ingestion.chunking import CHUNKERS
from investigator.ingestion.dedup import near_duplicate_pairs, simhash
from investigator.ingestion.models import LoadedDocument
from investigator.ingestion.store import embed_chunks, insert_chunks
from investigator.ingestion.tokenizer import Tokenizer, get_tokenizer
from investigator.migrate import apply_migrations
from investigator.text import content_hash

logger = logging.getLogger(__name__)

BATCH_DOCUMENTS = 1000
DUPLICATES_FILE = OUT_DIR / "duplicates.json"  # dropped source_id -> stored source_id


async def bulk_load(
    conn: asyncpg.Connection,
    documents: list[tuple[str, LoadedDocument]],  # (source_id, document)
    *,
    source_type: str,
    strategies: list[str],
    tokenizer: Tokenizer,
    embedder: Embedder,
    progress: Callable[[str], None] = logger.info,
) -> dict:
    await ensure_embedding_model(conn, embedder)

    # 1. Exact duplicates: the first copy wins; the others are remembered as aliases, so the
    #    evaluation can treat "Tara Strong filmography" as the stored "Tara Strong".
    stored = {r["content_hash"]: r["source_id"] for r in await conn.fetch(
        "SELECT source_id, content_hash FROM documents"
    )}  # fmt: skip
    stored_ids = set(stored.values())
    seen = dict(stored)
    new: list[tuple[str, LoadedDocument, str]] = []
    aliases: dict[str, str] = {}
    for source_id, document in documents:
        if source_id in stored_ids:
            continue  # loaded by an earlier run
        digest = content_hash(document.text)
        if digest in seen:
            aliases[source_id] = seen[digest]
            continue
        seen[digest] = source_id
        new.append((source_id, document, digest))

    # 2. Near duplicates within the new documents, linked both ways.
    fingerprints = {source_id: simhash(document.text) for source_id, document, _ in new}
    near: dict[str, list[dict]] = defaultdict(list)
    for a, b, distance in near_duplicate_pairs(fingerprints):
        near[a].append({"source_id": b, "distance": distance})
        near[b].append({"source_id": a, "distance": distance})
    progress(
        f"{len(new):,} new documents, {len(aliases):,} exact duplicates, "
        f"{len(near):,} with near-duplicates"
    )

    # 3. Chunk, embed and insert, batch by batch; the vector indexes are rebuilt at the end.
    index_definitions = await _drop_vector_indexes(conn, progress)
    started, chunk_total = time.perf_counter(), 0
    try:
        for start in range(0, len(new), BATCH_DOCUMENTS):
            batch = new[start : start + BATCH_DOCUMENTS]
            rows, chunk_sets = [], []
            for source_id, document, digest in batch:
                document_id = uuid.uuid4()
                metadata = document.metadata | (
                    {"near_duplicates": near[source_id]} if source_id in near else {}
                )
                rows.append((
                    document_id, source_id, document.title, source_type, document.file_format,
                    document.text, digest, fingerprints[source_id], metadata,
                ))  # fmt: skip
                chunk_sets.append(
                    (
                        document_id,
                        {name: CHUNKERS[name](document, tokenizer) for name in strategies},
                    )
                )
            embed_chunks(
                [c for _, sets in chunk_sets for cs in sets.values() for c in cs], embedder
            )

            async with conn.transaction():
                await conn.executemany(
                    """
                    INSERT INTO documents (id, source_id, title, source_type, file_format,
                                           content, content_hash, simhash, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    rows,
                )
                for document_id, sets in chunk_sets:
                    chunk_total += await insert_chunks(conn, document_id, sets)

            done = start + len(batch)
            rate = done / (time.perf_counter() - started)
            progress(
                f"{done:,}/{len(new):,} documents, {chunk_total:,} chunks, {rate:.0f} docs/s, "
                f"~{(len(new) - done) / rate / 60:.0f} min left"
            )
    finally:
        await _rebuild_indexes(conn, index_definitions, progress)

    return {"loaded": len(new), "duplicates": aliases, "chunks": chunk_total}


async def _drop_vector_indexes(conn: asyncpg.Connection, progress) -> list[str]:
    """Drop the HNSW indexes and return their definitions, to recreate them exactly as they were."""
    rows = await conn.fetch(
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE tablename = 'chunks' AND indexdef LIKE '%USING hnsw%'"
    )
    for row in rows:
        # Printed so the index can be recreated by hand if this process is killed mid-load.
        progress(f"dropping {row['indexname']} until the load ends: {row['indexdef']}")
        await conn.execute(f'DROP INDEX "{row["indexname"]}"')
    return [row["indexdef"] for row in rows]


async def _rebuild_indexes(conn: asyncpg.Connection, definitions: list[str], progress) -> None:
    # More memory lets Postgres build each HNSW graph in RAM instead of spilling to disk.
    await conn.execute("SET maintenance_work_mem = '1GB'")
    for definition in definitions:
        started = time.perf_counter()
        await conn.execute(definition)
        progress(f"rebuilt index in {time.perf_counter() - started:.0f}s: {definition[:70]}...")
    await conn.execute("ANALYZE chunks")  # fresh statistics for the query planner


def _save_duplicates(aliases: dict[str, str]) -> None:
    existing = (
        json.loads(DUPLICATES_FILE.read_text(encoding="utf-8")) if DUPLICATES_FILE.exists() else {}
    )
    DUPLICATES_FILE.write_text(
        json.dumps(existing | aliases, indent=1, ensure_ascii=False), encoding="utf-8"
    )


async def main_async(limit: int | None, strategies: list[str]) -> None:
    url = get_settings().database_url.get_secret_value()
    await apply_migrations(url)
    conn = await asyncpg.connect(url)
    await init_connection(conn)
    try:
        records = read_corpus()[:limit] if limit else read_corpus()
        documents = [(f"hotpotqa:{r['title']}", to_document(r)) for r in records]
        result = await bulk_load(
            conn,
            documents,
            source_type="hotpotqa",
            strategies=strategies,
            tokenizer=get_tokenizer(),
            embedder=get_embedder(),
            progress=lambda message: print(message, flush=True),
        )
        _save_duplicates(result["duplicates"])
        print(f"done: {result['loaded']:,} documents, {result['chunks']:,} chunks")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk-load the HotpotQA corpus.")
    parser.add_argument("--limit", type=int, help="only the first N documents")
    parser.add_argument("--strategies", default="structural", help="comma-separated")
    args = parser.parse_args()
    asyncio.run(main_async(args.limit, args.strategies.split(",")))


if __name__ == "__main__":
    main()
