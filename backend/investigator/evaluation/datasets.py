"""Evaluation question sets.

HotpotQA: a seeded sample of validation questions with reliable gold labels, stratified by
question type and split in two halves:
  tune - settings (fusion weights, re-ranker) are chosen here;
  test - every reported number comes from here, on questions no setting was chosen on.
The sampled ids are written to eval/retrieval_sample.json and reused from then on, so the
sample can't drift if the sampling code changes.

Synthetic: the hand-written questions of data/synthetic/questions.json that have an answer
in the corpus (unanswerable ones have no gold documents to retrieve).
"""

import json
import random
from dataclasses import dataclass, field

from investigator.config import REPO_ROOT
from investigator.datasets.hotpotqa import OUT_DIR as HOTPOT_DIR
from investigator.datasets.synthetic import SYNTHETIC_DIR

EVAL_DIR = REPO_ROOT / "eval"
SAMPLE_FILE = EVAL_DIR / "retrieval_sample.json"
DUPLICATES_FILE = HOTPOT_DIR / "duplicates.json"  # written by the bulk loader
SEED = 2026


@dataclass
class EvalQuestion:
    id: str
    dataset: str  # 'hotpotqa' or 'synthetic'
    question: str
    group: str  # 'bridge' / 'comparison' for HotpotQA, the category for synthetic questions
    split: str  # 'tune' or 'test'
    gold: set[str]  # source_ids of the documents the question needs
    gold_sentences: list[tuple[str, int]] = field(default_factory=list)  # (source_id, index)


def hotpotqa_questions(per_split: int = 150) -> list[EvalQuestion]:
    records = {}
    with (HOTPOT_DIR / "questions.jsonl").open(encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            records[record["id"]] = record

    if SAMPLE_FILE.exists():
        sample = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
    else:
        sample = _draw_sample(list(records.values()), per_split)
        EVAL_DIR.mkdir(exist_ok=True)
        SAMPLE_FILE.write_text(json.dumps(sample, indent=1), encoding="utf-8")

    # Exact-duplicate titles were not stored; their gold label points at the stored copy.
    aliases = (
        json.loads(DUPLICATES_FILE.read_text(encoding="utf-8")) if DUPLICATES_FILE.exists() else {}
    )

    def source(title: str) -> str:
        source_id = f"hotpotqa:{title}"
        return aliases.get(source_id, source_id)

    questions = []
    for split, ids in sample.items():
        for question_id in ids:
            record = records[question_id]
            questions.append(
                EvalQuestion(
                    id=question_id,
                    dataset="hotpotqa",
                    question=record["question"],
                    group=record["type"],
                    split=split,
                    gold={source(t) for t in record["supporting_titles"]},
                    gold_sentences=[
                        (source(f["title"]), f["sent_id"]) for f in record["supporting_facts"]
                    ],
                )
            )
    return questions


def _draw_sample(records: list[dict], per_split: int) -> dict[str, list[str]]:
    """Stratified: each question type keeps its share of the dataset in both splits."""
    rng = random.Random(SEED)
    reliable = sorted((r for r in records if r["labels_reliable"]), key=lambda r: r["id"])
    sample: dict[str, list[str]] = {"tune": [], "test": []}
    for kind in ("bridge", "comparison"):
        pool = [r["id"] for r in reliable if r["type"] == kind]
        take = round(per_split * len(pool) / len(reliable))
        chosen = rng.sample(pool, 2 * take)
        sample["tune"] += chosen[:take]
        sample["test"] += chosen[take:]
    return sample


def synthetic_questions() -> list[EvalQuestion]:
    records = json.loads((SYNTHETIC_DIR / "questions.json").read_text(encoding="utf-8"))
    return [
        EvalQuestion(
            id=r["id"],
            dataset="synthetic",
            question=r["question"],
            group=r["category"],
            split="test",
            gold={f"upload:{name}" for name in r["expected_sources"]},
        )
        for r in records
        if r["expected_sources"]
    ]
