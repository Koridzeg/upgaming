"""Migration runner and the behaviour the rest of the system relies on from the schema."""

import asyncio

import asyncpg

from investigator.migrate import MIGRATIONS_DIR, apply_migrations


async def _query(url: str, sql: str):
    conn = await asyncpg.connect(url)
    try:
        return await conn.fetch(sql)
    finally:
        await conn.close()


def test_migrations_apply_once(empty_db):
    all_versions = sorted(p.stem for p in MIGRATIONS_DIR.glob("*.sql"))

    assert asyncio.run(apply_migrations(empty_db)) == all_versions
    # A second run is a no-op: this is what makes it safe to migrate on every app start.
    assert asyncio.run(apply_migrations(empty_db)) == []


def test_core_tables_exist(empty_db):
    asyncio.run(apply_migrations(empty_db))

    sql = "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
    rows = asyncio.run(_query(empty_db, sql))

    assert {"documents", "chunks", "schema_migrations"} <= {r["tablename"] for r in rows}


async def _insert_document_with_chunk(conn: asyncpg.Connection, text: str):
    doc_id = await conn.fetchval(
        """
        INSERT INTO documents (source_id, title, source_type, file_format, content, content_hash)
        VALUES ('test:1', 'Test', 'upload', 'txt', $1, 'hash') RETURNING id
        """,
        text,
    )
    await conn.execute(
        """
        INSERT INTO chunks (document_id, strategy, chunk_index, text, token_count)
        VALUES ($1, 'fixed', 0, $2, 2)
        """,
        doc_id,
        text,
    )
    return doc_id


def test_deleting_a_document_deletes_its_chunks(empty_db):
    async def scenario():
        await apply_migrations(empty_db)
        conn = await asyncpg.connect(empty_db)
        try:
            doc_id = await _insert_document_with_chunk(conn, "payment gateway timeout")
            await conn.execute("DELETE FROM documents WHERE id = $1", doc_id)
            return await conn.fetchval("SELECT count(*) FROM chunks")
        finally:
            await conn.close()

    assert asyncio.run(scenario()) == 0


def test_keyword_index_matches_word_variants(empty_db):
    async def scenario():
        await apply_migrations(empty_db)
        conn = await asyncpg.connect(empty_db)
        try:
            await _insert_document_with_chunk(conn, "The payment services were failing")
            # 'fails' and 'failing' both stem to 'fail', 'service' and 'services' to 'servic'.
            return await conn.fetchval(
                "SELECT count(*) FROM chunks WHERE tsv @@ plainto_tsquery('english', $1)",
                "service fails",
            )
        finally:
            await conn.close()

    assert asyncio.run(scenario()) == 1
