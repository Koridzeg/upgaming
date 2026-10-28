-- 001: documents, their chunks, chunk embeddings and the keyword-search index.

CREATE EXTENSION IF NOT EXISTS vector;

-- One row per source document (a HotpotQA paragraph, an uploaded PDF, ...).
CREATE TABLE documents (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Stable external key, e.g. 'hotpotqa:Arthur''s Magazine' or 'upload:incident-0312.pdf'.
    -- Re-ingesting the same source_id updates the row instead of creating a duplicate.
    source_id     text NOT NULL UNIQUE,
    title         text NOT NULL,
    source_type   text NOT NULL,                -- 'hotpotqa' | 'synthetic' | 'upload'
    file_format   text NOT NULL,                -- 'txt' | 'md' | 'pdf' | 'docx' | 'html' | 'json'
    -- Full extracted text, kept so documents can be re-chunked without re-parsing the file.
    content       text NOT NULL,
    -- SHA-256 of the normalised text: finds exact duplicates and skips re-embedding unchanged files.
    content_hash  text NOT NULL,
    metadata      jsonb NOT NULL DEFAULT '{}',  -- author, dates, page count, ... (format-specific)
    version       integer NOT NULL DEFAULT 1,   -- incremented each time the content changes
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX documents_content_hash_idx ON documents (content_hash);
CREATE INDEX documents_source_type_idx ON documents (source_type);

-- The retrievable units. A document has one set of chunks per chunking strategy, so two
-- strategies can be stored side by side and compared on identical data.
CREATE TABLE chunks (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- ON DELETE CASCADE: deleting a document removes its chunks in the same transaction,
    -- so search can never return a chunk whose document is gone.
    document_id   uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    strategy      text NOT NULL,                -- chunking strategy that produced this chunk
    chunk_index   integer NOT NULL,             -- position within the document, per strategy
    text          text NOT NULL,
    token_count   integer NOT NULL,
    metadata      jsonb NOT NULL DEFAULT '{}',  -- page number, section heading, char offsets
    -- 384 dimensions = bge-small-en-v1.5. halfvec stores 16-bit floats: half the size of
    -- `vector`, with negligible recall loss. That matters on a 0.5 GB free database.
    -- NULL until the chunk has been embedded.
    embedding     halfvec(384),
    -- Maintained by Postgres on every insert/update; feeds the keyword half of hybrid search.
    tsv           tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    UNIQUE (document_id, strategy, chunk_index)
);

-- Approximate nearest-neighbour index for semantic search (cosine distance).
CREATE INDEX chunks_embedding_hnsw_idx ON chunks USING hnsw (embedding halfvec_cosine_ops);
-- Inverted index for full-text keyword search.
CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv);
