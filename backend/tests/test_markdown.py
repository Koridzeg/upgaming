from investigator.ingestion.markdown import parse_markdown, split_front_matter, strip_inline


def test_split_front_matter():
    meta, body = split_front_matter("---\ntitle: A: B\nrender: pdf\n---\n# Heading\n")

    assert meta == {"title": "A: B", "render": "pdf"}  # only the first ':' splits
    assert body == "# Heading\n"


def test_front_matter_is_optional():
    assert split_front_matter("# Just markdown") == ({}, "# Just markdown")


def test_parse_groups_lines_by_kind():
    markdown = "\n".join(
        [
            "# Title",
            "Author: X",
            "Date: Y",
            "",
            "- one",
            "* two",
            "1. first",
            "2) second",
            "| A | B |",
            "| --- | --- |",
            "| 1 | 2 |",
        ]
    )

    blocks = parse_markdown(markdown)

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


def test_code_fence_content_is_not_parsed_as_markdown():
    blocks = parse_markdown("```bash\n# not a heading\n- not a list\n```\nAfter")

    assert [b.kind for b in blocks] == ["code", "paragraph"]
    assert blocks[0].text == "# not a heading\n- not a list\n"


def test_strip_inline_keeps_link_text_and_drops_markers():
    assert strip_inline("See **the** [runbook](http://x/y) and `cmd`") == "See the runbook and cmd"
