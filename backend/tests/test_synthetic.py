import json

from investigator.datasets.synthetic import CORPUS_DIR, SYNTHETIC_DIR


def test_every_expected_source_exists_in_the_corpus():
    """Guards the eval set: a typo in questions.json would silently make a question unanswerable."""
    questions = json.loads((SYNTHETIC_DIR / "questions.json").read_text(encoding="utf-8"))
    corpus_files = {p.name for p in CORPUS_DIR.iterdir()}

    for q in questions:
        missing = set(q["expected_sources"]) - corpus_files
        assert not missing, f"{q['id']} cites missing files: {missing}"
        assert q["expect_abstain"] == (q["category"] == "unanswerable")
