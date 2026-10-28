from investigator.datasets.hotpotqa import build_corpus, build_questions


def example(qid, paragraphs, supporting):
    """A HotpotQA row in the shape pyarrow returns: columns of parallel lists."""
    return {
        "id": qid,
        "question": f"question {qid}",
        "answer": "answer",
        "type": "bridge",
        "level": "hard",
        "context": {
            "title": [title for title, _ in paragraphs],
            "sentences": [sentences for _, sentences in paragraphs],
        },
        "supporting_facts": {
            "title": [title for title, _ in supporting],
            "sent_id": [sent_id for _, sent_id in supporting],
        },
    }


def test_build_corpus_pools_and_deduplicates_paragraphs():
    examples = [
        example("q1", [("A", ["Alpha one.", " Alpha two."]), ("B", ["66 km."])], [("A", 0)]),
        example(
            "q2",
            [
                ("A", ["Alpha one.", " Alpha two."]),  # exact duplicate
                ("B", ["66\xa0km."]),  # a non-breaking space instead of a space
                ("C", ["Gamma."]),
                ("D", [""]),  # empty
            ],
            [("C", 0)],
        ),
        example("q3", [("C", ["Something else entirely."])], [("C", 0)]),  # real conflict
    ]

    corpus, stats, conflicts = build_corpus(examples)

    assert [p.title for p in corpus] == ["A", "B", "C"]
    assert corpus[0].text == "Alpha one. Alpha two."
    assert stats["raw_paragraphs"] == 7
    assert stats["exact_duplicates"] == 1
    assert stats["duplicates_after_normalization"] == 1
    assert stats["empty_paragraphs"] == 1
    assert conflicts == {"C"}


def test_questions_with_conflicting_gold_paragraphs_are_flagged():
    examples = [
        example("q1", [("A", ["x."])], [("A", 0)]),
        example("q2", [("C", ["y."])], [("C", 0), ("C", 1)]),
    ]

    questions = build_questions(examples, conflict_titles={"C"})

    assert questions[0]["labels_reliable"] is True
    assert questions[1]["labels_reliable"] is False
    assert questions[1]["supporting_titles"] == ["C"]  # deduplicated, order kept
    assert questions[1]["supporting_facts"] == [
        {"title": "C", "sent_id": 0},
        {"title": "C", "sent_id": 1},
    ]
