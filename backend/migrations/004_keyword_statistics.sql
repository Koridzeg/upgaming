-- 004: corpus statistics for BM25 keyword ranking.

-- Postgres finds chunks containing words quickly (GIN index on tsv), but its own ranking
-- functions use no corpus-wide statistics: a match on "american" weighs as much as a match on
-- "derrickson". BM25 needs, for every term, the number of chunks containing it (document
-- frequency). Counting that per query would scan the whole table, so it is precomputed here
-- and refreshed after loads (investigator.retrieval.keyword.refresh_term_stats).
CREATE TABLE term_stats (
    strategy  text NOT NULL,
    term      text NOT NULL,     -- a lexeme exactly as it appears in chunks.tsv (stemmed)
    df        integer NOT NULL,  -- number of chunks of this strategy containing the term
    PRIMARY KEY (strategy, term)
);

CREATE TABLE corpus_stats (
    strategy      text PRIMARY KEY,
    chunk_count   integer NOT NULL,
    avg_length    real NOT NULL,  -- average number of distinct lexemes per chunk
    refreshed_at  timestamptz NOT NULL DEFAULT now()
);
