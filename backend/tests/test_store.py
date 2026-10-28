"""Add / update / delete against the test database."""

import asyncio

import asyncpg

from investigator.db import init_connection
from investigator.ingestion.models import Block, LoadedDocument
from investigator.ingestion.store import delete_document, ingest_document
from investigator.ingestion.tokenizer import WhitespaceTokenizer
from investigator.migrate import apply_migrations
from tests.test_dedup import TEXT

TOKENS = WhitespaceTokenizer()


def runbook(text: str, title: str = "Gateway runbook") -> LoadedDocument:
    blocks = [Block("heading", title, level=1), Block("paragraph", text)]
    return LoadedDocument(title, "md", blocks, {"author": "Priya Nair"})


async def connect(url: str) -> asyncpg.Connection:
    await apply_migrations(url)
    conn = await asyncpg.connect(url)
    await init_connection(conn)
    return conn


async def ingest(conn, document, source_id="upload:runbook.md", strategies=("structural",)):
    return await ingest_document(
        conn,
        document,
        source_id=source_id,
        source_type="upload",
        strategies=list(strategies),
        tokenizer=TOKENS,
    )


def test_create_unchanged_update_keeps_history(empty_db):
    async def scenario():
        conn = await connect(empty_db)
        try:
            created = await ingest(conn, runbook("Pool max_size is 200."))
            again = await ingest(conn, runbook("Pool max_size is 200."))
            updated = await ingest(conn, runbook("Pool max_size is 150."))
            versions = await conn.fetch("SELECT version, content FROM document_versions")
            chunk_texts = [r["text"] for r in await conn.fetch("SELECT text FROM chunks")]
            return created, again, updated, versions, chunk_texts
        finally:
            await conn.close()

    created, again, updated, versions, chunk_texts = asyncio.run(scenario())

    assert (created.status, created.version) == ("created", 1)
    assert (again.status, again.document_id, again.chunk_count) == (
        "unchanged",
        created.document_id,
        0,
    )
    assert (updated.status, updated.version, updated.document_id) == (
        "updated",
        2,
        created.document_id,
    )
    assert [(v["version"], v["content"].endswith("200.")) for v in versions] == [(1, True)]
    assert chunk_texts == ["Pool max_size is 150."]  # old chunks are gone


def test_same_content_under_another_source_is_a_duplicate(empty_db):
    async def scenario():
        conn = await connect(empty_db)
        try:
            await ingest(conn, runbook(TEXT), source_id="upload:postmortem.pdf")
            copy = await ingest(conn, runbook(TEXT), source_id="upload:postmortem (copy).pdf")
            return copy, await conn.fetchval("SELECT count(*) FROM documents")
        finally:
            await conn.close()

    copy, document_count = asyncio.run(scenario())

    assert (copy.status, copy.duplicate_of) == ("duplicate", "upload:postmortem.pdf")
    assert document_count == 1


def test_concurrent_copies_store_exactly_one(empty_db):
    """Two sources with the same content at the same moment: the UNIQUE index decides."""

    async def scenario():
        conns = [await connect(empty_db), await connect(empty_db)]
        try:
            return await asyncio.gather(
                ingest(conns[0], runbook(TEXT), source_id="upload:a.pdf"),
                ingest(conns[1], runbook(TEXT), source_id="upload:b.pdf"),
            )
        finally:
            for conn in conns:
                await conn.close()

    results = asyncio.run(scenario())

    assert sorted(r.status for r in results) == ["created", "duplicate"]


def test_near_duplicate_is_stored_and_linked(empty_db):
    async def scenario():
        conn = await connect(empty_db)
        try:
            await ingest(conn, runbook(TEXT), source_id="upload:final.pdf")
            edited = TEXT.replace("144 connections", "150 connections")
            draft = await ingest(conn, runbook(edited), source_id="upload:draft.docx")
            stored = await conn.fetchval(
                "SELECT metadata FROM documents WHERE source_id = 'upload:draft.docx'"
            )
            return draft, stored
        finally:
            await conn.close()

    draft, stored = asyncio.run(scenario())

    assert draft.status == "created"
    assert [n["source_id"] for n in draft.near_duplicates] == ["upload:final.pdf"]
    assert stored["near_duplicates"] == draft.near_duplicates
    assert stored["author"] == "Priya Nair"


def test_unchanged_document_gains_a_missing_strategy(empty_db):
    async def scenario():
        conn = await connect(empty_db)
        try:
            await ingest(conn, runbook(TEXT), strategies=["structural"])
            result = await ingest(conn, runbook(TEXT), strategies=["fixed", "structural"])
            rows = await conn.fetch("SELECT DISTINCT strategy FROM chunks ORDER BY strategy")
            return result, [r["strategy"] for r in rows]
        finally:
            await conn.close()

    result, strategies = asyncio.run(scenario())

    assert result.status == "unchanged" and result.chunk_count > 0
    assert strategies == ["fixed", "structural"]


def test_delete_removes_chunks_and_history(empty_db):
    async def scenario():
        conn = await connect(empty_db)
        try:
            await ingest(conn, runbook("Version one."))
            updated = await ingest(conn, runbook("Version two."))
            first = await delete_document(conn, updated.document_id)
            second = await delete_document(conn, updated.document_id)
            counts = await conn.fetchrow(
                "SELECT (SELECT count(*) FROM chunks) AS chunks,"
                " (SELECT count(*) FROM document_versions) AS versions"
            )
            return first, second, dict(counts)
        finally:
            await conn.close()

    first, second, counts = asyncio.run(scenario())

    assert (first, second) == (True, False)
    assert counts == {"chunks": 0, "versions": 0}


def test_keyword_search_also_matches_the_context_header(empty_db):
    async def scenario():
        conn = await connect(empty_db)
        try:
            await ingest(conn, runbook("Pool max_size is 200.", title="Gateway runbook"))
            return await conn.fetchval(
                "SELECT count(*) FROM chunks WHERE tsv @@ plainto_tsquery('english', 'runbook')"
            )
        finally:
            await conn.close()

    # 'runbook' appears only in the title, which lives in the chunk's context, not its text.
    assert asyncio.run(scenario()) == 1
