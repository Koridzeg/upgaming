"""Two chunking strategies, compared in the evaluation.

fixed       Sliding windows of MAX_TOKENS tokens, overlapping by FIXED_OVERLAP. Ignores structure:
            a window can start mid-sentence or even mid-word. The classic baseline.
structural  Packs whole blocks (paragraphs, list items, table rows, records) into chunks of at
            most MAX_TOKENS. A heading always starts a new chunk; a block that is too long is
            split at sentence boundaries, and only a single huge sentence is cut by tokens.

Both strategies give every chunk the same context header (document title + the headings above
the chunk), so the comparison changes one variable: where the boundaries fall.

Chunk text is always an exact substring of the document content, `content[char_start:char_end]`,
so a citation can point at the precise passage.
"""

import re
from bisect import bisect_right
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import numpy as np

from investigator.ingestion.models import LoadedDocument
from investigator.ingestion.tokenizer import Tokenizer

MAX_TOKENS = 128  # 34% of HotpotQA documents exceed it, so the strategies really differ
FIXED_OVERLAP = 32  # 25%: a fact cut by one window boundary is whole in the neighbour

# A sentence ends at . ! or ? followed by whitespace and an uppercase letter, digit or quote.
# Abbreviations ("T.S. Arthur") cause false splits; that only matters inside an over-long block.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


@dataclass
class Chunk:
    index: int
    text: str
    context: str
    token_count: int
    char_start: int
    char_end: int
    metadata: dict = field(default_factory=dict)
    embedding: np.ndarray | None = None  # filled in by the ingestion step, before storing


def chunk_fixed(
    document: LoadedDocument,
    tokenizer: Tokenizer,
    size: int = MAX_TOKENS,
    overlap: int = FIXED_OVERLAP,
) -> list[Chunk]:
    text = document.text
    tokens = tokenizer.spans(text)
    layout = _Layout(document)
    chunks: list[Chunk] = []
    for first in range(0, len(tokens), size - overlap):
        window = tokens[first : first + size]
        start, end = window[0][0], window[-1][1]
        chunks.append(layout.chunk(len(chunks), start, end, len(window)))
        if first + size >= len(tokens):
            break  # this window reached the end; another would only repeat its tail
    return chunks


def chunk_structural(
    document: LoadedDocument,
    tokenizer: Tokenizer,
    max_tokens: int = MAX_TOKENS,
) -> list[Chunk]:
    text = document.text
    layout = _Layout(document)
    chunks: list[Chunk] = []
    units: list[tuple[int, int, int]] = []  # (start, end, tokens) packed into the open chunk

    def flush() -> None:
        if units:
            start, end = units[0][0], units[-1][1]
            chunks.append(layout.chunk(len(chunks), start, end, sum(u[2] for u in units)))
            units.clear()

    for (start, end), block in zip(layout.block_spans, document.blocks, strict=True):
        if block.kind == "heading":
            flush()  # a new section starts a new chunk; the heading goes into the context
            continue
        for unit in _units(text, start, end, tokenizer, max_tokens):
            if units and sum(u[2] for u in units) + unit[2] > max_tokens:
                flush()
            units.append(unit)
    flush()
    return chunks


CHUNKERS: dict[str, Callable[[LoadedDocument, Tokenizer], list[Chunk]]] = {
    "fixed": chunk_fixed,
    "structural": chunk_structural,
}


def _units(
    text: str, start: int, end: int, tokenizer: Tokenizer, max_tokens: int
) -> Iterator[tuple[int, int, int]]:
    """A block as packing units: whole if it fits, else its sentences, else token windows."""
    count = len(tokenizer.spans(text[start:end]))
    if count <= max_tokens:
        yield start, end, count
        return
    for s_start, s_end in sentence_spans(text, start, end):
        tokens = tokenizer.spans(text[s_start:s_end])
        if len(tokens) <= max_tokens:
            yield s_start, s_end, len(tokens)
            continue
        for i in range(0, len(tokens), max_tokens):  # one enormous "sentence" (a table dump)
            window = tokens[i : i + max_tokens]
            yield s_start + window[0][0], s_start + window[-1][1], len(window)


def sentence_spans(text: str, start: int, end: int) -> Iterator[tuple[int, int]]:
    position = start
    for match in _SENTENCE_BREAK.finditer(text, start, end):
        yield position, match.start()
        position = match.end()
    yield position, end


class _Layout:
    """Where each block sits in the document text, and which headings each block is under."""

    def __init__(self, document: LoadedDocument):
        self.document = document
        self.block_spans: list[tuple[int, int]] = []
        self.heading_paths: list[list[str]] = []
        position, stack = 0, []  # stack of (level, heading text)
        for block in document.blocks:
            self.block_spans.append((position, position + len(block.text)))
            position += len(block.text) + 2  # blocks are joined by a blank line
            if block.kind == "heading":
                stack = [(lvl, t) for lvl, t in stack if lvl < block.level] + [
                    (block.level, block.text)
                ]
            self.heading_paths.append([t for _, t in stack])
        self._starts = [start for start, _ in self.block_spans]

    def chunk(self, index: int, start: int, end: int, token_count: int) -> Chunk:
        first = bisect_right(self._starts, start) - 1
        last = bisect_right(self._starts, end - 1) - 1
        headings = self.heading_paths[first]
        pages = sorted({b.page for b in self.document.blocks[first : last + 1] if b.page})
        metadata: dict = {"char_start": start, "char_end": end}
        if headings:
            metadata["headings"] = headings
        if pages:
            metadata["pages"] = pages
        return Chunk(
            index=index,
            text=self.document.text[start:end],
            context=context_header(self.document.title, headings),
            token_count=token_count,
            char_start=start,
            char_end=end,
            metadata=metadata,
        )


def context_header(title: str, headings: list[str]) -> str:
    """'Title > Section > Subsection'. A heading that repeats the title is skipped.

    "Repeats" compares words, not exact strings: a PDF's metadata title and its first heading
    often differ only in punctuation ("Postmortem INC-1 - Delays" / "Postmortem: INC-1 Delays").
    """
    title_words = _words(title)
    parts = [title] + [h for h in headings if _words(h) != title_words]
    return " > ".join(parts)


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())
