"""HotpotQA (distractor setting, validation split) -> document corpus + evaluation questions.

Each HotpotQA question ships with 10 Wikipedia paragraphs: the 2 that hold the answer ("gold")
and 8 distractors chosen to look relevant. We pool the paragraphs of all validation questions
into one corpus and deduplicate them, so every question must be answered by searching the whole
pool instead of its own 10 paragraphs. That's a much harder, more realistic retrieval task.

Usage: python -m investigator.datasets.hotpotqa
"""

import json
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from investigator.config import DATA_DIR
from investigator.datasets.download import download
from investigator.ingestion.models import Block, LoadedDocument
from investigator.text import normalize_text

logger = logging.getLogger(__name__)

REPO_ID = "hotpotqa/hotpot_qa"
# Pinned dataset commit and file checksum: the input can never change underneath us.
REVISION = "1908d6afbbead072334abe2965f91bd2709910ab"
FILE = "distractor/validation-00000-of-00001.parquet"
SHA256 = "c20b638ca82b21d04fe12e14ff417ad05153d4d215a65de54497fca4e972f7c6"
URL = f"https://huggingface.co/datasets/{REPO_ID}/resolve/{REVISION}/{FILE}"

RAW_PATH = DATA_DIR / "raw" / "hotpotqa" / "validation.parquet"
OUT_DIR = DATA_DIR / "processed" / "hotpotqa"


@dataclass
class Paragraph:
    title: str
    sentences: list[str]

    @property
    def text(self) -> str:
        # HotpotQA keeps the leading space on every sentence after the first,
        # so plain concatenation restores the original paragraph.
        return "".join(self.sentences).strip()


def to_document(record: dict) -> LoadedDocument:
    """A corpus.jsonl record as a loaded document: one paragraph block.

    The sentence boundaries are kept in the metadata, because HotpotQA's gold labels point at
    sentences and the evaluation needs to know where each sentence sits in the text.
    """
    paragraph = Paragraph(record["title"], record["sentences"])
    text = paragraph.text
    lead = len("".join(paragraph.sentences)) - len("".join(paragraph.sentences).lstrip())
    spans, position = [], -lead  # positions in `text`, which has its leading space stripped
    for sentence in paragraph.sentences:
        start = position + len(sentence) - len(sentence.lstrip())  # skip the leading space
        position += len(sentence)
        spans.append([max(start, 0), min(position, len(text))])
    return LoadedDocument(
        title=paragraph.title,
        file_format="txt",
        blocks=[Block("paragraph", text)],
        metadata={"sentence_spans": spans},
    )


def read_corpus(path: Path = OUT_DIR / "corpus.jsonl") -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def load_examples(path: Path) -> list[dict]:
    import pyarrow.parquet as pq  # only needed for data preparation, not by the app

    return pq.read_table(path).to_pylist()


def build_corpus(examples: list[dict]) -> tuple[list[Paragraph], dict, set[str]]:
    """Pool every question's context paragraphs, keyed by Wikipedia title.

    A title is a Wikipedia article, so the same title seen under two questions is the same
    paragraph: we keep one copy (the first). Copies are compared after normalisation, because
    some differ only in whitespace characters (e.g. a non-breaking space). If the text still
    differs, the title is returned in the conflict set.
    """
    by_title: dict[str, Paragraph] = {}
    conflict_titles: set[str] = set()
    raw = empty = exact_duplicates = normalized_duplicates = 0

    for example in examples:
        context = example["context"]
        for title, sentences in zip(context["title"], context["sentences"], strict=True):
            raw += 1
            paragraph = Paragraph(title, list(sentences))
            if not paragraph.text:
                empty += 1
                continue
            kept = by_title.get(title)
            if kept is None:
                by_title[title] = paragraph
            elif kept.text == paragraph.text:
                exact_duplicates += 1
            elif normalize_text(kept.text) == normalize_text(paragraph.text):
                normalized_duplicates += 1
            else:
                conflict_titles.add(title)

    stats = {
        "questions": len(examples),
        "raw_paragraphs": raw,
        "empty_paragraphs": empty,
        "exact_duplicates": exact_duplicates,
        "duplicates_after_normalization": normalized_duplicates,
        "conflicting_titles": len(conflict_titles),
        "unique_documents": len(by_title),
    }
    return list(by_title.values()), stats, conflict_titles


def build_questions(examples: list[dict], conflict_titles: set[str]) -> list[dict]:
    """Evaluation records: the question, its answer, and the gold paragraphs/sentences."""
    questions = []
    for example in examples:
        facts = example["supporting_facts"]
        supporting_facts = [
            {"title": title, "sent_id": int(sent_id)}
            for title, sent_id in zip(facts["title"], facts["sent_id"], strict=True)
        ]
        supporting_titles = list(dict.fromkeys(f["title"] for f in supporting_facts))
        questions.append(
            {
                "id": example["id"],
                "question": example["question"],
                "answer": example["answer"],
                "type": example["type"],  # 'bridge' or 'comparison'
                "level": example["level"],
                "supporting_titles": supporting_titles,
                "supporting_facts": supporting_facts,
                # False when a gold paragraph's text differs from the copy we kept,
                # so sentence-level labels might not line up. Excluded from evaluation.
                "labels_reliable": not (set(supporting_titles) & conflict_titles),
            }
        )
    return questions


def write_jsonl(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    examples = load_examples(download(URL, RAW_PATH, SHA256))

    corpus, stats, conflict_titles = build_corpus(examples)
    questions = build_questions(examples, conflict_titles)

    write_jsonl(
        OUT_DIR / "corpus.jsonl",
        (
            {"source_id": f"hotpotqa:{p.title}", "title": p.title, "sentences": p.sentences}
            for p in corpus
        ),
    )
    write_jsonl(OUT_DIR / "questions.jsonl", questions)

    lengths = sorted(len(p.text) for p in corpus)
    stats |= {
        "question_types": dict(Counter(q["type"] for q in questions)),
        "questions_with_reliable_labels": sum(q["labels_reliable"] for q in questions),
        "chars_per_document": {
            "median": lengths[len(lengths) // 2],
            "p95": lengths[int(len(lengths) * 0.95)],
            "max": lengths[-1],
        },
        "source": {"repo": REPO_ID, "revision": REVISION, "file": FILE, "sha256": SHA256},
    }
    (OUT_DIR / "stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
