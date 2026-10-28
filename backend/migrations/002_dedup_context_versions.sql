-- 002: duplicate detection, chunk context headers, and document version history.

-- Exact duplicates become impossible at the database level, not only checked in code:
-- two concurrent uploads of the same content cannot both get in.
DROP INDEX documents_content_hash_idx;
CREATE UNIQUE INDEX documents_content_hash_key ON documents (content_hash);

-- 64-bit SimHash of the normalised text. Near-duplicates have fingerprints that differ in only
-- a few bits (small Hamming distance). Stored signed, because Postgres has no unsigned bigint.
ALTER TABLE documents ADD COLUMN simhash bigint;

-- The context header of a chunk: document title + section headings ("Postmortem > Root cause").
-- It is embedded and keyword-indexed together with the chunk text, but it is not part of the
-- cited passage, which stays an exact substring of the document.
ALTER TABLE chunks ADD COLUMN context text NOT NULL DEFAULT '';

-- Rebuild the keyword index over context + text. Context words weigh more (A > B in ts_rank):
-- a query naming a document or section should rank that document's chunks first.
-- Dropping the column also drops its GIN index.
ALTER TABLE chunks DROP COLUMN tsv;
ALTER TABLE chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('english', context), 'A') || setweight(to_tsvector('english', text), 'B')
) STORED;
CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv);

-- Previous versions of updated documents. The current version always lives in `documents`.
CREATE TABLE document_versions (
    document_id   uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    version       integer NOT NULL,
    title         text NOT NULL,
    content       text NOT NULL,
    content_hash  text NOT NULL,
    metadata      jsonb NOT NULL,
    replaced_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, version)
);
