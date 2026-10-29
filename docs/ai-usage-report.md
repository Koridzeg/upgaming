# AI Usage Report

English · [ქართული](ai-usage-report.ka.md)

I built this project with two AI tools. **Claude Code** wrote most of the code, tests and
documentation. **ChatGPT Astra** was my second opinion: I used it to review that code and to
understand the concepts behind it. I made the decisions, set up every account and secret,
reviewed and tested each step, and committed the work myself. I am responsible for all of it,
and I can explain and change any part without AI help.

| Tool | What I used it for |
| --- | --- |
| Claude Code (Anthropic's coding agent, in VS Code, on a Claude Opus model) | Design options, code, tests, experiments, debugging, documentation drafts |
| ChatGPT Astra | Reviewing the code Claude Code wrote; understanding concepts (RAG, embeddings, ranking, the design decisions) |

## How the work was organised

I explicitly did not let the assistant generate the application in one go. The project was built
in 15 steps (0 to 14), and every step followed the same loop:

1. **Plan.** The assistant proposed a design with alternatives and trade-offs; I chose.
2. **Tests first.** For behaviour changes, a test was written and seen to fail before the fix.
3. **Code.** The assistant wrote the code; tests and the linter had to pass.
4. **Review and test.** I read the code, had ChatGPT Astra review it as a second opinion, ran
   the tests, and tried the feature myself: in the terminal, through the API, in the UI, and on
   the live deployment.
5. **Explain.** Each step's reasoning, measurements and interview questions were recorded in a
   build-notes document, so I could defend every decision.
6. **Commit.** I committed and pushed each step myself.

**Secrets rule.** API keys and database URLs lived only in my `.env` and in Render's settings. The
assistant's scripts printed only whether a key was set and its length, never its value.

## What Claude Code did

| Area | Contribution |
| --- | --- |
| Code | Most of the Python backend (ingestion, retrieval, agent, LLM client, API, evaluation), the SQL migrations, the Angular UI, the Dockerfile and deployment scripts |
| Tests | Most of the 163 tests, including the prompt-injection suite |
| Design | Options with trade-offs at each step, for example int8 vs fp32 embeddings, two chunking strategies, rank fusion and the agent loop |
| Experiments | Ran the retrieval and answer evaluations, measured memory, latency and free-tier limits, and read logs to diagnose failures |
| Documentation | Drafts of the README, these reports and the build notes |

## What I did

- **Decisions:**
  - Angular for the UI (my main stack).
  - Free tiers only, accepting a smaller answer evaluation.
  - Groq and Gemini as LLM providers.
  - PostgreSQL with pgvector, on Neon in production.
  - Render after Hugging Face Docker Spaces turned out to need a paid plan.
  - Building every bonus feature.
  - The step-by-step method itself.
- **Accounts and secrets:**
  - created the Neon, Render, Groq, Gemini and Hugging Face accounts and API keys;
  - loaded the database URL;
  - configured the production environment on Render.
- **Review and testing:**
  - read each step's code and explanation, with ChatGPT Astra as a second reviewer;
  - used Astra to make sure I understood the concepts, not just the code;
  - ran the test suite;
  - tested the API, the UI and the live demo by hand.
- **Fixes in production:** found and corrected the misconfigured `DATABASE_URL` on Render that
  stopped the first deploy.
- **History:** every commit in the repository is mine.

## Where Claude Code was wrong

Claude Code made mistakes. These are the ones caught during the project, and what caught each
one.

| Mistake | Caught by |
| --- | --- |
| Step 0 ruled out Render because 512 MB seemed "too tight for the model"; measured, the app uses 140-220 MB, and Render now hosts the demo | Measuring memory before deploying |
| A claim in the build notes that pdfplumber releases Python's GIL (it doesn't); another overstated how many statistics refreshes ten uploads cause | My review of the notes; both corrected |
| A prompt-injection test passed for the wrong reason: the hidden payload sat outside the element under test | Checking that each test fails without its fix |
| The assistant's own rewrite of the injection detector flagged "Rollback instructions: stop the deploy" | False-positive tests: ordinary incident sentences must stay unflagged |
| Deep links in the UI returned 404: the code caught FastAPI's `HTTPException` subclass, but static files raise Starlette's parent class | Testing the UI by hand; a regression test was added |
| The evaluation's resume logic would have skipped 65 answers whose judging failed, silently scoring 19 of 84 | Reviewing the judge output before writing the report |
| Deployment instructions didn't make clear which of two database URLs to paste into Render | The failed first deploy; startup now logs the database host and port, without credentials |
| The size of the local database was stated as about 1 GB; it is 750 MB | Measuring it |
| A live answer came back without citations | Testing the live demo; an uncited answer is now sent back once to add them |

## How correctness was checked

- **Tests against real infrastructure:** 163 tests run against a real Postgres database. Only the
  embedder and the LLM are replaced, by a fake and a scripted model. That covers 93% of the code
  that runs in the app.
- **Measurements over assumptions:** retrieval and answer quality are measured on fixed question
  sets with confidence intervals. Numbers in the documentation come from those runs.
- **Live checks:** each step was tried against the real 66,569-document database and real LLMs,
  and the deployment was verified end to end on its public URL.

## AI inside the product

Separately from the development tool, the product itself uses LLMs:

- the investigation agent (a chain of Groq and Gemini models);
- a verifier that checks each cited claim;
- in the evaluation, a judge model that grades answers. It is always a different model from the
  one being graded.

These are described in the [README](../README.md) and the [Evaluation Report](evaluation-report.md).
