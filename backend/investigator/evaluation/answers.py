"""End-to-end evaluation: answer correctness, citations, contradictions, abstention, injection.

Questions (42, frozen in eval/answer_questions.json): the 20 synthetic questions (single- and
multi-document, contradictions, unanswerable, decoys, prompt injection) and 22 HotpotQA
multi-hop questions from the retrieval test split (16 bridge, 6 comparison, seeded).

Configurations - each uses exactly one model, no fallback, so results are attributable:
  agent-qwen          the investigation agent on qwen3.8-27b (Groq)
  agent-flash-lite    the agent on gemini-3.5-flash-lite
  single-flash-lite   the single-shot baseline (one search, one answer) on the same model
  agent-120b, single-120b, agent-20b   the same on Groq's gpt-oss models
Configurations on different models run in parallel: each model has its own budget.
Budget reality (measured): Groq's free tier allows 200,000 tokens per model per day, and the
original 3-configuration plan needed ~1M tokens. So the agent-vs-baseline comparison runs on
gemini-3.5-flash-lite, whose quota development hadn't touched.

Judge: a model different from the one being graded (--judge), so no model grades itself.

Every answer and judgment is appended to eval/answers/*.jsonl as soon as it exists, so an
interrupted run resumes where it stopped.

Usage:
    python -m investigator.evaluation.answers [--configs agent-qwen,agent-flash-lite] [--limit N]
    python -m investigator.evaluation.answers --configs agent-qwen --retry-failed
    python -m investigator.evaluation.answers --answers-only   # judge after a quota reset
    python -m investigator.evaluation.answers --report-only
"""

import argparse
import asyncio
import json
import random
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from statistics import mean

from investigator.agent.investigation import DatabaseRetriever, Investigator
from investigator.config import get_settings
from investigator.datasets.synthetic import SYNTHETIC_DIR
from investigator.db import create_pool
from investigator.embedding import get_embedder
from investigator.evaluation.datasets import EVAL_DIR, SEED, hotpotqa_questions
from investigator.evaluation.metrics import paired_bootstrap
from investigator.evaluation.report import markdown_table
from investigator.llm.client import GEMINI_URL, GROQ_URL, LLMClient
from investigator.llm.errors import LLMError
from investigator.llm.provider import OpenAICompatibleProvider
from investigator.llm.types import Message

QUESTIONS_FILE = EVAL_DIR / "answer_questions.json"
ANSWERS_DIR = EVAL_DIR / "answers"
CONFIGS = {
    "agent-qwen": {"model": "groq:qwen/qwen3.8-27b", "single_shot": False},
    "agent-flash-lite": {"model": "gemini:gemini-3.5-flash-lite", "single_shot": False},
    "single-flash-lite": {"model": "gemini:gemini-3.5-flash-lite", "single_shot": True},
    "agent-120b": {"model": "groq:openai/gpt-oss-120b", "single_shot": False},
    "single-120b": {"model": "groq:openai/gpt-oss-120b", "single_shot": True},
    "agent-20b": {"model": "groq:openai/gpt-oss-20b", "single_shot": False},
}
DEFAULT_JUDGE = "groq:openai/gpt-oss-20b"
# (from, to) pairs for the paired bootstrap: baseline -> agent on one model, and model -> model
COMPARISONS = [
    ("single-flash-lite", "agent-flash-lite"),
    ("agent-qwen", "agent-flash-lite"),
    ("single-120b", "agent-120b"),
    ("agent-20b", "agent-120b"),
]
VERDICT_SCORE = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0, "failed": 0.0}

JUDGE_SYSTEM = """\
You grade an answer from a document-investigation system against a reference answer.
Return JSON: {"verdict": "correct" | "partial" | "incorrect", "reason": "<one sentence>"}
- correct: the answer conveys the reference's key facts and contradicts none of them. Extra \
correct detail and different wording are fine.
- partial: some key facts are right; others are missing or wrong.
- incorrect: the key facts are missing or wrong, or it answers a different question.
If the reference says the information is not documented, "correct" means the answer says it \
can't be determined from the documents and invents nothing.
If the reference says the sources disagree, "correct" requires the answer or its reported \
contradictions to present the conflicting values."""


@dataclass
class AnswerQuestion:
    id: str
    dataset: str  # synthetic | hotpotqa
    category: str  # synthetic category, or bridge / comparison
    question: str
    reference: str
    gold_sources: list[str]
    expect_contradiction: bool = False
    expect_abstain: bool = False


def answer_questions() -> list[AnswerQuestion]:
    """The 42 questions; the HotpotQA selection is drawn once and frozen in a file."""
    synthetic = json.loads((SYNTHETIC_DIR / "questions.json").read_text(encoding="utf-8"))
    questions = [
        AnswerQuestion(
            id=q["id"],
            dataset="synthetic",
            category=q["category"],
            question=q["question"],
            reference=q["expected_answer"],
            gold_sources=[f"upload:{name}" for name in q["expected_sources"]],
            expect_contradiction=q["expect_contradiction"],
            expect_abstain=q["expect_abstain"],
        )  # fmt: skip
        for q in synthetic
    ]
    test_split = {q.id: q for q in hotpotqa_questions() if q.split == "test"}
    if QUESTIONS_FILE.exists():
        chosen = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))["hotpotqa"]
    else:
        rng = random.Random(SEED)
        chosen = []
        for kind, count in (("bridge", 16), ("comparison", 6)):
            pool = sorted(q.id for q in test_split.values() if q.group == kind)
            chosen += rng.sample(pool, count)
        QUESTIONS_FILE.write_text(json.dumps({"hotpotqa": chosen}, indent=1), encoding="utf-8")
    for question_id in chosen:
        q = test_split[question_id]
        questions.append(
            AnswerQuestion(
                id=q.id,
                dataset="hotpotqa",
                category=q.group,
                question=q.question,
                reference=q.answer,
                gold_sources=sorted(q.gold),
            )  # fmt: skip
        )
    return questions


def batch_provider(spec: str, timeout: float = 90.0) -> OpenAICompatibleProvider:
    """A patient provider for "groq:<model>" or "gemini:<model>": in a batch run, waiting out
    a rate limit beats switching model.

    More retries than interactive use, too: malformed generations ('output_parse_failed',
    phantom tools like 'json') are random, and the smoke test lost a whole question to three
    in a row.
    """
    provider, _, model = spec.partition(":")
    settings = get_settings()
    url, key = {
        "groq": (GROQ_URL, settings.groq_api_key),
        "gemini": (GEMINI_URL, settings.gemini_api_key),
    }[provider]
    # Groq's daily budget refills continuously (~8k tokens/hour), so an overnight run with
    # LLM_MAX_WAIT_SECONDS=1800 simply waits for each refill instead of failing.
    max_wait = max(90.0, settings.llm_max_wait_seconds)
    return OpenAICompatibleProvider(
        spec, url, key.get_secret_value(), model,
        timeout=timeout, max_retries=4, max_wait=max_wait, max_rate_limit_waits=12,
    )  # fmt: skip


def read_jsonl(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def append_jsonl(path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def drop_failed(name: str) -> int:
    """Forget a configuration's failed runs and their judgments, so they are answered again.
    Only runs that never finished: a wrong answer is a result and stays."""
    answers_path = ANSWERS_DIR / f"{name}.jsonl"
    answers = read_jsonl(answers_path)
    failed = {r["id"] for r in answers if r["status"] == "failed"}
    for path, rows in [(answers_path, answers),
                       (ANSWERS_DIR / f"{name}.judgments.jsonl",
                        read_jsonl(ANSWERS_DIR / f"{name}.judgments.jsonl"))]:  # fmt: skip
        kept = [r for r in rows if r["id"] not in failed]
        if path.exists():
            path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept),
                            encoding="utf-8")  # fmt: skip
    return len(failed)


async def answer_all(names: list[str], questions: list[AnswerQuestion]) -> None:
    pool = await create_pool(get_settings())
    retriever = DatabaseRetriever(pool, get_embedder())
    by_model = defaultdict(list)
    for name in names:
        by_model[CONFIGS[name]["model"]].append(name)

    async def run_model(model: str, configs: list[str]) -> None:
        client = LLMClient([batch_provider(model)])
        try:
            for name in configs:  # same model: one after another, they share its budget
                agent = Investigator(client, retriever, single_shot=CONFIGS[name]["single_shot"])
                path = ANSWERS_DIR / f"{name}.jsonl"
                done = {r["id"] for r in read_jsonl(path)}
                for i, q in enumerate(questions, start=1):
                    if q.id in done:
                        continue
                    result = await agent.run(q.question)
                    append_jsonl(path, {"id": q.id, "config": name, **asdict(result)})
                    seconds = result.duration_ms / 1000
                    print(
                        f"[{name}] {i}/{len(questions)} {q.id} {result.status} "
                        f"{result.llm_calls} calls {seconds:.0f}s",
                        flush=True,
                    )
        finally:
            await client.aclose()

    try:
        await asyncio.gather(*(run_model(m, c) for m, c in by_model.items()))
    finally:
        await pool.close()


async def judge_all(names: list[str], questions: list[AnswerQuestion], judge_model: str) -> None:
    graded_models = {CONFIGS[name]["model"] for name in names}
    if judge_model in graded_models:
        raise ValueError(f"{judge_model} would grade its own answers; pick another --judge")
    by_id = {q.id: q for q in questions}
    judge = LLMClient([batch_provider(judge_model)])
    try:
        for name in names:
            path = ANSWERS_DIR / f"{name}.judgments.jsonl"
            # "unjudged" (the judge failed, e.g. its daily quota ran out) is retried; the new
            # verdict is appended, and the report reads the last verdict per question.
            done = {r["id"] for r in read_jsonl(path) if r["verdict"] != "unjudged"}
            for answer in read_jsonl(ANSWERS_DIR / f"{name}.jsonl"):
                if answer["id"] in done or answer["id"] not in by_id:
                    continue
                verdict = await judge_one(judge, by_id[answer["id"]], answer)
                append_jsonl(path, {"id": answer["id"], **verdict})
                print(f"[judge {name}] {answer['id']} {verdict['verdict']}", flush=True)
    finally:
        await judge.aclose()


async def judge_one(judge: LLMClient, q: AnswerQuestion, answer: dict) -> dict:
    if answer["status"] == "failed":
        return {"verdict": "failed", "reason": "the investigation did not complete"}
    contradictions = "; ".join(
        f"{c['topic']}: " + " vs ".join(p["claim"] for p in c["positions"])
        for c in answer["contradictions"]
    )
    prompt = (
        f"Question: {q.question}\nReference answer: {q.reference}\n\n"
        f"Answer (status: {answer['status']}):\n{answer['answer']}\n\n"
        f"Reported contradictions: {contradictions or 'none'}"
    )
    try:
        response = await judge.complete(
            [Message("system", JUDGE_SYSTEM), Message("user", prompt)], json_mode=True
        )
        parsed = json.loads(response.content or "{}")
        if parsed.get("verdict") not in ("correct", "partial", "incorrect"):
            raise ValueError(f"unexpected verdict {parsed.get('verdict')!r}")
        return {"verdict": parsed["verdict"], "reason": parsed.get("reason", "")}
    except (LLMError, ValueError) as exc:
        return {"verdict": "unjudged", "reason": str(exc)[:200]}


# ---------------------------------------------------------------- metrics and report


def score_config(name: str, questions: list[AnswerQuestion]) -> tuple[dict, list[dict]]:
    by_id = {q.id: q for q in questions}
    answers = {r["id"]: r for r in read_jsonl(ANSWERS_DIR / f"{name}.jsonl")}
    verdicts = {r["id"]: r for r in read_jsonl(ANSWERS_DIR / f"{name}.judgments.jsonl")}
    rows = []
    for question_id, answer in answers.items():
        q = by_id.get(question_id)
        verdict = verdicts.get(question_id, {}).get("verdict", "unjudged")
        if q is None or verdict == "unjudged":
            continue
        gold = set(q.gold_sources)
        cited = {c["source_id"] for c in answer["citations"]}
        seen = set(answer["evidence_sources"])
        claims = answer["verification"]
        rows.append({
            "id": question_id, "dataset": q.dataset, "category": q.category,
            "verdict": verdict, "score": VERDICT_SCORE[verdict],
            "reason": verdicts[question_id].get("reason", ""), "status": answer["status"],
            "citation_precision": len(cited & gold) / len(cited) if gold and cited else None,
            "citation_recall": len(cited & gold) / len(gold) if gold else None,
            "evidence_recall": len(seen & gold) / len(gold) if gold else None,
            "supported_claims": sum(c["verdict"] == "supported" for c in claims),
            "verified_claims": len(claims),
            "contradiction_found": bool(answer["contradictions"]),
            "expect_contradiction": q.expect_contradiction, "expect_abstain": q.expect_abstain,
            "llm_calls": answer["llm_calls"],
            "tokens": answer["prompt_tokens"] + answer["completion_tokens"],
            "seconds": answer["duration_ms"] / 1000, "searches": len(answer["searches"]),
        })  # fmt: skip

    def avg(key, subset=None):
        values = [r[key] for r in (subset or rows) if r[key] is not None]
        return round(mean(values), 3) if values else None

    abstain = [r for r in rows if r["expect_abstain"]]
    contradict = [r for r in rows if r["expect_contradiction"]]
    verified = sum(r["verified_claims"] for r in rows)
    summary = {
        "questions": len(rows),
        "score": avg("score"),
        "correct": sum(r["verdict"] == "correct" for r in rows),
        "partial": sum(r["verdict"] == "partial" for r in rows),
        "incorrect": sum(r["verdict"] in ("incorrect", "failed") for r in rows),
        "score_by_group": {
            group: avg("score", [r for r in rows if f"{r['dataset']}/{r['category']}" == group])
            for group in sorted({f"{r['dataset']}/{r['category']}" for r in rows})
        },
        "citation_precision": avg("citation_precision"),
        "citation_recall": avg("citation_recall"),
        "evidence_recall": avg("evidence_recall"),
        "claim_support_rate": (
            round(sum(r["supported_claims"] for r in rows) / verified, 3) if verified else None
        ),
        "abstention_correct": f"{sum(r['verdict'] == 'correct' for r in abstain)}/{len(abstain)}",
        "contradictions_detected": f"{sum(r['contradiction_found'] for r in contradict)}"
        f"/{len(contradict)}",
        "injection_S14": next((r["verdict"] for r in rows if r["id"] == "S14"), None),
        "failed_runs": sum(r["status"] == "failed" for r in rows),
        "llm_calls": avg("llm_calls"),
        "tokens": avg("tokens"),
        "seconds": avg("seconds"),
    }
    return summary, rows


def compare(name_a: str, rows_a: list[dict], name_b: str, rows_b: list[dict]) -> dict | None:
    """Paired comparison of answer scores on the questions both configurations completed.

    A failed run (the model was unavailable, e.g. its daily quota was spent) says nothing about
    answer quality, so it is left out on both sides instead of being scored as wrong.
    """
    left = {r["id"]: r["score"] for r in rows_a if r["status"] != "failed"}
    right = {r["id"]: r["score"] for r in rows_b if r["status"] != "failed"}
    shared = sorted(left.keys() & right.keys())
    if not shared:
        return None
    diff, low, high = paired_bootstrap([left[i] for i in shared], [right[i] for i in shared])
    return {
        "from": name_a,
        "to": name_b,
        "questions": len(shared),
        "from_score": round(mean(left[i] for i in shared), 3),
        "to_score": round(mean(right[i] for i in shared), 3),
        "difference": round(diff, 3),
        "ci95": [round(low, 3), round(high, 3)],
    }


def build_report(names: list[str], questions: list[AnswerQuestion]) -> dict:
    scored = {name: score_config(name, questions) for name in names}
    results = {"configs": {name: summary for name, (summary, _) in scored.items()}}

    comparisons = []
    for a, b in COMPARISONS:
        if a in scored and b in scored:
            comparison = compare(a, scored[a][1], b, scored[b][1])
            if comparison:
                comparisons.append(comparison)
    results["comparisons"] = comparisons
    results["not_correct"] = {
        name: [r for r in rows if r["verdict"] != "correct"] for name, (_, rows) in scored.items()
    }
    (EVAL_DIR / "answer_results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    (EVAL_DIR / "answer_report.md").write_text(render(results), encoding="utf-8")
    return results


def render(results: dict) -> str:
    configs = results["configs"]
    names = list(configs)
    metrics = [
        ("questions judged", "questions"),
        ("answer score (correct=1, partial=0.5)", "score"),
        ("correct / partial / incorrect", None),
        ("citation precision", "citation_precision"),
        ("citation recall", "citation_recall"),
        ("evidence recall (gold docs seen)", "evidence_recall"),
        ("claims supported by their citations", "claim_support_rate"),
        ("abstention correct (unanswerable)", "abstention_correct"),
        ("contradictions detected", "contradictions_detected"),
        ("injection (S14)", "injection_S14"),
        ("failed runs", "failed_runs"),
        ("LLM calls / question", "llm_calls"),
        ("tokens / question", "tokens"),
        ("seconds / question", "seconds"),
    ]
    rows = []
    for label, key in metrics:
        if key is None:
            rows.append(
                [
                    label,
                    *(
                        f"{c['correct']} / {c['partial']} / {c['incorrect']}"
                        for c in configs.values()
                    ),
                ]
            )
        else:
            rows.append([label, *(configs[n][key] for n in names)])
    groups = sorted({g for c in configs.values() for g in c["score_by_group"]})
    lines = ["# Answer evaluation", "", "## Overall", ""]
    lines += markdown_table(["Metric", *names], rows)
    lines += ["", "## Answer score by question group", ""]
    lines += markdown_table(
        ["Group", *names],
        [[g, *(configs[n]["score_by_group"].get(g) for n in names)] for g in groups],
    )
    lines += ["", "## Paired bootstrap on the answer score, 95% CI", ""]
    lines += ["Only questions both configurations completed (failed runs are left out).", ""]
    lines += markdown_table(
        ["From", "To", "Questions", "From score", "To score", "Difference", "95% CI"],
        [[c["from"], c["to"], c["questions"], c["from_score"], c["to_score"],
          f"{c['difference']:+.3f}", f"[{c['ci95'][0]:+.3f}, {c['ci95'][1]:+.3f}]"]
         for c in results["comparisons"]],
    )  # fmt: skip
    for name, rows_ in results["not_correct"].items():
        lines += ["", f"## Not fully correct: {name}", ""]
        lines += markdown_table(
            ["Id", "Group", "Verdict", "Status", "Gold docs seen", "Judge's reason"],
            [[r["id"], f"{r['dataset']}/{r['category']}", r["verdict"], r["status"],
              r["evidence_recall"], r["reason"]] for r in rows_],
        )  # fmt: skip
    return "\n".join(str(line) for line in lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="End-to-end answer evaluation.")
    parser.add_argument("--configs", default="agent-qwen", help=f"comma-separated: {list(CONFIGS)}")
    parser.add_argument("--judge", default=DEFAULT_JUDGE, help="model id of the grading model")
    parser.add_argument("--limit", type=int, help="only the first N questions (smoke test)")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="run again the questions whose investigation failed (e.g. quota)",
    )
    parser.add_argument("--answers-only", action="store_true", help="answer now, judge later")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()  # fmt: skip
    names = args.configs.split(",")
    questions = answer_questions()[: args.limit] if args.limit else answer_questions()
    started = time.perf_counter()
    if args.retry_failed:
        for name in names:
            print(f"[{name}] dropped {drop_failed(name)} failed runs to answer again")
    if not args.report_only:
        asyncio.run(answer_all(names, questions))
        if args.answers_only:
            return
        asyncio.run(judge_all(names, questions, args.judge))
    results = build_report(names, questions)
    sys.stdout.reconfigure(errors="replace")  # answers contain characters a Windows console lacks
    print(render(results))
    print(f"total {time.perf_counter() - started:.0f}s")


if __name__ == "__main__":
    main()
