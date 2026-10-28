import pytest

from investigator.datasets.synthetic import CORPUS_DIR
from investigator.ingestion.chunking import (
    MAX_TOKENS,
    chunk_fixed,
    chunk_structural,
    context_header,
)
from investigator.ingestion.loaders import load_document
from investigator.ingestion.models import Block, LoadedDocument
from investigator.ingestion.tokenizer import WhitespaceTokenizer, get_tokenizer

TOKENS = WhitespaceTokenizer()  # one token per word: easy to count by eye


def words(n: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


def doc(*blocks: Block, title: str = "Report") -> LoadedDocument:
    return LoadedDocument(title, "md", list(blocks))


def assert_exact_substrings(document, chunks):
    for chunk in chunks:
        assert document.text[chunk.char_start : chunk.char_end] == chunk.text


def test_fixed_windows_overlap_and_reach_the_end():
    document = doc(Block("paragraph", words(25)))

    chunks = chunk_fixed(document, TOKENS, size=10, overlap=3)

    assert [c.token_count for c in chunks] == [10, 10, 10, 4]  # windows start at 0, 7, 14, 21
    assert chunks[1].text.startswith("w7 ")  # the last 3 words of chunk 0 repeat
    assert chunks[-1].text.endswith("w24")
    assert_exact_substrings(document, chunks)


def test_fixed_short_document_is_one_chunk():
    assert len(chunk_fixed(doc(Block("paragraph", words(5))), TOKENS, size=10, overlap=3)) == 1


def test_structural_splits_at_headings_and_moves_them_into_the_context():
    document = doc(
        Block("heading", "Intro", level=1),
        Block("paragraph", words(4, "a")),
        Block("heading", "Details", level=2),
        Block("paragraph", words(4, "b")),
        Block("heading", "Appendix", level=1),
        Block("paragraph", words(4, "c")),
    )

    chunks = chunk_structural(document, TOKENS, max_tokens=50)

    assert [c.text for c in chunks] == [words(4, "a"), words(4, "b"), words(4, "c")]
    assert [c.context for c in chunks] == [
        "Report > Intro",
        "Report > Intro > Details",
        "Report > Appendix",  # a new level-1 heading closes the previous section path
    ]
    assert_exact_substrings(document, chunks)


def test_structural_packs_whole_blocks_and_never_splits_one():
    rows = [Block("table_row", words(4, f"r{i}_")) for i in range(3)]
    document = doc(*rows)

    chunks = chunk_structural(document, TOKENS, max_tokens=10)

    assert [c.token_count for c in chunks] == [8, 4]  # 4 + 4 fit; a third row would make 12
    assert chunks[0].text == f"{rows[0].text}\n\n{rows[1].text}"
    assert_exact_substrings(document, chunks)


def test_structural_splits_long_blocks_at_sentences_then_by_tokens():
    sentences = "One two three. Four five six. Seven eight nine."
    giant = words(25)  # no sentence boundary at all
    document = doc(Block("paragraph", sentences), Block("paragraph", giant))

    chunks = chunk_structural(document, TOKENS, max_tokens=4)

    assert [c.text for c in chunks[:3]] == ["One two three.", "Four five six.", "Seven eight nine."]
    assert [c.token_count for c in chunks[3:]] == [4, 4, 4, 4, 4, 4, 1]
    assert_exact_substrings(document, chunks)


def test_context_header_does_not_repeat_the_title():
    # The real case: PDF metadata title vs the document's first heading.
    title = "Postmortem INC-2026-0311 - Card payment processing delays"
    headings = ["Postmortem: INC-2026-0311 Card payment processing delays", "Root cause"]

    assert context_header(title, headings) == f"{title} > Root cause"


@pytest.mark.parametrize("chunker", [chunk_fixed, chunk_structural])
def test_real_tokenizer_respects_the_limit_on_a_real_document(chunker):
    path = CORPUS_DIR / "postmortem-INC-2026-0311.pdf"
    document = load_document(path.name, path.read_bytes())

    chunks = chunker(document, get_tokenizer())

    assert all(c.token_count <= MAX_TOKENS for c in chunks)
    assert_exact_substrings(document, chunks)
    assert all(c.context.startswith(document.title) for c in chunks)
    assert {p for c in chunks for p in c.metadata["pages"]} == {1, 2}
