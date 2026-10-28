# AI Investigation Engine

A multi-document investigation system: it searches a large document collection, connects
facts across sources, and answers with citations, flagging contradictions and refusing to
answer when the evidence is insufficient.

> Work in progress. This README grows with each build step; architecture, design decisions,
> evaluation results and deployment notes will be added as those parts are built.

## Local development

Prerequisites: Python 3.12+, Docker.

```bash
# 1. Start Postgres + pgvector (port 5433 on the host)
docker compose up -d

# 2. Configure
cp .env.example .env            # then fill in the values

# 3. Install the backend (from backend/)
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows; on macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"

# 4. Create the schema and run the API
python -m investigator.migrate
uvicorn investigator.main:app --reload
# -> http://localhost:8000/api/health  and the interactive API docs at /docs

# 5. Tests (need the database from step 1)
pytest
```

## Data preparation

Both datasets are rebuilt from scripts, so results are reproducible.

```bash
pip install -e ".[data]"                       # pyarrow, fpdf2, python-docx
python -m investigator.datasets.hotpotqa      # -> data/processed/hotpotqa/ (66,581 documents)
python -m investigator.datasets.synthetic     # -> data/synthetic/corpus/ (15 files, 6 formats)
```

- **HotpotQA** (distractor setting, validation split), pinned to dataset commit `1908d6af` and
  verified by SHA-256. The context paragraphs of all 7,405 questions are pooled and deduplicated
  (73,700 raw, 66,581 unique), so each question must be answered from the whole pool.
- **Synthetic incident corpus**: a fictional company's documents with planted contradictions,
  gaps, decoys and a prompt-injection page. See [data/synthetic/README.md](data/synthetic/README.md).

## Supported file formats

PDF, DOCX, HTML, Markdown, plain text, JSON / JSON Lines and CSV. Every format is converted to
the same model (title, metadata, ordered blocks). To see what a file turns into:

```bash
python -m investigator.ingestion.loaders path/to/file.pdf
```

## Embeddings and bulk loading

Chunks are embedded with `BAAI/bge-small-en-v1.5` (384 dimensions), quantized to int8 and run
on ONNX Runtime. The quantized model is created once from the pinned upstream model:

```bash
python -m investigator.embedding                              # -> data/models/*.onnx
python -m investigator.ingestion.bulk --strategies fixed,structural   # all 66,581 HotpotQA docs
```

The bulk loader skips documents that are already stored, so it can be re-run after an
interruption. The database records which model produced its vectors and refuses vectors from
any other model.

## Searching

Hybrid search: dense (pgvector HNSW) and BM25 keyword candidates, fused with weighted
Reciprocal Rank Fusion, then re-ranked (title naming + MMR diversity). Each result shows why it
ranked where it did.

```bash
python -m investigator.retrieval "Were Scott Derrickson and Ed Wood of the same nationality?"
python -m investigator.retrieval "pool size during INC-2026-0311" --mode keyword --k 5
python -m investigator.retrieval --refresh-stats   # recompute BM25 term statistics
```

## Ingesting files

```bash
# Load, deduplicate, chunk and store files (both chunking strategies, for comparison)
python -m investigator.ingestion.cli data/synthetic/corpus/* --strategies fixed,structural

# Duplicate and chunking statistics for both corpora
python -m investigator.ingestion.report
```

Re-ingesting a file is safe: unchanged content is skipped, changed content creates a new
version (the old one is kept in `document_versions`), and content already stored under another
name is reported as a duplicate instead of being stored twice.

## Repository layout

```
backend/
  investigator/     Python package (API, ingestion, retrieval, agent, evaluation)
  migrations/       Numbered SQL migrations, applied in order on startup
  tests/
docker/             Local Postgres init scripts
docker-compose.yml  Local development database
```
