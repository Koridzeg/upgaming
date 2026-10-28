"""Retrieval evaluation: which retriever, which chunking strategy, which ranking settings.

1. Retrieve once per question and strategy (dense + BM25 candidates, the database part).
2. Tune: try every setting in GRID on the tune split - only fusion and re-ranking change, so
   each setting is replayed in memory over the same candidates.
3. Test: score baselines and the tuned setting, for both chunking strategies, on the test
   split; paired-bootstrap confidence intervals for the key comparisons.
4. Failure analysis of the tuned setting, and the synthetic questions.

Writes eval/retrieval_results.json and eval/retrieval_report.md.

Usage: python -m investigator.evaluation.retrieval
"""

import asyncio
import json
import time
from dataclasses import asdict, dataclass, replace
from statistics import mean

import asyncpg

from investigator.config import get_settings
from investigator.db import init_connection
from investigator.embedding import get_embedder
from investigator.evaluation.datasets import (
    EVAL_DIR,
    EvalQuestion,
    hotpotqa_questions,
    synthetic_questions,
)
from investigator.evaluation.metrics import (
    all_at_k,
    ndcg_at_k,
    paired_bootstrap,
    recall_at_k,
    reciprocal_rank,
    sentence_recall_at_k,
)
from investigator.evaluation.report import retrieval_report
from investigator.retrieval.ranking import title_match
from investigator.retrieval.search import (
    Candidates,
    SearchConfig,
    SearchHit,
    fetch_hits,
    rank,
    retrieve,
)

K_VALUES = (5, 10, 20)
STRATEGIES = ("structural", "fixed")
PRIMARY = "all@10"  # tuning target: every gold document within the 10 chunks the LLM will see

BASELINES = {
    "dense only": SearchConfig(mode="dense", rerank=False),
    "BM25 only": SearchConfig(mode="keyword", rerank=False),
    "hybrid RRF": SearchConfig(mode="hybrid", rerank=False),
    # The first guesses from Step 6, kept explicit so this row means the same after defaults change.
    "hybrid + re-rank (initial guess)": SearchConfig(title_boost=0.3, mmr_lambda=0.8),
}
GRID = [SearchConfig(keyword_weight=w, rerank=False) for w in (0.5, 1.0, 1.5, 2.0)] + [
    SearchConfig(keyword_weight=w, title_boost=boost, mmr_lambda=lam)
    for w in (0.5, 1.0, 1.5, 2.0)
    for boost in (0.0, 0.3, 0.6)
    for lam in (1.0, 0.8, 0.6)
]


@dataclass
class Prepared:
    question: EvalQuestion
    candidates: Candidates
    hits: dict[int, SearchHit]


async def prepare(conn, embedder, questions: list[EvalQuestion], strategy: str) -> list[Prepared]:
    config = SearchConfig(mode="hybrid", strategy=strategy)
    prepared = []
    for question in questions:
        candidates = await retrieve(conn, embedder, question.question, config)
        prepared.append(
            Prepared(question, candidates, await fetch_hits(conn, candidates.chunk_ids()))
        )
    return prepared


def score(prepared: Prepared, config: SearchConfig, spans: dict) -> dict:
    hits = rank(
        prepared.candidates,
        prepared.hits,
        prepared.question.question,
        replace(config, k=max(K_VALUES)),
    )
    documents = [h.source_id for h in hits]
    gold = prepared.question.gold
    chunk_spans = [
        (h.source_id, h.chunk_metadata["char_start"], h.chunk_metadata["char_end"]) for h in hits
    ]
    sentences = [
        (doc, *spans[doc][i]) for doc, i in prepared.question.gold_sentences
        if doc in spans and i < len(spans[doc])
    ]  # fmt: skip
    row = {"mrr": reciprocal_rank(documents, gold), "ndcg@10": ndcg_at_k(documents, gold, 10)}
    for k in K_VALUES:
        row[f"recall@{k}"] = recall_at_k(documents, gold, k)
        row[f"all@{k}"] = all_at_k(documents, gold, k)
        if sentences:
            row[f"sentence_recall@{k}"] = sentence_recall_at_k(chunk_spans, sentences, k)
    row["tokens@10"] = sum(h.token_count for h in hits[:10])
    return row


def summarize(rows: list[dict]) -> dict:
    return {key: round(mean(r[key] for r in rows if key in r), 4) for key in rows[0]}


def evaluate(
    prepared: list[Prepared], config: SearchConfig, spans: dict
) -> tuple[dict, list[dict]]:
    rows = [score(p, config, spans) for p in prepared]
    return summarize(rows), rows


def describe(config: SearchConfig) -> str:
    if not config.rerank:
        return f"{config.mode}, keyword weight {config.keyword_weight}, no re-rank"
    return (
        f"{config.mode}, keyword weight {config.keyword_weight}, "
        f"title boost {config.title_boost}, MMR lambda {config.mmr_lambda}"
    )


def failure_analysis(prepared: list[Prepared], config: SearchConfig) -> dict:
    """Why gold documents were missed: never retrieved, or retrieved but ranked below 10."""
    missed, by_group = [], {}
    for p in prepared:
        hits = rank(p.candidates, p.hits, p.question.question, replace(config, k=10))
        top10 = {h.source_id for h in hits}
        candidates = {p.hits[i].source_id for i in p.candidates.chunk_ids() if i in p.hits}
        by_group.setdefault(p.question.group, []).append(1.0 if p.question.gold <= top10 else 0.0)
        for doc in p.question.gold - top10:
            title = doc.split(":", 1)[1]
            missed.append(
                {
                    "question": p.question.question,
                    "group": p.question.group,
                    "missed": title,
                    "named_in_question": title_match(title, p.question.question) == 1.0,
                    "in_candidates": doc in candidates,
                }
            )
    total = len(missed) or 1
    return {
        "all@10_by_group": {g: round(mean(v), 3) for g, v in by_group.items()},
        "group_sizes": {g: len(v) for g, v in by_group.items()},
        "missed_gold_documents": len(missed),
        "share_never_retrieved": round(sum(not m["in_candidates"] for m in missed) / total, 3),
        "share_named_in_question": round(sum(m["named_in_question"] for m in missed) / total, 3),
        "examples": missed[:8],
    }


async def load_sentence_spans(conn, documents: set[str]) -> dict[str, list[list[int]]]:
    rows = await conn.fetch(
        "SELECT source_id, metadata->'sentence_spans' AS spans"
        " FROM documents WHERE source_id = ANY($1)",
        list(documents),
    )
    return {r["source_id"]: r["spans"] for r in rows if r["spans"]}


async def run() -> dict:
    conn = await asyncpg.connect(get_settings().database_url.get_secret_value())
    await init_connection(conn)
    embedder = get_embedder()
    try:
        hotpot = hotpotqa_questions()
        synthetic = synthetic_questions()
        spans = await load_sentence_spans(conn, {d for q in hotpot for d in q.gold})
        started = time.perf_counter()
        prepared = {s: await prepare(conn, embedder, hotpot + synthetic, s) for s in STRATEGIES}
        elapsed = time.perf_counter() - started
        count = len(hotpot) + len(synthetic)
        print(f"candidates for {count} questions x 2 strategies: {elapsed:.0f}s")
    finally:
        await conn.close()

    def split(strategy: str, name: str) -> list[Prepared]:
        return [
            p
            for p in prepared[strategy]
            if p.question.dataset == "hotpotqa" and p.question.split == name
        ]

    synthetic_prepared = [p for p in prepared["structural"] if p.question.dataset == "synthetic"]

    # 1. Tune on the tune split, structural chunks.
    tuning = []
    for config in GRID:
        summary, _ = evaluate(split("structural", "tune"), config, spans)
        tuning.append({"config": describe(config), "settings": asdict(config), **summary})
    best_index = max(range(len(GRID)), key=lambda i: (tuning[i][PRIMARY], tuning[i]["mrr"]))
    tuned = GRID[best_index]
    print(f"best on tune split: {describe(tuned)} ({PRIMARY} {tuning[best_index][PRIMARY]})")

    # 2. Test split: baselines + tuned, both strategies.
    configs = {**BASELINES, "hybrid tuned": tuned}
    test, rows = {}, {}
    for strategy in STRATEGIES:
        for name, config in configs.items():
            config = replace(config, strategy=strategy)
            summary, per_question = evaluate(split(strategy, "test"), config, spans)
            test[f"{name} | {strategy}"] = summary
            rows[(name, strategy)] = per_question

    def compare(a: tuple, b: tuple, metric: str) -> dict:
        diff, low, high = paired_bootstrap(
            [r[metric] for r in rows[a]], [r[metric] for r in rows[b]]
        )
        return {"from": " | ".join(a), "to": " | ".join(b), "metric": metric,
                "difference": round(diff, 4), "ci95": [round(low, 4), round(high, 4)]}  # fmt: skip

    comparisons = [
        compare(("dense only", "structural"), ("hybrid tuned", "structural"), PRIMARY),
        compare(("BM25 only", "structural"), ("hybrid tuned", "structural"), PRIMARY),
        compare(("hybrid RRF", "structural"), ("hybrid tuned", "structural"), PRIMARY),
        compare(("hybrid tuned", "fixed"), ("hybrid tuned", "structural"), PRIMARY),
        compare(("hybrid tuned", "fixed"), ("hybrid tuned", "structural"), "sentence_recall@10"),
        compare(("hybrid tuned", "fixed"), ("hybrid tuned", "structural"), "tokens@10"),
    ]

    # 3. Failures of the tuned setting, and the synthetic questions.
    failures = failure_analysis(split("structural", "test"), tuned)
    synthetic_results = {}
    for name, config in (
        ("hybrid RRF", BASELINES["hybrid RRF"]),
        ("hybrid tuned", tuned),
    ):
        summary, per_question = evaluate(synthetic_prepared, config, spans)
        recall_by_question = {
            p.question.id: r["recall@10"]
            for p, r in zip(synthetic_prepared, per_question, strict=True)
        }
        synthetic_results[name] = {**summary, "recall@10_by_question": recall_by_question}

    results = {
        "sample": {
            "tune": len(split("structural", "tune")),
            "test": len(split("structural", "test")),
            "synthetic": len(synthetic_prepared),
        },
        "tuned": {"config": describe(tuned), "settings": asdict(tuned)},
        "tuning": sorted(tuning, key=lambda t: (-t[PRIMARY], -t["mrr"])),
        "test": test,
        "comparisons": comparisons,
        "failures": failures,
        "synthetic": synthetic_results,
    }
    EVAL_DIR.mkdir(exist_ok=True)
    (EVAL_DIR / "retrieval_results.json").write_text(
        json.dumps(results, indent=1), encoding="utf-8"
    )
    (EVAL_DIR / "retrieval_report.md").write_text(
        retrieval_report(results, PRIMARY), encoding="utf-8"
    )
    return results


def main() -> None:
    results = asyncio.run(run())
    print(retrieval_report(results, PRIMARY))


if __name__ == "__main__":
    main()
