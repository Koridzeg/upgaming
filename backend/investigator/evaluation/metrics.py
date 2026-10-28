"""Retrieval metrics over a ranked list of retrieved chunks.

`documents` is the source_id of each retrieved chunk, in rank order (a document can appear
more than once, when several of its chunks were retrieved). `gold` is the set of documents a
question needs. K counts chunks: what the LLM actually receives is K chunks.

- recall@K       share of gold documents present in the top K chunks
- all@K          1 if every gold document is in the top K, else 0. For a multi-hop question
                 this is what matters: with one of two facts, the answer is still a guess.
- MRR            1 / rank of the first chunk from a gold document (0 if none in the list)
- nDCG@K         rank-aware recall: a gold document found at rank 1 counts more than at rank 9
- sentence recall@K  share of gold *sentences* lying entirely inside a top-K chunk. Document-level
                 metrics can't tell chunking strategies apart; this one can, because a fact
                 split across a chunk boundary isn't fully in any chunk.
"""

import math
import random
from collections.abc import Sequence


def recall_at_k(documents: Sequence[str], gold: set[str], k: int) -> float:
    return len(gold & set(documents[:k])) / len(gold)


def all_at_k(documents: Sequence[str], gold: set[str], k: int) -> float:
    return 1.0 if gold <= set(documents[:k]) else 0.0


def reciprocal_rank(documents: Sequence[str], gold: set[str]) -> float:
    return next((1 / rank for rank, doc in enumerate(documents, start=1) if doc in gold), 0.0)


def ndcg_at_k(documents: Sequence[str], gold: set[str], k: int) -> float:
    seen: set[str] = set()
    dcg = 0.0
    for rank, doc in enumerate(documents[:k], start=1):
        if doc in gold and doc not in seen:  # each gold document is credited once
            seen.add(doc)
            dcg += 1 / math.log2(rank + 1)
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(len(gold), k) + 1))
    return dcg / ideal


def sentence_recall_at_k(
    chunks: Sequence[tuple[str, int, int]],  # (source_id, char_start, char_end), rank order
    sentences: Sequence[tuple[str, int, int]],  # gold sentences, same coordinates
    k: int,
) -> float:
    if not sentences:
        return 0.0
    covered = sum(
        any(doc == s_doc and start <= s_start and s_end <= end for doc, start, end in chunks[:k])
        for s_doc, s_start, s_end in sentences
    )
    return covered / len(sentences)


def paired_bootstrap(
    a: Sequence[float], b: Sequence[float], iterations: int = 2000, seed: int = 0
) -> tuple[float, float, float]:
    """Mean of (b - a) per question, with a 95% confidence interval.

    Resample the questions with replacement many times and look at how much the mean
    difference moves. "Paired": both configurations are scored on the same resampled
    questions, so question difficulty cancels out. If the interval excludes 0, the
    difference is unlikely to be luck of the sample.
    """
    differences = [y - x for x, y in zip(a, b, strict=True)]
    n = len(differences)
    rng = random.Random(seed)
    means = sorted(
        sum(differences[rng.randrange(n)] for _ in range(n)) / n for _ in range(iterations)
    )
    return sum(differences) / n, means[int(0.025 * iterations)], means[int(0.975 * iterations)]
