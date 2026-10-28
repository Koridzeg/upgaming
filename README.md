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

## Repository layout

```
backend/
  investigator/     Python package (API, ingestion, retrieval, agent, evaluation)
  migrations/       Numbered SQL migrations, applied in order on startup
  tests/
docker/             Local Postgres init scripts
docker-compose.yml  Local development database
```
