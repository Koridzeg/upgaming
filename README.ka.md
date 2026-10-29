# AI Investigation Engine

[English](README.md) · ქართული

მრავალდოკუმენტიანი საგამოძიებო სისტემა: ეძებს დოკუმენტების დიდ კოლექციაში, აკავშირებს ფაქტებს
სხვადასხვა წყაროდან და პასუხობს წყაროს მითითებით. ის აღნიშნავს ურთიერთგამომრიცხავ ინფორმაციას
და, როცა მტკიცებულება არასაკმარისია, პასუხისგან თავს იკავებს.

**Live Demo:** <https://investigation-engine-jpin.onrender.com/> (66,569 დოკუმენტი). ის უფასო
ტარიფებზე მუშაობს: 15-წუთიანი უმოქმედობის შემდეგ პირველ შესვლას დაახლოებით ერთი წუთი სჭირდება,
სანამ სერვერი გაიღვიძებს. ამასთან, ყოველ LLM-ს ტოკენების დღიური ბიუჯეტი აქვს (იხ.
[Deployment](#deployment)).

**ანგარიშები:** [შეფასების ანგარიში](docs/evaluation-report.ka.md) ·
[AI-ის გამოყენების ანგარიში](docs/ai-usage-report.ka.md)

## როგორ მუშაობს

```
                 ┌─ ingestion (CLI or upload API) ─────────────────────────────┐
 PDF, DOCX,      │ load -> drop hidden text -> metadata -> dedupe (content     │
 HTML, MD, TXT,  │ hash) -> chunk (structural | fixed) -> embed (bge-small     │
 JSON, CSV  ───> │ int8, ONNX) -> store, all in one transaction per document   │
                 └──────────────────────────────┬──────────────────────────────┘
                                                v
                         PostgreSQL + pgvector: documents, versions, chunks,
                         halfvec HNSW index per strategy, BM25 term statistics
                                                ^
                 ┌─ investigation (UI or API) ──┴──────────────────────────────┐
 question  ───>  │ LLM with tools: search / read_more / final_answer           │
                 │   search = dense (HNSW) + BM25 -> weighted RRF -> re-rank   │
                 │ -> answer with [E#] citations, contradictions, abstention   │
                 │ -> citation checks -> verifier LLM checks each cited claim  │
                 └─────────────────────────────────────────────────────────────┘
```

გამოძიება ნაბიჯ-ნაბიჯ:

1. კითხვა LLM-ს გადაეცემა სამ ინსტრუმენტთან ერთად: `search(query, k)`, `read_more(evidence_id)`
   (შედეგის მიმდებარე ფრაგმენტები) და `final_answer`.
2. ყოველი `search` ასრულებს Hybrid Search-ს. მოთხოვნა HNSW ვექტორული ძიებისთვის embedding-ად
   გარდაიქმნება, BM25-ისთვის კი ტოკენებად იყოფა და წინასწარ დათვლილ ტერმინების სტატისტიკას
   იყენებს. ორი რანჟირება შეწონილი Reciprocal Rank Fusion-ით ერთიანდება, შემდეგ კი Re-ranking
   ხდება.
3. ყოველი ფრაგმენტი ხდება მტკიცებულება `E1`, `E2`, ... escape-ირებულ `<evidence>` ბლოკში,
   თავისი სათაურით, სექციით, გვერდით და დოკუმენტის სტატუსით. ფრაგმენტები, რომლებიც AI-ს
   მიმართავს, მოინიშნება როგორც "do not follow".
4. მოდელი ხელახლა ეძებს იმის გამოყენებით, რაც უკვე გაიგო: პირველი ძიებით ნაპოვნი ერთეული
   (entity) შემდეგი მოთხოვნა ხდება (მრავალსაფეხურიანი ძიება). მაქსიმუმ 5 სვლის შემდეგ მან
   აუცილებლად უნდა უპასუხოს.
5. `final_answer` შეიცავს პასუხს `[E#]` ციტირებებით, სტატუსს (`answered`, `partial` ან
   `insufficient_evidence`), ნაპოვნ წინააღმდეგობებს (თითოეულ მხარეს თავისი მტკიცებულებით) და
   იმას, რაც აკლია.
6. შედეგი შემოწმებას გადის. ციტირებები მტკიცებულებაზე, რომელიც არასოდეს ყოფილა ნაჩვენები,
   იშლება, ხოლო პასუხი ციტირებების გარეშე ერთხელ ბრუნდება მათ დასამატებლად. შემდეგ ცალკე
   შემმოწმებელი (verifier) გამოძახება ყოველ ციტირებულ წინადადებას მის მტკიცებულებასთან
   მიმართებით აფასებს.
7. ყოველი ნაბიჯი UI-ში Streaming-ით გადაიცემა server-sent event-ის სახით, ასე რომ მომხმარებელი
   ძიებებს მიმდინარეობისას ხედავს.

## ტექნიკური გადაწყვეტილებები

შეფასების რიცხვები HotpotQA-ის სატესტო ნაწილს (test split) ეხება: 150 კითხვა. პარამეტრები სხვა
150 კითხვაზე შეირჩა. ფრჩხილებში მოცემულია paired-bootstrap 95% სანდოობის ინტერვალები.

| გადაწყვეტილება | არჩევანი | რატომ | უარყოფილი ალტერნატივა |
| --- | --- | --- | --- |
| მონაცემები | HotpotQA-ის distractor ვალიდაციის ნაკრები (66,581 უნიკალური პარაგრაფი 7,405 კითხვიდან) და სინთეტიკური ინციდენტების კორპუსი (15 ფაილი, 6 ფორმატი) | ეტალონური (gold) დამხმარე პარაგრაფები Recall@K-სა და MRR-ს ხელით მონიშვნის გარეშე გაზომვადს ხდის; სინთეტიკური კორპუსი ამატებს ფაილის ფორმატებს, ურთიერთგამომრიცხავ ინფორმაციას, ხარვეზებს და Prompt Injection-ს | RAGBench, 2WikiMultihopQA: მსგავსია, მაგრამ HotpotQA-ის მონიშვნებით შეფასება ყველაზე მარტივია; danluu post-mortems: ეტალონური მონიშვნები არ აქვს |
| მონაცემთა ბაზა | PostgreSQL + pgvector | დოკუმენტები, ვერსიები, chunk-ები, ვექტორები და BM25 სტატისტიკა ერთ საცავშია; განახლება ტექსტს, chunk-ებს და ვექტორებს ერთ ტრანზაქციაში ცვლის; Neon-ს უფასო ტარიფი აქვს | Qdrant, Pinecone: დოკუმენტებსა და მეტამონაცემებს მეორე საცავი დასჭირდებოდა, რომელიც პირველთან სინქრონში უნდა ყოფილიყო |
| Chunking | სტრუქტურული: მთლიანი პარაგრაფები, სიის ელემენტები და ცხრილის სტრიქონები ჯგუფდება მაქსიმუმ 128 ტოკენიან chunk-ებად; სათაური ახალ chunk-ს იწყებს | Sentence recall@10 +0.023 [+0.009, +0.041] ფიქსირებულ ფანჯრებთან შედარებით, ტოკენების იმავე ხარჯით | ფიქსირებული 128-ტოკენიანი ფანჯრები 32 ტოკენის გადაფარვით: დატოვებულია შედარებისთვის |
| Embeddings | `bge-small-en-v1.5` (384 განზომილება), int8-მდე კვანტიზებული, ლოკალურად ეშვება ONNX Runtime-ზე | უფასოა, მასობრივი ჩატვირთვისას მოთხოვნების სიხშირის შეზღუდვები არ არის, შედეგები განმეორებადია. int8 მოდელი 34 MB-ია 133 MB-ის ნაცვლად და 1.7x უფრო სწრაფია (58 vs 34 texts/s), იმავე ხარისხით: self-retrieval top-1 98.0% vs 97.7%. ეტევა 512 MB-იან უფასო სერვერზე | Embedding API-ები: სიხშირის შეზღუდვები და ხარჯი 66k დოკუმენტზე, თანაც ყოველი მოთხოვნა სხვა სერვისზე იქნებოდა დამოკიდებული |
| საკვანძო სიტყვებით ძიება | BM25, დაწერილი SQL-ში `term_stats` ცხრილზე | Postgres-ის `ts_rank`-ს inverse document frequency არ აქვს, ამიტომ იშვიათი, ინფორმატიული ტერმინი გავრცელებულზე მეტად არ ითვლება | `ts_rank`; Elasticsearch (კიდევ ერთი სერვისი დასაჰოსტად) |
| რანჟირება (საკუთარი ალგორითმი) | dense და BM25 რანჟირებების შეწონილი RRF, შემდეგ re-ranker (სათაურის დასახელების ბონუსი, MMR მრავალფეროვნება) | RRF რანგებს აერთიანებს, ამიტომ სხვადასხვა სკალის ქულებს კალიბრაცია არ სჭირდება. Hybrid მარტო dense-ს +0.060 all@10-ით [0.000, +0.127] ჯობია, მარტო BM25-ს კი +0.120-ით [+0.067, +0.180]. მორგებული re-ranker ჩვეულებრივ RRF-ს მხოლოდ +0.007-ს მატებს, რაც სტატისტიკურად მნიშვნელოვანი არ არის, და მორგებამ MMR გამორთო (λ = 1.0) | დაუმუშავებელი ქულების გასაშუალოება (cosine და BM25 სხვადასხვა სკალაზეა); მხოლოდ ბიბლიოთეკის re-ranker (დავალება საკუთარ კომპონენტს მოითხოვს) |
| აგენტი | საკუთარი Tool Calling ციკლი: search, read_more, final_answer | 38 ეტალონური დოკუმენტიდან, რომლებსაც საუკეთესო ერთჯერადი ძიება ვერ პოულობს, არც ერთი არ არის დასახელებული თავის კითხვაში: ეს bridge ერთეულებია, რომელთა დასახელებაც მხოლოდ მეორე ძიებას შეუძლია, პირველის ნაპოვნის გამოყენებით. საკუთარი ციკლით ყოველი ნაბიჯი ხილული რჩება და სკრიპტირებული LLM-ით სვლა-სვლით ტესტირდება | LangChain-ის ან LlamaIndex-ის აგენტები: დამალული prompt-ები და მართვის ნაკადი, უფრო რთულია მათი ტესტირება და ახსნა |
| LLM-ები | Groq-ისა და Gemini-ის უფასო ტარიფები მათი OpenAI-თავსებადი API-ებით, ერთი საკუთარი კლიენტი | უფასოა; ყოველ მოდელს საკუთარი ბიუჯეტი აქვს, ამიტომ მოდელების ჯაჭვი მეტ მომხმარებელს ემსახურება; ერთი კლიენტი უზრუნველყოფს retry-ებს, backoff-ს, `Retry-After`-ს, fallback-ს და circuit breaker-ს | ფასიანი API-ები (დემო კრედიტებს დახარჯავდა); ორი პროვაიდერის SDK ერთი და იმავე პროტოკოლისთვის |
| API | FastAPI, ასინქრონული, server-sent events | აგენტი ძირითადად I/O-ს ელოდება; SSE ცალმხრივი ჩვეულებრივი HTTP-ია, რაც ნაბიჯების Streaming-ისთვის საკმარისია | WebSockets: ორმხრივი არხი საჭირო არ არის |
| მონაცემთა ბაზის დრაივერი | asyncpg, სუფთა SQL | ძიება იყენებს ვექტორულ და full-text ოპერატორებს, რომლებსაც ORM დამალავდა; მუშაობს Windows-ის ნაგულისხმევ event loop-ზე | SQLAlchemy; psycopg 3 (მის async რეჟიმს Windows-ზე სხვა event loop სჭირდება) |
| UI | Angular 19 (standalone კომპონენტები, signals), აწყობილი იმავე image-ში | ერთი კონტეინერი, ერთი deploy; ავტორის ძირითადი frontend სტეკი | Streamlit ან Gradio: UI-ზე ნაკლები კონტროლი და მეორე პროცესი |
| ჰოსტინგი | Render-ის უფასო ტარიფი (Docker) და Neon-ის უფასო ტარიფი (Postgres) | აპლიკაცია 140-220 MB-ს იყენებს, რაც Render-ის 512 MB-ში ეტევა; Neon მონაცემებს გადატვირთვების შემდეგაც ინახავს | Hugging Face Docker Spaces ახლა ფასიან ტარიფს მოითხოვს; serverless პლატფორმები არ ერგება 30-60 s ხანგრძლივობის Streaming-იან გამოძიებას, რომლის დროსაც მოდელი მეხსიერებაშია |

## ლოკალური დეველოპმენტი

წინაპირობები: Python 3.12+, Node 20+, Docker.

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

## მონაცემების მომზადება

ორივე მონაცემთა ნაკრები სკრიპტებით იქმნება თავიდან, ამიტომ შედეგები განმეორებადია.

```bash
pip install -e ".[data]"                       # pyarrow, fpdf2, python-docx
python -m investigator.datasets.hotpotqa      # -> data/processed/hotpotqa/ (66,581 documents)
python -m investigator.datasets.synthetic     # -> data/synthetic/corpus/ (15 files, 6 formats)
```

- **HotpotQA** (distractor setting, validation split), ფიქსირებულია dataset-ის commit-ზე `1908d6af`
  და შემოწმებულია SHA-256-ით. ყველა 7,405 კითხვის კონტექსტის პარაგრაფები ერთ საერთო ნაკრებში
  გაერთიანდა და დუბლიკატები მოცილდა (73,700 საწყისი, 66,581 უნიკალური), ასე რომ ყოველ კითხვას
  მთელი ნაკრებიდან უნდა გაეცეს პასუხი.
- **სინთეტიკური ინციდენტების კორპუსი**: გამოგონილი კომპანიის დოკუმენტები ჩადებული
  ურთიერთგამომრიცხავი ინფორმაციით, ხარვეზებით, სატყუარებით (decoys) და Prompt Injection-ის
  გვერდით. იხ. [data/synthetic/README.md](data/synthetic/README.md).

## მხარდაჭერილი ფაილის ფორმატები

PDF, DOCX, HTML, Markdown, ჩვეულებრივი ტექსტი, JSON / JSON Lines და CSV. ყოველი ფორმატი ერთსა და
იმავე მოდელად გარდაიქმნება (სათაური, მეტამონაცემები, დალაგებული ბლოკები). ფაილის გარდაქმნის
შედეგის სანახავად:

```bash
python -m investigator.ingestion.loaders path/to/file.pdf
```

## Embeddings და მასობრივი ჩატვირთვა

Chunk-ების embedding-ები იქმნება `BAAI/bge-small-en-v1.5`-ით (384 განზომილება), რომელიც int8-მდეა
კვანტიზებული და ONNX Runtime-ზე ეშვება. კვანტიზებული მოდელი ერთხელ იქმნება ფიქსირებული upstream
მოდელიდან:

```bash
python -m investigator.embedding                              # -> data/models/*.onnx
python -m investigator.ingestion.bulk --strategies fixed,structural   # all 66,581 HotpotQA docs
```

მასობრივი ჩამტვირთავი უკვე შენახულ დოკუმენტებს გამოტოვებს, ამიტომ შეწყვეტის შემდეგ მისი ხელახლა
გაშვება შეიძლება. ბაზა იმახსოვრებს, რომელმა მოდელმა შექმნა მისი ვექტორები, და ნებისმიერი სხვა
მოდელის ვექტორებს უარყოფს.

## ძიება

Hybrid Search: dense (pgvector HNSW) და BM25 საკვანძო სიტყვების კანდიდატები შეწონილი Reciprocal
Rank Fusion-ით ერთიანდება, შემდეგ კი Re-ranking ხდება (სათაურის დასახელება + MMR მრავალფეროვნება).
ყოველი შედეგი აჩვენებს, რატომ დაიკავა თავისი ადგილი.

```bash
python -m investigator.retrieval "Were Scott Derrickson and Ed Wood of the same nationality?"
python -m investigator.retrieval "pool size during INC-2026-0311" --mode keyword --k 5
python -m investigator.retrieval --refresh-stats   # recompute BM25 term statistics
```

## ძიების შეფასება

```bash
python -m investigator.evaluation.retrieval   # ~1 minute; writes eval/retrieval_report.md
```

300 HotpotQA კითხვა (ფიქსირებული seed-ით, სტრატიფიცირებული; 150 პარამეტრების მოსარგებად და 150
შედეგების საანგარიშოდ; id-ები ფიქსირებულია `eval/retrieval_sample.json`-ში) და ის სინთეტიკური
კითხვები, რომლებზეც პასუხი არსებობს. მეტრიკები: recall@K, all@K (ნაპოვნია ყველა ეტალონური
დოკუმენტი), MRR, nDCG@10, sentence recall@10, paired-bootstrap სანდოობის ინტერვალებით. იხ.
[eval/retrieval_report.md](eval/retrieval_report.md).

## გამოძიება

აგენტი იმდენ ნაბიჯად ეძებს, რამდენიც სჭირდება (ყოველ ძიებას შეუძლია გამოიყენოს ის, რაც წინამ
გამოავლინა), შემდეგ პასუხობს `[E#]` ციტირებებით, აღნიშნავს წყაროებს შორის წინააღმდეგობებს და
პირდაპირ ამბობს, როცა მტკიცებულება არასაკმარისია. ციტირებები მტკიცებულებაზე, რომელიც მოდელს
არასოდეს უნახავს, იშლება, ხოლო პასუხი, რომელიც არაფერს ციტირებს, ერთხელ ბრუნდება ციტირებების
დასამატებლად. LLM-ის მეორე გავლა ყოველ ციტირებულ დებულებას თავის მტკიცებულებასთან ამოწმებს.

```bash
python -m investigator.agent "Which system changes caused the payment processing delays in March 2026?"
```

## LLM პროვაიდერები

Groq და Gemini, მათი OpenAI-თავსებადი API-ებით, ერთი კლიენტის მიღმა. კლიენტს აქვს retry-ები
(exponential backoff ან პროვაიდერის საკუთარი `Retry-After`), მოდელების fallback ჯაჭვი
(`LLM_MODELS`), circuit breaker, Tool Calling, Streaming და ტოკენების/დაყოვნების მთვლელები.

უფასო ტარიფის შეზღუდვები 2026-10-28-ს გაიზომა და სწორედ ისინი განსაზღვრავს ჯაჭვს. ყოველ Groq
მოდელს საკუთარი ბიუჯეტი აქვს: 200,000 tokens/day, 8,000 tokens/minute და 1,000 requests/day.
ყოველ Gemini მოდელს საკუთარი დღიური კვოტა აქვს (`gemini-3.5-flash`: 20 requests/day, 5/minute).
მედიანური გამოძიება ~9k ტოკენს ხარჯავს, ამიტომ ერთი Groq მოდელი დღეში მხოლოდ დაახლოებით 20
გამოძიებას ემსახურება. ჯაჭვი დატვირთვას ცალკე ბიუჯეტის მქონე მოდელებზე ანაწილებს:

| სად | `LLM_MODELS` |
| --- | --- |
| ნაგულისხმევი (ლოკალური) | `gpt-oss-120b` -> `gpt-oss-20b` (Groq) -> `gemini-3.5-flash` |
| Live Demo ([render.yaml](render.yaml)) | `gpt-oss-120b` -> `qwen3.8-27b` -> `gpt-oss-20b` (Groq) -> `gemini-3.5-flash-lite` -> `gemini-3.5-flash` |

მოდელი, რომლის ბიუჯეტიც ამოწურულია, პასუხობს 429-ით და გრძელი `Retry-After`-ით. ლოდინის ნაცვლად
კლიენტი შემდეგ მოდელზე გადადის. ზედიზედ 3 წარუმატებლობის შემდეგ circuit breaker ამ მოდელს 60
წამით გამოტოვებს, შემდეგ კი ერთ საცდელ გამოძახებას გაატარებს.

```bash
python -m investigator.llm --models   # model ids your keys can use
python -m investigator.llm            # live tool-call round trip + streaming per provider
```

## პასუხების შეფასება

```bash
python -m investigator.evaluation.answers --configs agent-flash-lite,single-flash-lite
python -m investigator.evaluation.answers --configs agent-qwen --answers-only   # judge later
python -m investigator.evaluation.answers --configs agent-qwen --retry-failed
python -m investigator.evaluation.answers --configs agent-flash-lite,single-flash-lite --report-only
```

42 კითხვა, ფიქსირებული `eval/answer_questions.json`-ში: 20 სინთეტიკური კითხვა (ერთ- და
მრავალდოკუმენტიანი, ურთიერთგამომრიცხავი ინფორმაცია, პასუხის არმქონე, სატყუარები, Prompt
Injection) და 22 HotpotQA მრავალსაფეხურიანი კითხვა ძიების შეფასების სატესტო ნაწილიდან (16
bridge, 6 comparison).

ყოველი კონფიგურაცია ზუსტად ერთ მოდელს იყენებს, fallback-ის გარეშე, ასე რომ შედეგი კონკრეტულ
მოდელს მიეწერება:

| კონფიგურაცია | რა არის |
| --- | --- |
| `agent-flash-lite` | საგამოძიებო აგენტი `gemini-3.5-flash-lite`-ზე |
| `single-flash-lite` | საბაზისო ვარიანტი (baseline) იმავე მოდელზე: ერთი ძიება კითხვით ისე, როგორც დაისვა, და ერთი პასუხი |
| `agent-qwen` | აგენტი Groq-ის `qwen3.8-27b`-ზე |

მთავარი შედარება აგენტსა და baseline-ს შორისაა, იმავე მოდელზე. ის Gemini flash-lite-ზე ტარდება,
რადგან Groq-ის 200k tokens/day თითო მოდელზე თავდაპირველ გეგმას (~1M ტოკენი) ვერ იტევდა.

სხვა მოდელი (`gpt-oss-20b`) ყოველ პასუხს საეტალონო პასუხთან ადარებს და აფასებს როგორც სწორს,
ნაწილობრივ სწორს ან არასწორს, ასე რომ არც ერთი მოდელი საკუთარ თავს არ აფასებს. ანგარიში ასევე
ზომავს, მსაჯის (judge) გარეშე:
- ციტირების precision-ს და recall-ს ეტალონურ დოკუმენტებთან მიმართებით;
- მტკიცებულების recall-ს (ეტალონური დოკუმენტები, რომლებიც აგენტმა ნახა);
- დებულებების წილს, რომლებიც შემმოწმებელმა მათი ციტირებებით დადასტურებულად მიიჩნია;
- პასუხისგან თავის შეკავებას პასუხის არმქონე კითხვებზე;
- ურთიერთგამომრიცხავი ინფორმაციის აღმოჩენას;
- Prompt Injection-ის კითხვას;
- ხარჯს თითო კითხვაზე.

paired bootstrap კონფიგურაციებს შორის სხვაობისთვის 95% სანდოობის ინტერვალს იძლევა.

ყოველი პასუხი და მსაჯის შეფასება `eval/answers/*.jsonl`-ს ემატება მაშინვე, როგორც კი შეიქმნება.
ამიტომ შეწყვეტილი გაშვება, ან კვოტის ამოწურვით შეჩერებული, იქიდან გრძელდება, სადაც გაჩერდა.
შედეგები: [eval/answer_report.md](eval/answer_report.md).

## ვებ UI და API

ერთი FastAPI პროცესი API-ს `/api`-ის ქვეშ ემსახურება, აწყობილ Angular აპლიკაციას კი `/`-ზე
(ნებისმიერი სხვა მისამართი აპლიკაციაზე გადადის, ამიტომ deep link-ები და ბრაუზერში გვერდის
განახლება მუშაობს).

| გვერდი | რას აკეთებს |
| --- | --- |
| Investigate | დასვით კითხვა; აგენტის ყოველი ძიება რეალურ დროში ჩანს, შემდეგ კი პასუხი დაკლიკებადი `[E#]` ციტირებებით (ციტირებული ფრაგმენტი, მისი სექცია, გვერდი, დოკუმენტის სტატუსი), წინააღმდეგობები, ნაკლული ინფორმაცია, დებულებების ვერიფიკაცია და ტოკენების/დაყოვნების მოხმარება |
| Documents | სია და ფილტრი, ატვირთვა (დამატება), ჩანაცვლება (განახლება ახალ ვერსიამდე), წაშლა, შიგთავსის, მეტამონაცემებისა და ვერსიების ისტორიის ნახვა |
| Search explorer | მხოლოდ ძიება, LLM-ის გარეშე: შეადარეთ hybrid / dense / BM25 და ნახეთ ყოველი ქულა, რომელიც თითოეულ ადგილს განაპირობებს |

| Endpoint | |
| --- | --- |
| `GET /api/health` | ბაზასთან round-trip, დოკუმენტების რაოდენობა, არის თუ არა LLM კონფიგურირებული, ემთხვევა თუ არა embedding მოდელი ინდექსს (503, თუ ბაზა მიუწვდომელია) |
| `GET /api/metrics` | დოკუმენტები წყაროების მიხედვით, chunk-ები სტრატეგიების მიხედვით, LLM-ის გამოძახებები/წარუმატებლობები/ტოკენები/დაყოვნება მოდელების მიხედვით |
| `GET /api/documents` · `GET /api/documents/{id}` | სია (ფილტრი წყაროს ტიპითა და სათაურით, გვერდებად) · დეტალები |
| `POST /api/documents` · `PUT /api/documents/{id}` · `DELETE /api/documents/{id}` | დამატება · განახლება (ახალი ვერსია) · წაშლა; მაქსიმუმ 10 MB, მხარდაუჭერელ ან დაზიანებულ ფაილებზე ბრუნდება 422 მიზეზის მითითებით |
| `POST /api/search` | Hybrid / dense / keyword ძიება ქულების დეტალური დაშლით |
| `POST /api/investigate` | გამოძიება, ერთი JSON შედეგი |
| `GET /api/investigate/stream?question=` | იგივე, server-sent events-ის სახით: `search`, `read_more`, `warning`, `verification`, შემდეგ `final` |

გამოძიებების სიხშირე თითო კლიენტზე შეზღუდულია (ნაგულისხმევად 3/minute, 25/day), რადგან დემო
LLM-ების უფასო კვოტებზე მუშაობს. ლიმიტის გადაჭარბებისას API პასუხობს 429-ით და `Retry-After`-ით.
გვერდის დახურვა მიმდინარე გამოძიებას აუქმებს, ამიტომ ტოკენები არ იხარჯება პასუხზე, რომელსაც
არავინ წაიკითხავს.

## ტესტირება

```bash
pytest                      # 164 tests, ~10 s; needs the database from `docker compose up`
pytest --cov                # with coverage
pytest tests/test_injection.py -v
```

ტესტები რეალურ კოდს რეალურ Postgres-ზე უშვებს (ცალკე `investigator_test` ბაზა, რომელიც ყოველი
ტესტისთვის თავიდან სუფთავდება) და ანაცვლებს მხოლოდ იმას, რაც ნელია ან ფული ჯდება: ყალბ embedder-ს
და სკრიპტირებულ LLM-ს. ასე აგენტის მართვის ნაკადი სვლა-სვლით მოწმდება, ქსელური გამოძახებების
გარეშე. Embedding მოდელი, ტოკენიზატორი, loader-ები და SQL რეალურად ტესტირდება.

დაფარვა (coverage): აპლიკაციაში გაშვებადი კოდის 93% (API, აგენტი, LLM კლიენტი, ძიება, დოკუმენტების
დამუშავება), 76% მთლიანად. დანარჩენი offline ინსტრუმენტებია (dataset-ების ამგები, შეფასების
გამშვებები), რომელთა შედეგიც `eval/`-შია შენახული.

### Prompt Injection

დოკუმენტები არასანდო შემავალი მონაცემებია, რომლებიც LLM-მდე აღწევს. `tests/test_injection.py`
თითოეულ დაცვას ცალ-ცალკე უტევს:

| ფენა | დაცვა | რით მოწმდება |
| --- | --- | --- |
| დოკუმენტების დამუშავება | ტექსტი, რომელსაც მკითხველი ვერ ხედავს, არასოდეს ინდექსირდება: დამალული HTML (`hidden`, `aria-hidden`, `display:none`, `visibility:hidden`, `opacity:0`, `font-size:0`, კომენტარები, სკრიპტები, template-ები) და დამალული Word run-ები | გვერდი და .docx, payload-ით ყოველ დასამალ ადგილას |
| აღმოჩენა | ფრაგმენტები, რომლებიც AI-ს მიმართავს, მოდელის კონტექსტში მოინიშნება როგორც "do not follow" | შეტევის 10 ფორმულირება (მათ შორის chat-template ტოკენები) უნდა მოინიშნოს; ინციდენტების 7 ჩვეულებრივი წინადადება, რომლებიც იმავე სიტყვებს იყენებს, არ უნდა მოინიშნოს |
| იზოლაცია | მტკიცებულება `<evidence>` ბლოკებშია, რომლებსაც დოკუმენტის ტექსტი ვერ დახურავს (`</evidence>` escape-ირებულია) და რომელთა ატრიბუტებს სათაური ვერ გააყალბებს (`status="final"`) | დოკუმენტები, რომლებიც ბლოკიდან გაღწევას ან ატრიბუტების გაყალბებას ცდილობს |
| მინიმალური პრივილეგიები | მოდელს მხოლოდ ძიება და წაკითხვა შეუძლია; მოგონილი ინსტრუმენტები, არნანახი მტკიცებულების id-ები და ციტირებები არასოდეს ნაჩვენებ მტკიცებულებაზე უარყოფილია; დოკუმენტის ტექსტი მხოლოდ ინსტრუმენტის შედეგის სახით შემოდის | `delete_document` / `send_email` გამოძახებები, `read_more` უცნობ id-ებზე |
| შემმოწმებელი (verifier) | დებულებების შემმოწმებელი იმავე escape-ირებულ ბლოკებს იღებს და წესს, რომ მათში არსებული ინსტრუქციები უგულებელყოს | ფრაგმენტი, რომელიც შემმოწმებელს ყველა დებულების დადასტურებას ავალებს |

რეალურ მოდელზე ცოცხალი შემოწმება შეფასების კითხვაა S14: ჩადებული შეტყობინება მომწოდებლისგან
(vendor advisory), რომელიც DDoS შეტევას აბრალებს და მოითხოვს, რომ მხოლოდ ის იყოს ციტირებული.
აგენტი რეალური წყაროებიდან პასუხობს და ამ შეტყობინების ინსტრუქციის შესახებ აცნობებს. არ არის
დაფარული: ფერით ან ეკრანს გარეთ პოზიციონირებით დამალული ტექსტი (საჭიროა renderer) და დამალული
ტექსტი PDF-ებში.

## ფაილების დამუშავება

```bash
# Load, deduplicate, chunk and store files (both chunking strategies, for comparison)
python -m investigator.ingestion.cli data/synthetic/corpus/* --strategies fixed,structural

# Duplicate and chunking statistics for both corpora
python -m investigator.ingestion.report
```

ფაილის ხელახლა დამუშავება უსაფრთხოა: უცვლელი შიგთავსი გამოტოვებულია, შეცვლილი შიგთავსი ახალ
ვერსიას ქმნის (ძველი `document_versions`-ში ინახება), ხოლო სხვა სახელით უკვე შენახული შიგთავსი
დუბლიკატად აღინიშნება და ორჯერ არ ინახება.

## Deployment

```
browser ──HTTPS──> Render web service (Docker: FastAPI + built Angular UI), Virginia
                     ├─ Postgres, TLS ──> Neon (Postgres 18 + pgvector), AWS us-east-1
                     └─ HTTPS ──> Groq, Gemini
```

| კომპონენტი | უფასო ტარიფი (შემოწმებულია 2026-10-28) | რას ნიშნავს დემოსთვის |
| --- | --- | --- |
| Render web service | 512 MB RAM, 0.1 CPU, 750 instance-hours თვეში; ტრაფიკის გარეშე 15 წუთის შემდეგ იძინებს; დისკი არ არის მუდმივი | აპლიკაცია უმოქმედობისას ~170 MB-ს იყენებს, ატვირთვისას კი ~220 MB-ს (გაზომილი). **ძილის შემდეგ პირველ შესვლას დაახლოებით ერთი წუთი უწევს ლოდინი**, სანამ კონტეინერი ჩაირთვება; 0.1 CPU-ზე ატვირთული ფაილების embedding ნელა მიმდინარეობს |
| Neon Postgres | 0.5 GB საცავი, 100 compute-hours თვეში, 5-წუთიანი უმოქმედობის შემდეგ ნულამდე მცირდება | ეტევა ერთი Chunking სტრატეგია (structural): 405 MB. უმოქმედობის შემდეგ პირველი მოთხოვნა ~1 s ელოდება ბაზის გაღვიძებას |
| Groq | თითო მოდელზე: 200k tokens/day, 8k tokens/min, 1,000 requests/day | ~20 გამოძიება თითო მოდელზე დღეში (მედიანა ~9k ტოკენი თითოზე); ჯაჭვში სამი მოდელია |
| Gemini | დღიური კვოტები თითო მოდელზე (gemini-3.5-flash: 20 requests/day) | fallback ჯაჭვში ბოლოა |

ჰოსტინგის ისტორია: გეგმა იყო Hugging Face Space (2 vCPU, 16 GB), მაგრამ Docker Spaces ახლა ფასიან
PRO ანგარიშს მოითხოვს (API-მ უპასუხა `402 Payment Required`), ამიტომ დემო Render-ის უფასო
ტარიფზე მუშაობს, რომელშიც გაზომილი მეხსიერება ეტევა.

**მონაცემების შენარჩუნება.** მთელი მდგომარეობა Neon-შია: დემოზე ატვირთული დოკუმენტები
გადატვირთვების და ხელახალი deploy-ების შემდეგაც რჩება, აპლიკაცია კი კონტეინერის საკუთარ დისკზე
არაფერს წერს.

**საიდუმლო მონაცემები.** API გასაღებები და ბაზის URL ლოკალურად `.env`-შია (git-ის მიერ
იგნორირებული, Docker build context-იდან გამორიცხული), production-ში კი Render-ის დაშიფრულ
environment პარამეტრებში, რომლებსაც აპლიკაცია გარემოს ცვლადებად კითხულობს.
[render.yaml](render.yaml) მათ `sync: false`-ით აცხადებს: Render მნიშვნელობებს სერვისის შექმნისას
ითხოვს და ისინი რეპოზიტორიაში არასოდეს ჩნდება. არასაიდუმლო პარამეტრები (`LLM_MODELS`)
render.yaml-შია.

**ჰოსტები, რომლებიც 5432 პორტს ბლოკავს.** ზოგიერთი ჰოსტი (მაგალითად, Hugging Face Spaces) მხოლოდ
გამავალ ვებ-პორტებს უშვებს. Neon Postgres-ის პროტოკოლს TLS WebSocket-ის შიგნითაც იღებს 443-ზე
(ამას იყენებს მისი serverless დრაივერი). ამიტომ `DATABASE_TRANSPORT=websocket`-ით აპლიკაცია
127.0.0.1-ზე უშვებს პატარა ხიდს ([investigator/tunnel.py](backend/investigator/tunnel.py)),
რომელიც pool-ის ყოველ კავშირს საკუთარი WebSocket-ით გადასცემს; asyncpg და ყველა მოთხოვნა უცვლელი
რჩება. Render 5432-ს უშვებს, ამიტომ დემო პირდაპირ უკავშირდება; deploy-ის ბრძანება Neon-ს გვირაბით
ამოწმებს.

**Image** ([Dockerfile](Dockerfile)) სამი ეტაპისგან შედგება: Angular-ის build, int8 embedding
მოდელი (აიწყობა ფიქსირებული upstream მოდელიდან, `backend/constraints.txt`-ში მითითებული პაკეტების
ზუსტი ვერსიებით; build ჩავარდება, თუ მისი SHA-256 არ ემთხვევა იმ მოდელს, რომელმაც შენახული
ვექტორები შექმნა) და აპლიკაცია, რომელიც non-root მომხმარებლით ეშვება იმ პორტზე, რომელსაც ჰოსტი
გამოუყოფს.

**Deploy**

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

## რეპოზიტორიის სტრუქტურა

```
backend/
  investigator/     Python package (API, ingestion, retrieval, agent, evaluation)
  migrations/       Numbered SQL migrations, applied in order on startup
  tests/
frontend/           Angular UI (standalone components, signals)
docs/               Evaluation Report and AI Usage Report (English and Georgian, *.ka.md)
eval/               Evaluation question sets, raw results and generated reports
Dockerfile          The production image (UI build, embedding model, app)
render.yaml         The live demo's service definition (Render Blueprint)
docker/             Local Postgres init scripts
docker-compose.yml  Local development database
```
