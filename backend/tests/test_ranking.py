import numpy as np
import pytest

from investigator.retrieval import strategy_literal
from investigator.retrieval.ranking import mmr_select, title_match, weighted_rrf


def test_rrf_rewards_agreement_between_retrievers():
    scores = weighted_rrf({"dense": ["a", "b"], "keyword": ["c", "b"]}, weights={})

    # b is only 2nd in each list, but found by both: 1/62 + 1/62 > 1/61
    assert max(scores, key=scores.get) == "b"
    assert scores["a"] == pytest.approx(1 / 61)


def test_rrf_weights_scale_each_retriever():
    rankings = {"dense": ["a"], "keyword": ["b"]}

    assert weighted_rrf(rankings, {"dense": 1.0, "keyword": 0.0}) == {"a": 1 / 61, "b": 0.0}
    scores = weighted_rrf(rankings, {"dense": 1.0, "keyword": 2.0})
    assert scores["b"] == pytest.approx(2 * scores["a"])


@pytest.mark.parametrize(
    ("title", "query", "expected"),
    [
        ("Ed Wood (film)", "Were Scott Derrickson and Ed Wood of the same nationality?", 1.0),
        ("Scott Derrickson", "Were Scott Derrickson and Ed Wood of the same nationality?", 1.0),
        ("Elijah Wood", "Were Scott Derrickson and Ed Wood of the same nationality?", 0.0),
        # Sharing an incident id is not naming the document (the bug that motivated the rule).
        (
            "Postmortem INC-2026-0311 (DRAFT)",
            "What size was the pool raised to during INC-2026-0311?",
            0.0,
        ),
    ],
)
def test_title_match_requires_the_whole_title(title, query, expected):
    assert title_match(title, query) == expected


def unit(*values):
    vector = np.array(values, dtype=np.float32)
    return vector / np.linalg.norm(vector)


def test_mmr_skips_a_near_copy_in_favour_of_something_new():
    relevance = np.array([1.0, 0.95, 0.8])
    vectors = np.vstack([unit(1, 0, 0), unit(1, 0.05, 0), unit(0, 1, 0)])  # 0 and 1 nearly equal

    assert mmr_select(relevance, vectors, k=2, lam=0.5) == [0, 2]
    assert mmr_select(relevance, vectors, k=3, lam=1.0) == [0, 1, 2]  # lam 1 = plain ranking


def test_unknown_strategy_never_reaches_sql():
    assert strategy_literal("structural") == "'structural'"
    with pytest.raises(ValueError):
        strategy_literal("structural' OR '1'='1")
