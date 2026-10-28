"""Our ranking algorithms: weighted Reciprocal Rank Fusion, title matching, and MMR.

Pure functions over ids, scores and vectors - no database, no model - so each can be tested
and reasoned about on its own.
"""

import re
from collections import defaultdict
from collections.abc import Hashable

import numpy as np

RRF_K = 60  # the constant from Cormack, Clarke & Buettcher (2009)

_STOPWORDS = frozenset(
    "a an and are as at be by for from in is it of on or the to was were with what which who "
    "when where how did does do".split()
)


def weighted_rrf(
    rankings: dict[str, list[Hashable]], weights: dict[str, float], k: int = RRF_K
) -> dict[Hashable, float]:
    """score(item) = sum over rankings r of  weight_r / (k + rank of item in r).

    Why ranks and not scores: cosine similarities (around 0.6-0.9) and BM25 scores (0 to 30+)
    live on unrelated scales, and normalising them is fragile (one outlier rescales everything).
    Ranks are comparable across any retrievers. k dampens the head: with k = 60, rank 1 is
    worth 1/61 and rank 10 still 1/70, so an item found by both retrievers at modest ranks
    beats an item found by only one at the very top.
    """
    scores: dict[Hashable, float] = defaultdict(float)
    for name, ranked in rankings.items():
        weight = weights.get(name, 1.0)
        for rank, item in enumerate(ranked, start=1):
            scores[item] += weight / (k + rank)
    return dict(scores)


def title_match(title: str, query: str) -> float:
    """1.0 if the query names the document's title, else 0.0.

    A question naming an entity ("Were Scott Derrickson and Ed Wood ...") is very likely about
    the documents with that title. "Naming" means every title word appears in the query:
    partial overlap is not naming. (A share-based score was tried first: "Postmortem
    INC-2026-0311 (DRAFT)" shares 3 of 4 words with any question quoting the incident id, and
    that boost lifted an irrelevant chunk to rank 1.) Disambiguators like "(film)" are ignored,
    so "Ed Wood (film)" is named by "Ed Wood".
    """
    core = re.sub(r"\s*\([^)]*\)", "", title)
    title_words = {w for w in re.findall(r"\w+", core.casefold()) if w not in _STOPWORDS}
    query_words = set(re.findall(r"\w+", query.casefold()))
    return 1.0 if title_words and title_words <= query_words else 0.0


def mmr_select(relevance: np.ndarray, vectors: np.ndarray, k: int, lam: float) -> list[int]:
    """Maximal Marginal Relevance (Carbonell & Goldstein, 1998): pick k items one at a time,
    each maximising   lam * relevance  -  (1 - lam) * (max similarity to anything picked).

    lam = 1 is plain ranking by relevance; lower values trade relevance for variety. For
    multi-hop questions variety matters: three near-identical chunks about the first entity
    would push out the one chunk about the second. Vectors must be unit length (cosine = dot).
    Returns positions into `relevance`, in selection order.
    """
    k = min(k, len(relevance))
    similarity = vectors @ vectors.T
    chosen: list[int] = []
    redundancy = np.full(len(relevance), -np.inf)  # max similarity to the chosen set so far
    for _ in range(k):
        penalty = np.where(np.isfinite(redundancy), redundancy, 0.0)
        score = lam * relevance - (1 - lam) * penalty
        score[chosen] = -np.inf
        best = int(np.argmax(score))
        chosen.append(best)
        redundancy = np.maximum(redundancy, similarity[best])
    return chosen
