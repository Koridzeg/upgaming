r"""The search pipeline: dense + keyword candidates -> weighted RRF -> re-ranking.

    query --> dense search (top N by cosine) ---\
         \--> keyword search (top N by BM25) ----> weighted RRF --> top N
    top N --> re-rank (+ title match, then MMR) --> top k

Every hit carries its score breakdown (ranks in each list, fused score, title match, final
score), so a result can always be explained - in the UI and in the evaluation.
"""

import asyncio
from dataclasses import dataclass, field, replace
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
    # Both chosen by the Step 7 evaluation (tune split, all@10): a title boost of 0.6 and no
    # MMR diversity. MMR (lambda < 1) never improved all@10 on HotpotQA; it stays available.
    title_boost: float = 0.6  # added to the normalised fused score when the title is named
    mmr_lambda: float = 1.0  # 1.0 = plain ranking, lower = more diversity
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
    token_count: int
    chunk_metadata: dict
    document_metadata: dict
    scores: dict = field(default_factory=dict)
    embedding: np.ndarray | None = field(default=None, repr=False)


async def search(
    conn: asyncpg.Connection, embedder: Embedder, query: str, config: SearchConfig | None = None
) -> list[SearchHit]:
    config = config or SearchConfig()
    candidates = await retrieve(conn, embedder, query, config)
    hits = await fetch_hits(conn, candidates.chunk_ids())
    return rank(candidates, hits, query, config)


@dataclass
class Candidates:
    """What the retrievers returned, before any fusion: chunk ids best-first, and raw scores."""

    rankings: dict[str, list[int]] = field(default_factory=dict)
    scores: dict[str, dict[int, float]] = field(default_factory=dict)

    def chunk_ids(self) -> list[int]:
        return list(dict.fromkeys(i for ranked in self.rankings.values() for i in ranked))


_RETRIEVERS: dict[str, set[str]] = {
    "hybrid": {"dense", "keyword"},
    "dense": {"dense"},
    "keyword": {"keyword"},
}


async def retrieve(
    conn: asyncpg.Connection, embedder: Embedder, query: str, config: SearchConfig
) -> Candidates:
    """The database half of a search: run the retrievers the mode needs."""
    candidates = Candidates()
    if "dense" in _RETRIEVERS[config.mode]:
        # Embedding is CPU work: run it in a thread so the event loop keeps serving requests.
        vector = await asyncio.to_thread(embedder.embed_query, query)
        dense = await dense_search(
            conn, vector, config.strategy, config.candidates, config.source_types
        )
        candidates.rankings["dense"] = [i for i, _ in dense]
        candidates.scores["dense"] = dict(dense)
    if "keyword" in _RETRIEVERS[config.mode]:
        keyword = await keyword_search(
            conn, query, config.strategy, config.candidates, config.source_types
        )
        candidates.rankings["keyword"] = [i for i, _ in keyword]
        candidates.scores["keyword"] = dict(keyword)
    return candidates


def rank(
    candidates: Candidates, hits: dict[int, SearchHit], query: str, config: SearchConfig
) -> list[SearchHit]:
    """The pure half of a search: fusion and re-ranking. No database, no model.

    Separate so the evaluation can retrieve once per question and then try many settings.
    Returns new SearchHit objects; `hits` is left untouched.
    """
    rankings = {
        name: ranked
        for name, ranked in candidates.rankings.items()
        if name in _RETRIEVERS[config.mode]
    }
    fused = weighted_rrf(
        rankings,
        {"dense": config.dense_weight, "keyword": config.keyword_weight},
        k=config.rrf_k,
    )
    top = sorted(fused, key=fused.__getitem__, reverse=True)[: config.candidates]

    ranked_hits = []
    for chunk_id in top:
        scores = {"fused": round(fused[chunk_id], 5)}
        for name, ranked in rankings.items():
            if chunk_id in candidates.scores[name]:
                scores[f"{name}_rank"] = ranked.index(chunk_id) + 1
                scores[f"{name}_score"] = round(candidates.scores[name][chunk_id], 4)
        ranked_hits.append(replace(hits[chunk_id], scores=scores))

    if config.rerank:
        return _rerank(ranked_hits, query, config)
    return _truncate(ranked_hits, config.k)


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


async def fetch_neighbors(conn: asyncpg.Connection, chunk_id: int) -> list[SearchHit]:
    """The chunks just before and after `chunk_id` in its document (same strategy)."""
    rows = await conn.fetch(
        """
        SELECT n.id
          FROM chunks c
          JOIN chunks n ON n.document_id = c.document_id
                       AND n.strategy = c.strategy
                       AND n.chunk_index IN (c.chunk_index - 1, c.chunk_index + 1)
         WHERE c.id = $1
         ORDER BY n.chunk_index
        """,
        chunk_id,
    )
    hits = await fetch_hits(conn, [r["id"] for r in rows])
    return [hits[r["id"]] for r in rows]


async def fetch_hits(conn: asyncpg.Connection, chunk_ids: list[int]) -> dict[int, SearchHit]:
    """Load chunks with their documents' title and metadata, keyed by chunk id."""
    rows = await conn.fetch(
        """
        SELECT c.id, c.document_id, d.source_id, d.source_type, d.title, c.context, c.text,
               c.token_count, c.metadata AS chunk_metadata, c.embedding,
               d.metadata - 'sentence_spans' - 'near_duplicates' AS document_metadata
          FROM chunks c
          JOIN documents d ON d.id = c.document_id
         WHERE c.id = ANY($1::bigint[])
        """,
        chunk_ids,
    )
    return {
        row["id"]: SearchHit(
            chunk_id=row["id"],
            document_id=row["document_id"],
            source_id=row["source_id"],
            source_type=row["source_type"],
            title=row["title"],
            context=row["context"],
            text=row["text"],
            token_count=row["token_count"],
            chunk_metadata=row["chunk_metadata"],
            document_metadata=row["document_metadata"],
            embedding=np.asarray(row["embedding"].to_numpy(), dtype=np.float32),
        )
        for row in rows
    }
