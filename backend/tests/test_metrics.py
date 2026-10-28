import pytest

from investigator.evaluation.metrics import (
    all_at_k,
    ndcg_at_k,
    paired_bootstrap,
    recall_at_k,
    reciprocal_rank,
    sentence_recall_at_k,
)

RETRIEVED = ["x", "a", "a", "y", "b"]  # source_id per retrieved chunk, rank order
GOLD = {"a", "b"}


def test_recall_and_all_count_documents_within_the_first_k_chunks():
    assert recall_at_k(RETRIEVED, GOLD, 2) == 0.5
    assert recall_at_k(RETRIEVED, GOLD, 5) == 1.0
    assert all_at_k(RETRIEVED, GOLD, 4) == 0.0
    assert all_at_k(RETRIEVED, GOLD, 5) == 1.0


def test_reciprocal_rank_uses_the_first_gold_chunk():
    assert reciprocal_rank(RETRIEVED, GOLD) == 0.5
    assert reciprocal_rank(["x", "y"], GOLD) == 0.0


def test_ndcg_credits_each_gold_document_once():
    perfect = ndcg_at_k(["a", "b", "x"], GOLD, 3)
    with_repeat = ndcg_at_k(["a", "a", "b"], GOLD, 3)  # the second "a" earns nothing

    assert perfect == pytest.approx(1.0)
    assert with_repeat < perfect


def test_a_sentence_counts_only_if_one_chunk_holds_all_of_it():
    chunks = [("a", 0, 50), ("a", 40, 90)]
    sentences = [("a", 10, 30), ("a", 45, 60), ("a", 80, 120), ("b", 0, 10)]

    # (10,30) in chunk 1; (45,60) in chunk 2; (80,120) runs past both; "b" never retrieved
    assert sentence_recall_at_k(chunks, sentences, 2) == 0.5


def test_bootstrap_interval_excludes_zero_only_for_a_consistent_gain():
    baseline = [0.0, 1.0] * 50
    better = [1.0, 1.0] * 50
    mean, low, high = paired_bootstrap(baseline, better)

    assert mean == 0.5 and low > 0

    _, low, high = paired_bootstrap(baseline, list(reversed(baseline)))
    assert low < 0 < high  # same scores, shuffled: no real difference
