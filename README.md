# AI Investigation Engine

A multi-document investigation system: it searches a large document collection, connects
facts across sources, and answers with citations, flagging contradictions and refusing to
answer when the evidence is insufficient.

**Live demo:** <https://investigation-engine-jpin.onrender.com/> (66,569 documents). It runs on
free plans: the first visit after 15 idle minutes takes about a minute while the server wakes,
and each LLM has a daily token budget (see [Deployment](#deployment)).

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
so when the evidence is insufficient. Citations to evidence the model was never shown are
removed, and an answer that cites nothing is sent back once to add citations. A second LLM pass
checks every cited claim against its evidence.

```bash
python -m investigator.agent "Which system changes caused the payment processing delays in March 2026?"
```

## LLM providers

Groq and Gemini, through their OpenAI-compatible APIs, behind one client with retries
(exponential backoff, or the provider's own `Retry-After`), a fallback chain of models
(`LLM_MODELS`), a circuit breaker, tool calling, streaming and token/latency counters.

Free-tier limits measured on 2026-10-28, which shape the chain: every Groq model has its own
budget of 200,000 tokens/day, 8,000 tokens/minute and 1,000 requests/day; every Gemini model has
its own daily quota (`gemini-3.5-flash`: 20 requests/day, 5/minute). A median investigation uses
~9k tokens, so one Groq model serves only about 20 a day, and the chain spreads the load over
models with separate budgets:

| Where | `LLM_MODELS` |
| --- | --- |
| Default (local) | `gpt-oss-120b` -> `gpt-oss-20b` (Groq) -> `gemini-3.5-flash` |
| Live demo ([render.yaml](render.yaml)) | `gpt-oss-120b` -> `qwen3.8-27b` -> `gpt-oss-20b` (Groq) -> `gemini-3.5-flash-lite` -> `gemini-3.5-flash` |

A model whose budget is spent answers 429 with a long `Retry-After`; rather than wait, the
client moves on to the next model, and after 3 failures in a row a circuit breaker skips that
model for 60 seconds before letting one probe call through.

```bash
python -m investigator.llm --models   # model ids your keys can use
python -m investigator.llm            # live tool-call round trip + streaming per provider
```

## Answer evaluation

```bash
python -m investigator.evaluation.answers --configs agent-flash-lite,single-flash-lite
python -m investigator.evaluation.answers --configs agent-qwen --answers-only   # judge later
python -m investigator.evaluation.answers --configs agent-qwen --retry-failed
python -m investigator.evaluation.answers --configs agent-flash-lite,single-flash-lite --report-only
```

42 questions, frozen in `eval/answer_questions.json`: the 20 synthetic questions
(single- and multi-document, contradictions, unanswerable, decoys, prompt injection) and 22
HotpotQA multi-hop questions from the retrieval test split (16 bridge, 6 comparison).

Each configuration uses exactly one model with no fallback, so a result can be attributed:

| Configuration | What it is |
| --- | --- |
| `agent-flash-lite` | The investigation agent on `gemini-3.5-flash-lite` |
| `single-flash-lite` | The baseline on the same model: one search with the question as asked, one answer |
| `agent-qwen` | The agent on Groq's `qwen3.8-27b` |

The main comparison is the agent against the baseline on the same model. Gemini flash-lite
carries it because Groq's 200k tokens/day per model couldn't fit the original plan (~1M
tokens).

A different model (`gpt-oss-20b`) grades each answer against the reference answer as
correct, partial or incorrect, so no model grades itself. The report also measures, without
the judge:
- citation precision and recall against the gold documents;
- evidence recall (gold documents the agent saw);
- the share of claims the verifier found supported by their citations;
- abstention on unanswerable questions;
- contradiction detection;
- the prompt-injection question;
- cost per question.

A paired bootstrap gives a 95% confidence interval for the difference between configurations.

Every answer and judgment is appended to `eval/answers/*.jsonl` as soon as it exists, so an
interrupted run, or one stopped by a spent quota, resumes where it stopped. Results:
[eval/answer_report.md](eval/answer_report.md).

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

## Deployment

```
browser ──HTTPS──> Render web service (Docker: FastAPI + built Angular UI), Virginia
                     ├─ Postgres, TLS ──> Neon (Postgres 18 + pgvector), AWS us-east-1
                     └─ HTTPS ──> Groq, Gemini
```

| Piece | Free tier (checked 2026-10-28) | What it means for the demo |
| --- | --- | --- |
| Render web service | 512 MB RAM, 0.1 CPU, 750 instance-hours a month; sleeps after 15 minutes without traffic; disk not persistent | The app uses ~170 MB idle and ~220 MB during an upload (measured). **The first visit after a sleep waits about a minute** while the container starts; uploads embed slowly on 0.1 CPU |
| Neon Postgres | 0.5 GB storage, 100 compute-hours a month, scales to zero after 5 idle minutes | One chunking strategy (structural) fits: 405 MB. The first query after idle waits ~1 s for the database to wake |
| Groq | Per model: 200k tokens/day, 8k tokens/min, 1,000 requests/day | ~20 investigations per model per day (median ~9k tokens each); three models in the chain |
| Gemini | Per model daily quotas (gemini-3.5-flash: 20 requests/day) | Last in the fallback chain |

Hosting history: the plan was a Hugging Face Space (2 vCPU, 16 GB), but Docker Spaces now need
a paid PRO account (the API answered `402 Payment Required`), so the demo runs on Render's free
plan, which fits the measured memory.

**Persistence.** All state is in Neon: documents uploaded on the demo survive restarts and
redeploys, and the app writes nothing to the container's own disk.

**Secrets.** API keys and the database URL live in `.env` locally (git-ignored, excluded from
the Docker build context) and in Render's encrypted environment settings in production, which
the app reads as environment variables. [render.yaml](render.yaml) declares them with
`sync: false`: Render asks for the values when the service is created, and they never appear in
the repository. Non-secret settings (`LLM_MODELS`) are in render.yaml.

**Hosts that block port 5432.** Some hosts (Hugging Face Spaces, for one) only allow outgoing
web ports. Neon also accepts the Postgres protocol inside a TLS WebSocket on 443 (what its
serverless driver uses), so with `DATABASE_TRANSPORT=websocket` the app starts a small bridge
([investigator/tunnel.py](backend/investigator/tunnel.py)) on 127.0.0.1 that relays each pooled
connection over its own WebSocket; asyncpg and every query stay unchanged. Render allows 5432,
so the demo connects directly; the deploy command verifies Neon through the tunnel.

**The image** ([Dockerfile](Dockerfile)) has three stages: the Angular build, the int8
embedding model (built from the pinned upstream model with the exact package versions in
`backend/constraints.txt`; the build fails unless its SHA-256 matches the model that produced
the stored vectors), and the app, running as a non-root user on the port the host gives it.

**Deploying**

```bash
# 1. The index: copies the local database (structural chunks only) to Neon - no re-embedding.
#    .env: DEPLOY_DATABASE_URL = Neon's direct (not pooled) connection string
python -m investigator.deploy

# 2. The app: push to GitHub, then in Render: New > Blueprint > this repository. Render reads
#    render.yaml, asks for DATABASE_URL / GROQ_API_KEY / GEMINI_API_KEY, builds the Dockerfile
#    and redeploys on every push to main.

# The same image locally
docker build -t investigator . && docker run -p 7860:7860 --env-file .env investigator
```

## Repository layout

```
backend/
  investigator/     Python package (API, ingestion, retrieval, agent, evaluation)
  migrations/       Numbered SQL migrations, applied in order on startup
  tests/
frontend/           Angular UI (standalone components, signals)
eval/               Evaluation question sets, raw results and reports
Dockerfile          The production image (UI build, embedding model, app)
render.yaml         The live demo's service definition (Render Blueprint)
docker/             Local Postgres init scripts
docker-compose.yml  Local development database
```
