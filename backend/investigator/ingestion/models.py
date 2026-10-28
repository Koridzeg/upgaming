"""The format-independent document model every loader produces, plus small shared helpers.

Each loader turns its format into the same thing: a title, metadata, and an ordered list of
blocks (headings, paragraphs, list items, table rows, records). Everything after loading -
chunking, embedding, citations - only ever sees this model, never PDF or HTML specifics.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

BlockKind = Literal["heading", "paragraph", "list_item", "table_row", "record", "code"]


class DocumentError(Exception):
    """Base class for problems with an uploaded file (reported to the user as a 4xx)."""


class UnsupportedFormatError(DocumentError):
    pass


class DocumentParseError(DocumentError):
    pass


@dataclass
class Block:
    kind: BlockKind
    text: str
    level: int = 0  # heading level, 1 = top
    page: int | None = None  # 1-based page number, for formats that have pages


@dataclass
class LoadedDocument:
    title: str
    file_format: str
    blocks: list[Block]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """The full text: blocks separated by blank lines. Stored as documents.content."""
        return "\n\n".join(block.text for block in self.blocks)


def linearize_row(header: list[str], row: list[str]) -> str:
    """One table row as self-describing text: 'Service: merchant-api; Effect: timed out'.

    Repeating the column names in every row means a row still makes sense when it ends up in
    a chunk without the table header, which is what retrieval and the LLM actually see.
    """
    if len(header) != len(row):
        return " | ".join(cell for cell in row if cell)
    return "; ".join(f"{name}: {value}" for name, value in zip(header, row, strict=True))


def table_blocks(rows: list[list[str]], page: int | None = None) -> list[Block]:
    """Turn table rows (first row = header) into one `table_row` block per data row."""
    rows = [[" ".join((cell or "").split()) for cell in row] for row in rows]
    rows = [row for row in rows if any(row)]
    if not rows:
        return []
    if len(rows) == 1:
        return [Block("table_row", " | ".join(cell for cell in rows[0] if cell), page=page)]
    header, *body = rows
    return [Block("table_row", linearize_row(header, row), page=page) for row in body]


def decode_text(data: bytes) -> tuple[str, str]:
    """Decode a text file: UTF-8 (with or without BOM), falling back to Latin-1.

    Latin-1 maps every byte to a character, so the fallback never fails; it may show some
    characters wrongly, which is better than rejecting the file. Newlines are normalised.
    """
    try:
        text, encoding = data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = data.decode("latin-1"), "latin-1"
    return text.replace("\r\n", "\n").replace("\r", "\n"), encoding


def pick_title(candidate: str | None, blocks: list[Block], filename: str) -> str:
    """Embedded title if there is one, else the first heading, else the file name."""
    if candidate and candidate.strip():
        return candidate.strip()
    for block in blocks:
        if block.kind == "heading":
            return block.text
    return Path(filename).stem
