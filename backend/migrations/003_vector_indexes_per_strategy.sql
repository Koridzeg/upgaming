-- 003: one vector index per chunking strategy, and a record of the model behind the vectors.

-- Every search filters on one strategy. With a single HNSW index over all chunks, that filter
-- is applied after the approximate search: the index returns its ~40 nearest candidates, the
-- filter throws away the ones of the other strategy, and fewer than k results may remain.
-- A partial index per strategy only ever contains the vectors a query is allowed to return.
-- (The planner uses a partial index when the query's WHERE clause repeats its predicate,
-- with the strategy written as a literal.)
DROP INDEX chunks_embedding_hnsw_idx;
CREATE INDEX chunks_embedding_fixed_idx ON chunks
    USING hnsw (embedding halfvec_cosine_ops) WHERE strategy = 'fixed';
CREATE INDEX chunks_embedding_structural_idx ON chunks
    USING hnsw (embedding halfvec_cosine_ops) WHERE strategy = 'structural';

-- Vectors from different models (or even differently quantized copies of one model) are not
-- comparable. The model that produced the stored vectors is recorded once; writes with any
-- other model are refused, and the app can check its query model against it.
CREATE TABLE embedding_model (
    singleton    boolean PRIMARY KEY DEFAULT true CHECK (singleton),  -- at most one row
    name         text NOT NULL,
    sha256       text NOT NULL,
    dimensions   integer NOT NULL,
    recorded_at  timestamptz NOT NULL DEFAULT now()
);
