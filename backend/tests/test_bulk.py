import asyncio

from investigator.ingestion.bulk import bulk_load
from investigator.ingestion.models import Block, LoadedDocument
from investigator.ingestion.tokenizer import WhitespaceTokenizer
from tests.fakes import FakeEmbedder
from tests.test_dedup import TEXT
from tests.test_store import connect


def paragraph(title: str, text: str) -> tuple[str, LoadedDocument]:
    return f"hotpotqa:{title}", LoadedDocument(title, "txt", [Block("paragraph", text)])


def test_bulk_load_dedups_links_embeds_and_restores_indexes(empty_db):
    documents = [
        paragraph("Tara Strong", "Tara Strong is a voice actress."),
        paragraph("Tara Strong filmography", "Tara Strong is a voice actress."),  # exact copy
        paragraph("Incident", TEXT),
        paragraph("Incident draft", TEXT.replace("144 connections", "150 connections")),  # near
        paragraph("Kafka", "Kafka brokers were upgraded from 3.6 to 3.7."),
    ]

    async def scenario():
        conn = await connect(empty_db)
        try:
            kwargs = dict(
                source_type="hotpotqa",
                strategies=["fixed", "structural"],
                tokenizer=WhitespaceTokenizer(),
                embedder=FakeEmbedder(),
                progress=lambda message: None,
            )
            first = await bulk_load(conn, documents, **kwargs)
            second = await bulk_load(conn, documents, **kwargs)  # re-run: nothing new
            stats = await conn.fetchrow(
                """
                SELECT (SELECT count(*) FROM documents) AS documents,
                       (SELECT count(*) FROM chunks WHERE embedding IS NULL) AS unembedded,
                       (SELECT metadata FROM documents WHERE title = 'Incident draft') AS draft,
                       (SELECT count(*) FROM pg_indexes
                         WHERE tablename = 'chunks' AND indexdef LIKE '%hnsw%') AS hnsw_indexes
                """
            )
            return first, second, stats
        finally:
            await conn.close()

    first, second, stats = asyncio.run(scenario())

    assert first["loaded"] == 4
    assert first["duplicates"] == {"hotpotqa:Tara Strong filmography": "hotpotqa:Tara Strong"}
    assert second["loaded"] == 0
    assert stats["documents"] == 4
    assert stats["unembedded"] == 0
    assert stats["draft"]["near_duplicates"][0]["source_id"] == "hotpotqa:Incident"
    assert stats["hnsw_indexes"] == 2  # dropped for the load, rebuilt afterwards
