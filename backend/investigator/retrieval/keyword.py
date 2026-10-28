"""Keyword search with BM25, computed in SQL over the Postgres full-text vectors.

Postgres finds the chunks containing a word quickly (GIN index on `tsv`), but its ranking
(ts_rank) has no inverse document frequency. BM25 adds it:

    score  = sum over query terms t of  idf(t) * tf * (k1 + 1) / (tf + k1 * norm)
    norm   = 1 - b + b * len / avg_len
    idf(t) = ln(1 + (N - df + 0.5) / (df + 0.5))

- tf: occurrences of t in the chunk. An occurrence in the context header (title, headings;
  weight A in tsv) counts CONTEXT_WEIGHT times: a light version of BM25F.
- len / avg_len: chunk length relative to the average, so long chunks don't win by size alone.
- k1 caps how much repeating a term helps; b sets how strongly length is normalised.
- df: chunks containing t, precomputed in `term_stats` (see migration 004).

Query terms are produced by the same `english` configuration as the indexed text, so "failing"
in a question meets "fail" in a chunk. Very common terms (in more than MAX_DF_RATIO of chunks)
still add to scores but don't select candidates: they would pull in thousands of rows while
carrying almost no information.
"""

import math

import asyncpg

from investigator.retrieval import strategy_literal

K1 = 1.2
B = 0.75
CONTEXT_WEIGHT = 2.0
# Measured on 50 HotpotQA questions (both gold paragraphs in the top 10 / p50 / p95 latency):
#   0.05 -> 35/50, 192 ms, 508 ms     0.01  -> 35/50, 35 ms, 101 ms
#   0.02 -> 35/50,  70 ms, 206 ms     0.005 -> 33/50, 15 ms,  36 ms
MAX_DF_RATIO = 0.01


async def refresh_term_stats(conn: asyncpg.Connection, strategy: str) -> int:
    """Recount document frequencies for one strategy. Returns the number of distinct terms."""
    literal = strategy_literal(strategy)
    async with conn.transaction():
        await conn.execute("DELETE FROM term_stats WHERE strategy = $1", strategy)
        # ts_stat runs the given query and counts, per lexeme, the rows it occurs in (ndoc).
        await conn.execute(
            f"""
            INSERT INTO term_stats (strategy, term, df)
            SELECT $1, word, ndoc
              FROM ts_stat($q$SELECT tsv FROM chunks WHERE strategy = {literal}$q$)
            """,
            strategy,
        )
        await conn.execute(
            """
            INSERT INTO corpus_stats (strategy, chunk_count, avg_length)
            SELECT $1, count(*), coalesce(avg(length(tsv)), 0) FROM chunks WHERE strategy = $1
            ON CONFLICT (strategy) DO UPDATE
               SET chunk_count = EXCLUDED.chunk_count,
                   avg_length = EXCLUDED.avg_length,
                   refreshed_at = now()
            """,
            strategy,
        )
    return await conn.fetchval("SELECT count(*) FROM term_stats WHERE strategy = $1", strategy)


async def keyword_search(
    conn: asyncpg.Connection,
    query: str,
    strategy: str,
    limit: int,
    source_types: list[str] | None = None,
) -> list[tuple[int, float]]:
    """[(chunk_id, BM25 score)], best first. Empty when statistics haven't been computed."""
    stats = await conn.fetchrow(
        "SELECT chunk_count, avg_length FROM corpus_stats WHERE strategy = $1", strategy
    )
    if stats is None or stats["chunk_count"] == 0:
        return []
    total, avg_length = stats["chunk_count"], max(stats["avg_length"], 1.0)

    # A term missing from term_stats is treated as the rarest possible (df = 1), not ignored:
    # the tsv index is always current, but the statistics are refreshed only after loads, so a
    # rare word from a just-uploaded document must still be searchable. If no chunk contains
    # the term at all, the candidate query simply matches nothing.
    terms = await conn.fetch(
        """
        SELECT t.lexeme AS term, coalesce(s.df, 1) AS df
          FROM unnest(to_tsvector('english', $1)) AS t
          LEFT JOIN term_stats s ON s.strategy = $2 AND s.term = t.lexeme
        """,
        query,
        strategy,
    )
    if not terms:
        return []
    names = [t["term"] for t in terms]
    idfs = [math.log(1 + (total - t["df"] + 0.5) / (t["df"] + 0.5)) for t in terms]
    selective = [t["term"] for t in terms if t["df"] <= MAX_DF_RATIO * total]
    if not selective:  # every term is common: fall back to the rarest one
        selective = [min(terms, key=lambda t: t["df"])["term"]]

    join, condition, args = "", "", [limit, names, idfs, _or_query(selective), avg_length]
    if source_types:
        join = "JOIN documents d ON d.id = c.document_id"
        condition, args = "AND d.source_type = ANY($6)", [*args, source_types]

    rows = await conn.fetch(
        f"""
        SELECT c.id,
               sum(q.idf * m.tf * {K1 + 1} / (m.tf + {K1} * (1 - {B} + {B} * length(c.tsv) / $5)))
                   AS score
          FROM chunks c {join}
         CROSS JOIN LATERAL (
               -- one row per query term found in the chunk, with its weighted occurrence
               -- count. Filtering to query terms first matters: a chunk has ~60 lexemes and
               -- computing tf for all of them made this query 5-10x slower.
               SELECT u.lexeme,
                      (SELECT sum(CASE w WHEN 'A' THEN {CONTEXT_WEIGHT} ELSE 1 END)
                         FROM unnest(u.weights) AS w) AS tf
                 FROM unnest(c.tsv) AS u
                WHERE u.lexeme = ANY($2::text[])
         ) m
          JOIN unnest($2::text[], $3::float8[]) AS q(term, idf) ON q.term = m.lexeme
         WHERE c.strategy = {strategy_literal(strategy)}
           AND c.tsv @@ to_tsquery('simple', $4)  -- candidates: any selective term (GIN index)
           {condition}
         GROUP BY c.id
         ORDER BY score DESC
         LIMIT $1
        """,
        *args,
    )
    return [(row["id"], float(row["score"])) for row in rows]


def _or_query(terms: list[str]) -> str:
    """'a' | 'b' as tsquery text. 'simple' config: the terms are already stemmed lexemes."""
    quoted = ("'" + t.replace("\\", "\\\\").replace("'", "''") + "'" for t in terms)
    return " | ".join(quoted)
