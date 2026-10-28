"""Search against a small corpus in the test database."""

import asyncio

from investigator.embedding import embedding_input
from investigator.ingestion.models import Block, LoadedDocument
from investigator.ingestion.store import ingest_document
from investigator.ingestion.tokenizer import WhitespaceTokenizer
from investigator.retrieval.dense import dense_search
from investigator.retrieval.keyword import keyword_search, refresh_term_stats
from investigator.retrieval.search import SearchConfig, search
from tests.fakes import FakeEmbedder
from tests.test_store import connect

DOCUMENTS = {
    "Scott Derrickson": "Scott Derrickson is an American director and screenwriter.",
    "Ed Wood": "Edward Davis Wood Jr. was an American filmmaker and actor.",
    "Kafka": "The American team upgraded Kafka brokers to version 3.7.",
    "Directors": "A list of American film directors, including one named Derrickson.",
}


async def corpus(url: str):
    conn = await connect(url)
    embedder = FakeEmbedder()
    for title, text in DOCUMENTS.items():
        source_type = "upload" if title == "Kafka" else "hotpotqa"
        await ingest_document(
            conn,
            LoadedDocument(title, "txt", [Block("paragraph", text)]),
            source_id=f"{source_type}:{title}",
            source_type=source_type,
            strategies=["structural"],
            tokenizer=WhitespaceTokenizer(),
            embedder=embedder,
        )
    await refresh_term_stats(conn, "structural")
    return conn, embedder


async def titles_for(conn, ids):
    rows = await conn.fetch(
        "SELECT c.id, d.title FROM chunks c JOIN documents d ON d.id = c.document_id"
        " WHERE c.id = ANY($1)",
        ids,
    )
    by_id = {r["id"]: r["title"] for r in rows}
    return [by_id[i] for i in ids]


def test_bm25_prefers_rare_terms_and_title_matches(empty_db):
    async def scenario():
        conn, _ = await corpus(empty_db)
        try:
            hits = await keyword_search(conn, "Is Derrickson American?", "structural", 10)
            return await titles_for(conn, [chunk_id for chunk_id, _ in hits])
        finally:
            await conn.close()

    ranked = asyncio.run(scenario())

    # "american" is in every document, so only "derrickson" decides the order. Both mentions
    # count once in the text, but "Scott Derrickson" also has it in its title (the context
    # header), which weighs double.
    assert ranked[:2] == ["Scott Derrickson", "Directors"]


def test_new_terms_are_searchable_before_statistics_are_refreshed(empty_db):
    async def scenario():
        conn, embedder = await corpus(empty_db)
        try:
            await ingest_document(
                conn,
                LoadedDocument("Advisory", "txt", [Block("paragraph", "Orchard SDK deprecation.")]),
                source_id="upload:advisory",
                source_type="upload",
                strategies=["structural"],
                tokenizer=WhitespaceTokenizer(),
                embedder=embedder,
            )  # no refresh_term_stats: "orchard" is unknown to term_stats
            hits = await keyword_search(conn, "orchard deprecation", "structural", 5)
            return await titles_for(conn, [chunk_id for chunk_id, _ in hits])
        finally:
            await conn.close()

    assert asyncio.run(scenario()) == ["Advisory"]


def test_dense_search_finds_the_identical_text_first(empty_db):
    async def scenario():
        conn, embedder = await corpus(empty_db)
        try:
            # FakeEmbedder maps equal text to equal vectors, so querying with a chunk's exact
            # embedding input must return that chunk first with similarity ~1.
            target = embedding_input("Ed Wood", DOCUMENTS["Ed Wood"])
            hits = await dense_search(conn, embedder.embed_query(target), "structural", 2)
            return await titles_for(conn, [hits[0][0]]), hits[0][1]
        finally:
            await conn.close()

    titles, similarity = asyncio.run(scenario())

    assert titles == ["Ed Wood"]
    assert similarity > 0.999  # halfvec stores 16-bit floats, hence not exactly 1


def test_hybrid_search_explains_its_ranking_and_honours_filters(empty_db):
    async def scenario():
        conn, embedder = await corpus(empty_db)
        try:
            everything = await search(conn, embedder, "Derrickson director", SearchConfig(k=3))
            uploads = await search(
                conn, embedder, "American", SearchConfig(k=3, source_types=["upload"])
            )
            return everything, uploads
        finally:
            await conn.close()

    everything, uploads = asyncio.run(scenario())

    # FakeEmbedder vectors carry no meaning, so the dense half of the ranking is arbitrary
    # here: assert the explanation and the filter, not the exact order.
    assert "Scott Derrickson" in [hit.title for hit in everything]
    for hit in everything:
        assert {"dense_rank", "fused", "relevance", "final_rank"} <= set(hit.scores)
    assert [hit.source_type for hit in uploads] == ["upload"]
