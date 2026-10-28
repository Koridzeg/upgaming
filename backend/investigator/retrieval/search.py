r"""The search pipeline: dense + keyword candidates -> weighted RRF -> re-ranking.

    query --> dense search (top N by cosine) ---\
         \--> keyword search (top N by BM25) ----> weighted RRF --> top N
    top N --> re-rank (+ title match, then MMR) --> top k

Every hit carries its score breakdown (ranks in each list, fused score, title match, final
score), so a result can always be explained - in the UI and in the evaluation.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

import asyncpg
import numpy as np

from investigator.embedding import Embedder
from investigator.retrieval.dense import dense_search
from investigator.retrieval.keyword import keyword_search
from investigator.retrieval.ranking import RRF_K, mmr_select, title_match, weighted_rrf

Mode = Literal["hybrid", "dense", "keyword"]


@dataclass
class SearchConfig:
    mode: Mode = "hybrid"
    strategy: str = "structural"
    k: int = 10  # results returned
    candidates: int = 50  # taken from each retriever, and kept after fusion
    dense_weight: float = 1.0
    keyword_weight: float = 1.0
    rrf_k: int = RRF_K
    rerank: bool = True
    title_boost: float = 0.3  # added to the normalised fused score x title match share
    mmr_lambda: float = 0.8  # 1.0 = no diversity
    source_types: list[str] | None = None  # e.g. ["upload", "synthetic"]; None = everything


@dataclass
class SearchHit:
    chunk_id: int
    document_id: UUID
    source_id: str
    source_type: str
    title: str
    context: str
    text: str
    chunk_metadata: dict
    document_metadata: dict
    scores: dict = field(default_factory=dict)
    embedding: np.ndarray | None = field(default=None, repr=False)


async def search(
    conn: asyncpg.Connection, embedder: Embedder, query: str, config: SearchConfig | None = None
) -> list[SearchHit]:
    config = config or SearchConfig()
    rankings: dict[str, list[int]] = {}
    raw: dict[str, dict[int, float]] = {}

    if config.mode in ("hybrid", "dense"):
        # Embedding is CPU work: run it in a thread so the event loop keeps serving requests.
        vector = await asyncio.to_thread(embedder.embed_query, query)
        dense = await dense_search(
            conn, vector, config.strategy, config.candidates, config.source_types
        )
        rankings["dense"], raw["dense"] = [i for i, _ in dense], dict(dense)
    if config.mode in ("hybrid", "keyword"):
        keyword = await keyword_search(
            conn, query, config.strategy, config.candidates, config.source_types
        )
        rankings["keyword"], raw["keyword"] = [i for i, _ in keyword], dict(keyword)

    fused = weighted_rrf(
        rankings,
        {"dense": config.dense_weight, "keyword": config.keyword_weight},
        k=config.rrf_k,
    )
    top = sorted(fused, key=fused.__getitem__, reverse=True)[: config.candidates]
    hits = await _fetch(conn, top)

    for hit in hits:
        for name, ranked in rankings.items():
            if hit.chunk_id in raw[name]:
                hit.scores[f"{name}_rank"] = ranked.index(hit.chunk_id) + 1
                hit.scores[f"{name}_score"] = round(raw[name][hit.chunk_id], 4)
        hit.scores["fused"] = round(fused[hit.chunk_id], 5)

    return _rerank(hits, query, config) if config.rerank else _truncate(hits, config.k)


def _rerank(hits: list[SearchHit], query: str, config: SearchConfig) -> list[SearchHit]:
    if not hits:
        return hits
    fused = np.array([h.scores["fused"] for h in hits])
    matches = np.array([title_match(h.title, query) for h in hits])
    relevance = fused / fused.max() + config.title_boost * matches
    order = mmr_select(
        relevance, np.vstack([h.embedding for h in hits]), config.k, config.mmr_lambda
    )
    for hit, match, rel in zip(hits, matches, relevance, strict=True):
        hit.scores["title_match"] = round(float(match), 3)
        hit.scores["relevance"] = round(float(rel), 4)
    selected = [hits[i] for i in order]
    for rank, hit in enumerate(selected, start=1):
        hit.scores["final_rank"] = rank
    return selected


def _truncate(hits: list[SearchHit], k: int) -> list[SearchHit]:
    for rank, hit in enumerate(hits[:k], start=1):
        hit.scores["final_rank"] = rank
    return hits[:k]


async def _fetch(conn: asyncpg.Connection, chunk_ids: list[int]) -> list[SearchHit]:
    """Load the chunks and their documents, in the order of `chunk_ids`."""
    rows = await conn.fetch(
        """
        SELECT c.id, c.document_id, d.source_id, d.source_type, d.title, c.context, c.text,
               c.metadata AS chunk_metadata, c.embedding,
               d.metadata - 'sentence_spans' - 'near_duplicates' AS document_metadata
          FROM unnest($1::bigint[]) WITH ORDINALITY AS wanted(id, position)
          JOIN chunks c ON c.id = wanted.id
          JOIN documents d ON d.id = c.document_id
         ORDER BY wanted.position
        """,
        chunk_ids,
    )
    return [
        SearchHit(
            chunk_id=row["id"],
            document_id=row["document_id"],
            source_id=row["source_id"],
            source_type=row["source_type"],
            title=row["title"],
            context=row["context"],
            text=row["text"],
            chunk_metadata=row["chunk_metadata"],
            document_metadata=row["document_metadata"],
            embedding=np.asarray(row["embedding"].to_numpy(), dtype=np.float32),
        )
        for row in rows
    ]
