import json

from investigator.datasets.synthetic import (
    CORPUS_DIR,
    SYNTHETIC_DIR,
    parse_blocks,
    split_front_matter,
)


def test_split_front_matter():
    meta, body = split_front_matter("---\ntitle: A: B\nrender: pdf\n---\n# Heading\n")

    assert meta == {"title": "A: B", "render": "pdf"}  # only the first ':' splits
    assert body == "# Heading\n"


def test_front_matter_is_optional():
    assert split_front_matter("# Just markdown") == ({}, "# Just markdown")


def test_parse_blocks_groups_lines_by_kind():
    markdown = "\n".join(
        [
            "# Title",
            "Author: X",
            "Date: Y",
            "",
            "- one",
            "- two",
            "1. first",
            "2. second",
            "| A | B |",
            "| --- | --- |",
            "| 1 | 2 |",
        ]
    )

    blocks = parse_blocks(markdown)

    assert [(b.kind, b.level) for b in blocks] == [
        ("heading", 1),
        ("paragraph", 0),
        ("bullets", 0),
        ("numbered", 0),
        ("table", 0),
    ]
    assert blocks[1].text == "Author: X\nDate: Y"
    assert blocks[2].items == ["one", "two"]
    assert blocks[3].items == ["first", "second"]
    assert blocks[4].items == [["A", "B"], ["1", "2"]]  # separator row skipped


def test_every_expected_source_exists_in_the_corpus():
    """Guards the eval set: a typo in questions.json would silently make a question unanswerable."""
    if not CORPUS_DIR.exists():
        return  # corpus not generated yet (python -m investigator.datasets.synthetic)
    questions = json.loads((SYNTHETIC_DIR / "questions.json").read_text(encoding="utf-8"))
    corpus_files = {p.name for p in CORPUS_DIR.iterdir()}

    for q in questions:
        missing = set(q["expected_sources"]) - corpus_files
        assert not missing, f"{q['id']} cites missing files: {missing}"
        assert q["expect_abstain"] == (q["category"] == "unanswerable")
