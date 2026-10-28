"""Writing documents and their chunks: add, update (with version history) and delete.

Ingesting is idempotent per `source_id` (the document's stable external key):

    same source, same content      -> 'unchanged'  nothing rebuilt (missing strategies are added)
    same source, new content       -> 'updated'    old version archived, chunks rebuilt
    new source, content stored
      under another source         -> 'duplicate'  not stored; points at the existing document
    otherwise                      -> 'created'
"""

import logging
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

import asyncpg

from investigator.embedding import Embedder, embedding_input, ensure_embedding_model
from investigator.ingestion.chunking import CHUNKERS, Chunk
from investigator.ingestion.dedup import NEAR_DUPLICATE_MAX_DISTANCE, simhash
from investigator.ingestion.models import LoadedDocument
from investigator.ingestion.tokenizer import Tokenizer
from investigator.text import content_hash

logger = logging.getLogger(__name__)

Status = Literal["created", "updated", "unchanged", "duplicate"]


@dataclass
class IngestResult:
    status: Status
    source_id: str
    document_id: UUID | None = None
    version: int | None = None
    chunk_count: int = 0
    duplicate_of: str | None = None
    near_duplicates: list[dict] = field(default_factory=list)


async def ingest_document(
    conn: asyncpg.Connection,
    document: LoadedDocument,
    *,
    source_id: str,
    source_type: str,
    strategies: list[str],
    tokenizer: Tokenizer,
    embedder: Embedder | None = None,  # None: chunks are stored without vectors (tests)
) -> IngestResult:
    content = document.text
    digest = content_hash(content)

    # Fast path, without a transaction: re-uploading an unchanged file costs one query instead
    # of chunking and embedding it again. The authoritative check is repeated under the lock.
    stored = await conn.fetchrow(
        """
        SELECT d.id, d.version, array_agg(DISTINCT c.strategy) AS strategies
          FROM documents d LEFT JOIN chunks c ON c.document_id = d.id
         WHERE d.source_id = $1 AND d.content_hash = $2
         GROUP BY d.id
        """,
        source_id,
        digest,
    )
    if stored and set(strategies) <= set(stored["strategies"]):
        return IngestResult("unchanged", source_id, stored["id"], stored["version"])

    # CPU-heavy work happens before the transaction, so no lock is held while it runs.
    fingerprint = simhash(content)
    chunks = {name: CHUNKERS[name](document, tokenizer) for name in strategies}
    if embedder is not None:
        embed_chunks([c for strategy_chunks in chunks.values() for c in strategy_chunks], embedder)

    try:
        async with conn.transaction():
            if embedder is not None:
                await ensure_embedding_model(conn, embedder)
            return await _write(conn, document, source_id, source_type, digest, fingerprint, chunks)
    except asyncpg.UniqueViolationError as exc:
        # The duplicate check inside _write saw no twin, but a concurrent ingest stored the
        # same content first; the UNIQUE index caught it. Same outcome as a normal duplicate.
        if exc.constraint_name != "documents_content_hash_key":
            raise
        twin = await conn.fetchval(
            "SELECT source_id FROM documents WHERE content_hash = $1", digest
        )
        return IngestResult("duplicate", source_id, duplicate_of=twin)


async def _write(
    conn: asyncpg.Connection,
    document: LoadedDocument,
    source_id: str,
    source_type: str,
    digest: str,
    fingerprint: int,
    chunks: dict[str, list[Chunk]],
) -> IngestResult:
    # Two ingests of the same source_id run one after the other, never interleaved.
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", source_id)
    existing = await conn.fetchrow(
        "SELECT id, content_hash, version FROM documents WHERE source_id = $1", source_id
    )

    if existing and existing["content_hash"] == digest:
        present = {
            row["strategy"]
            for row in await conn.fetch(
                "SELECT DISTINCT strategy FROM chunks WHERE document_id = $1", existing["id"]
            )
        }
        missing = {name: c for name, c in chunks.items() if name not in present}
        added = await insert_chunks(conn, existing["id"], missing)
        return IngestResult("unchanged", source_id, existing["id"], existing["version"], added)

    twin = await conn.fetchval("SELECT source_id FROM documents WHERE content_hash = $1", digest)
    if twin is not None:
        return IngestResult("duplicate", source_id, duplicate_of=twin)

    near = await _near_duplicates(conn, fingerprint, exclude=existing["id"] if existing else None)
    metadata = document.metadata | ({"near_duplicates": near} if near else {})

    if existing:
        await conn.execute(
            """
            INSERT INTO document_versions
                (document_id, version, title, content, content_hash, metadata)
            SELECT id, version, title, content, content_hash, metadata
              FROM documents
             WHERE id = $1
            """,
            existing["id"],
        )
        await conn.execute("DELETE FROM chunks WHERE document_id = $1", existing["id"])
        row = await conn.fetchrow(
            """
            UPDATE documents
               SET title = $2, file_format = $3, content = $4, content_hash = $5, simhash = $6,
                   metadata = $7, version = version + 1, updated_at = now()
             WHERE id = $1
            RETURNING id, version
            """,
            existing["id"], document.title, document.file_format, document.text, digest,
            fingerprint, metadata,
        )  # fmt: skip
        status: Status = "updated"
    else:
        row = await conn.fetchrow(
            """
            INSERT INTO documents (source_id, title, source_type, file_format,
                                   content, content_hash, simhash, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id, version
            """,
            source_id, document.title, source_type, document.file_format, document.text, digest,
            fingerprint, metadata,
        )  # fmt: skip
        status = "created"

    count = await insert_chunks(conn, row["id"], chunks)
    return IngestResult(status, source_id, row["id"], row["version"], count, near_duplicates=near)


def embed_chunks(chunks: list[Chunk], embedder: Embedder) -> None:
    """Set `chunk.embedding` on every chunk, embedding each distinct input text only once.

    Identical inputs are common: a document shorter than the chunk size produces the very same
    chunk under both strategies.
    """
    inputs = [embedding_input(c.context, c.text) for c in chunks]
    distinct = list(dict.fromkeys(inputs))
    vectors = dict(zip(distinct, embedder.embed_documents(distinct), strict=True))
    for chunk, text in zip(chunks, inputs, strict=True):
        chunk.embedding = vectors[text]


async def insert_chunks(
    conn: asyncpg.Connection, document_id: UUID, chunks: dict[str, list[Chunk]]
) -> int:
    records = [
        (document_id, strategy, c.index, c.text, c.context, c.token_count, c.metadata, c.embedding)
        for strategy, strategy_chunks in chunks.items()
        for c in strategy_chunks
    ]
    await conn.executemany(
        """
        INSERT INTO chunks
            (document_id, strategy, chunk_index, text, context, token_count, metadata, embedding)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        records,
    )
    return len(records)


async def _near_duplicates(
    conn: asyncpg.Connection, fingerprint: int, exclude: UUID | None
) -> list[dict]:
    """Stored documents whose SimHash is within the near-duplicate distance.

    A full scan computing XOR + popcount per row: a few milliseconds for 66k documents.
    """
    rows = await conn.fetch(
        """
        SELECT source_id, bit_count(int8send(simhash # $1)) AS distance
          FROM documents
         WHERE simhash IS NOT NULL
           AND id IS DISTINCT FROM $2
           AND bit_count(int8send(simhash # $1)) <= $3
         ORDER BY distance
         LIMIT 5
        """,
        fingerprint,
        exclude,
        NEAR_DUPLICATE_MAX_DISTANCE,
    )
    return [{"source_id": r["source_id"], "distance": r["distance"]} for r in rows]


async def delete_document(conn: asyncpg.Connection, document_id: UUID) -> bool:
    """Delete a document; its chunks and version history go with it (ON DELETE CASCADE)."""
    deleted = await conn.fetchval("DELETE FROM documents WHERE id = $1 RETURNING id", document_id)
    return deleted is not None
