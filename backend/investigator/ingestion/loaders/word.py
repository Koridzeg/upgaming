"""DOCX loader: headings, paragraphs, lists and tables in document order, plus core properties.

Unlike PDF, DOCX stores structure explicitly: heading and list styles, real tables. So this
loader reads structure instead of guessing it. Headers, footers and comments are not read.
"""

import io
import re
from datetime import datetime

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from investigator.ingestion.models import Block, LoadedDocument, pick_title, table_blocks

_HEADING_STYLE = re.compile(r"Heading (\d)")


def load_docx(data: bytes, filename: str) -> LoadedDocument:
    doc = Document(io.BytesIO(data))
    blocks: list[Block] = []

    # Paragraphs and tables interleaved, in the order they appear in the document.
    for item in doc.iter_inner_content():
        if isinstance(item, Table):
            blocks.extend(table_blocks([[cell.text for cell in row.cells] for row in item.rows]))
            continue
        text = item.text.strip()
        if not text:
            continue
        style = item.style.name if item.style is not None else ""
        if heading := _HEADING_STYLE.match(style):
            blocks.append(Block("heading", text, level=int(heading.group(1))))
        elif style == "Title":
            blocks.append(Block("heading", text, level=1))
        elif style.startswith("List") or _has_numbering(item):
            blocks.append(Block("list_item", text))
        else:
            blocks.append(Block("paragraph", text))

    props = doc.core_properties
    metadata = {
        "author": props.author,
        "created_at": _iso(props.created),
        "modified_at": _iso(props.modified),
        "last_modified_by": props.last_modified_by,
        "doc_type": props.category,
        "keywords": props.keywords,
        "subject": props.subject,
        "revision": props.revision,
    }
    return LoadedDocument(pick_title(props.title, blocks, filename), "docx", blocks, metadata)


def _has_numbering(paragraph: Paragraph) -> bool:
    """Lists made with Word's bullet/number buttons carry numbering properties (numPr)."""
    properties = paragraph._p.pPr  # python-docx has no public API for this
    return properties is not None and properties.numPr is not None


def _iso(value: datetime | None) -> str | None:
    return value.replace(tzinfo=None).isoformat() if value else None
