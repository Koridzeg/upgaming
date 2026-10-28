"""Semantic search: nearest chunk vectors by cosine distance, through the HNSW index."""

import asyncpg
import numpy as np

from investigator.retrieval import strategy_literal


async def dense_search(
    conn: asyncpg.Connection,
    vector: np.ndarray,
    strategy: str,
    limit: int,
    source_types: list[str] | None = None,
) -> list[tuple[int, float]]:
    """[(chunk_id, cosine similarity)], best first."""
    join, condition, args = "", "", [vector, limit]
    if source_types:
        join = "JOIN documents d ON d.id = c.document_id"
        condition, args = "AND d.source_type = ANY($3)", [*args, source_types]

    async with conn.transaction():  # SET LOCAL-style settings last until the transaction ends
        # ef_search = size of the candidate list HNSW keeps while searching. It must be at
        # least `limit`; larger means better recall, more work. (Default 40.)
        await conn.execute("SELECT set_config('hnsw.ef_search', $1, true)", str(max(40, 2 * limit)))
        # With a filter (e.g. only uploaded documents), keep scanning the graph until enough
        # rows pass it, instead of returning whatever survived the first ef_search candidates.
        # relaxed_order may return rows slightly out of order, so the outer query re-sorts.
        await conn.execute("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)")
        rows = await conn.fetch(
            f"""
            SELECT id, distance FROM (
                SELECT c.id, c.embedding <=> $1 AS distance
                  FROM chunks c {join}
                 WHERE c.strategy = {strategy_literal(strategy)} {condition}
                 ORDER BY c.embedding <=> $1
                 LIMIT $2
            ) nearest
            ORDER BY distance
            """,
            *args,
        )
    return [(row["id"], 1.0 - row["distance"]) for row in rows]
