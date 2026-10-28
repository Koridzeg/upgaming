# AI Investigation Engine

A multi-document investigation system: it searches a large document collection, connects
facts across sources, and answers with citations, flagging contradictions and refusing to
answer when the evidence is insufficient.

> Work in progress. This README grows with each build step; architecture, design decisions,
> evaluation results and deployment notes will be added as those parts are built.

## Local development

Prerequisites: Python 3.12+, Node 20+, Docker.

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

# 6. The web UI (from frontend/)
npm install
npx ng serve       # -> http://localhost:4200, /api is proxied to the backend on :8000
npx ng build       # -> frontend/dist/; the backend then serves the UI itself at http://localhost:8000
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

## Retrieval evaluation

```bash
python -m investigator.evaluation.retrieval   # ~1 minute; writes eval/retrieval_report.md
```

300 HotpotQA questions (seeded, stratified, 150 to tune settings on and 150 to report on;
the ids are fixed in `eval/retrieval_sample.json`) plus the answerable synthetic questions.
Metrics: recall@K, all@K (every gold document found), MRR, nDCG@10, sentence recall@10,
with paired-bootstrap confidence intervals. See [eval/retrieval_report.md](eval/retrieval_report.md).

## Investigating

The agent searches in as many steps as it needs (each search can use what the previous one
revealed), then answers with `[E#]` citations, reports contradictions between sources, and says
so when the evidence is insufficient. A second LLM pass checks every cited claim.

```bash
python -m investigator.agent "Which system changes caused the payment processing delays in March 2026?"
```

## LLM providers

Groq and Gemini, through their OpenAI-compatible APIs, behind one client with retries
(exponential backoff, or the provider's own `Retry-After`), a fallback chain of models
(`LLM_MODELS`), a circuit breaker, tool calling, streaming and token/latency counters.

Free-tier limits measured on 2026-10-28, which shape the defaults: every Groq model has its own
1,000 requests/day and 8,000 tokens/minute; Gemini allows 5 requests/minute and 20/day. So the
chain is `gpt-oss-120b` -> `gpt-oss-20b` (Groq, separate budgets) -> `gemini-3.5-flash` (last
resort, independent provider).

```bash
python -m investigator.llm --models   # model ids your keys can use
python -m investigator.llm            # live tool-call round trip + streaming per provider
```

## Answer evaluation

```bash
python -m investigator.evaluation.answers        # ~1 hour on the free tier; resumable
python -m investigator.evaluation.answers --report-only
```

42 questions (`eval/answer_questions.json`): the 20 synthetic questions (multi-document,
contradictions, unanswerable, decoys, prompt injection) and 22 HotpotQA multi-hop questions
from the retrieval test split. Three configurations, each on a single model: the agent on
gpt-oss-120b, a single-shot baseline (one search, one answer) on the same model, and the agent
on gpt-oss-20b. A third model (qwen3.8-27b) judges correctness against reference answers.
Results: [eval/answer_report.md](eval/answer_report.md).

## Web UI and API

One FastAPI process serves the API under `/api` and the built Angular app at `/` (any other
path falls back to the app, so deep links and browser refreshes work).

| Page | What it does |
| --- | --- |
| Investigate | Ask a question; each search the agent makes appears live, then the answer with clickable `[E#]` citations (the cited passage, its section, page, document status), contradictions, missing information, claim verification and token/latency usage |
| Documents | List and filter, upload (add), replace (update to a new version), delete, view content, metadata and version history |
| Search explorer | Retrieval alone, without the LLM: compare hybrid / dense / BM25 and see every score behind each rank |

| Endpoint | |
| --- | --- |
| `GET /api/health` | Database round-trip, document count, LLM configured, embedding model matches the index (503 if the database is down) |
| `GET /api/metrics` | Documents per source, chunks per strategy, LLM calls/failures/tokens/latency per model |
| `GET /api/documents` · `GET /api/documents/{id}` | List (filter by source type and title, paged) · detail |
| `POST /api/documents` · `PUT /api/documents/{id}` · `DELETE /api/documents/{id}` | Add · update (new version) · delete; max 10 MB, unsupported or broken files get a 422 with the reason |
| `POST /api/search` | Hybrid / dense / keyword search with score breakdowns |
| `POST /api/investigate` | Investigation, one JSON result |
| `GET /api/investigate/stream?question=` | The same as server-sent events: `search`, `read_more`, `warning`, `verification`, then `final` |

Investigations are rate-limited per client (3/minute, 25/day by default) because the demo runs
on free LLM quotas; over the limit the API answers 429 with `Retry-After`. Closing the page
cancels a running investigation, so no tokens are spent on an answer nobody reads.

## Testing

```bash
pytest                      # 157 tests, ~10 s; needs the database from `docker compose up`
pytest --cov                # with coverage
pytest tests/test_injection.py -v
```

The tests run the real code against a real Postgres (a separate `investigator_test`
database, reset per test) and replace only what is slow or costs money: a fake embedder and a
scripted LLM, so the agent's control flow is tested turn by turn without network calls. The
embedding model, tokenizer, loaders and SQL are tested for real.

Coverage: 93% of the code that runs in the app (API, agent, LLM client, retrieval, ingestion),
76% overall; the rest is offline tooling (dataset builders, evaluation runners) whose output is
checked into `eval/`.

### Prompt injection

Documents are untrusted input that reaches an LLM. `tests/test_injection.py` attacks each
defence separately:

| Layer | Defence | Tested with |
| --- | --- | --- |
| Ingestion | Text a reader can't see is never indexed: hidden HTML (`hidden`, `aria-hidden`, `display:none`, `visibility:hidden`, `opacity:0`, `font-size:0`, comments, scripts, templates) and hidden Word runs | A page and a .docx with a payload in each hiding place |
| Detection | Passages that address an AI are labelled "do not follow" in the model's context | 10 attack phrasings (incl. chat-template tokens) must be flagged; 7 ordinary incident sentences using the same words must not |
| Isolation | Evidence sits in `<evidence>` blocks that document text can't close (`</evidence>` is escaped) and whose attributes a title can't forge (`status="final"`) | Breakout and attribute-forging documents |
| Least privilege | The model can only search and read; invented tools, unseen evidence ids and citations to evidence never shown are refused; document text only ever arrives as tool output | `delete_document` / `send_email` calls, `read_more` on unknown ids |
| Verifier | The claim checker gets the same escaped blocks and a rule to ignore instructions in them | A passage telling the verifier to approve every claim |

The live check against a real model is evaluation question S14 (a planted vendor advisory that
blames a DDoS attack and says to cite only itself); the agent answers from the real sources and
reports the advisory's instruction. Not handled: text hidden by colour or off-screen
positioning (needs a renderer), and hidden text in PDFs.

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
frontend/           Angular UI (standalone components, signals)
eval/               Evaluation question sets, raw results and reports
docker/             Local Postgres init scripts
docker-compose.yml  Local development database
```
