"""PDF loader: text in reading order, with headings and tables recovered from the layout.

A PDF has no paragraphs, headings or tables, only characters placed on a page. Structure is
rebuilt with layout heuristics, tuned on measured output (body 10.5pt, headings 13/16pt,
~5pt between lines of a paragraph, ~11pt between paragraphs):

- the most common font size is body text; lines clearly larger are headings, and the distinct
  heading sizes, largest first, give the heading levels;
- a vertical gap clearly larger than normal line spacing starts a new paragraph;
- ruled tables are found by pdfplumber from their cell borders and emitted row by row,
  instead of being read as loose lines with each cell split across several of them.

Known limits: scanned PDFs (no text layer), multi-column layouts, bold-only headings at body
size, and repeated page headers/footers are not handled.
"""

import io
import re
from collections import Counter

import pdfplumber

from investigator.ingestion.models import Block, LoadedDocument, pick_title, table_blocks

HEADING_MIN_EXTRA_PT = 1.5  # heading = font at least 1.5pt larger than body text
PARAGRAPH_GAP_RATIO = 0.8  # gap > 0.8 x body font size between lines = new paragraph
SHORT_LINE_RATIO = 0.75  # a line shorter than 75% of the text width was broken on purpose
LIST_MARKER = re.compile(r"^(?:[-*•]|\d+[.)])\s+")
_PDF_DATE = re.compile(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?")


def load_pdf(data: bytes, filename: str) -> LoadedDocument:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = [_page_items(page) for page in pdf.pages]
        info = {key: _decode(value) for key, value in (pdf.metadata or {}).items()}
        page_count = len(pdf.pages)

    blocks = _build_blocks(pages)
    metadata = {
        "author": info.get("Author"),
        "created_at": _pdf_date(info.get("CreationDate")),
        "modified_at": _pdf_date(info.get("ModDate")),
        "doc_type": info.get("Subject"),
        "keywords": info.get("Keywords"),
        "page_count": page_count,
    }
    return LoadedDocument(pick_title(info.get("Title"), blocks, filename), "pdf", blocks, metadata)


def _page_items(page) -> list[tuple[float, str, object]]:
    """A page's text lines and tables as (top, kind, content), sorted top to bottom."""
    tables = page.find_tables()
    boxes = [table.bbox for table in tables]
    # Characters inside a table are read through the table, not as free text lines.
    text_only = page.filter(lambda obj: not _inside_any(obj, boxes))

    items: list[tuple[float, str, object]] = [
        (table.bbox[1], "table", table.extract()) for table in tables
    ]
    for line in text_only.extract_text_lines(return_chars=True):
        sizes = Counter(round(char["size"], 1) for char in line["chars"])
        line_info = {
            "text": line["text"].strip(),
            "size": sizes.most_common(1)[0][0],
            "top": line["top"],
            "bottom": line["bottom"],
            "width": line["x1"] - line["x0"],
        }
        items.append((line["top"], "line", line_info))
    items.sort(key=lambda item: item[0])
    return items


def _inside_any(obj: dict, boxes: list[tuple]) -> bool:
    x = (obj["x0"] + obj["x1"]) / 2
    y = (obj["top"] + obj["bottom"]) / 2
    return any(x0 <= x <= x1 and top <= y <= bottom for x0, top, x1, bottom in boxes)


def _build_blocks(pages: list[list[tuple[float, str, object]]]) -> list[Block]:
    # Body size = the size that most characters are set in, across the whole document.
    sizes: Counter = Counter()
    for items in pages:
        for _, kind, obj in items:
            if kind == "line":
                sizes[obj["size"]] += len(obj["text"])
    body = sizes.most_common(1)[0][0] if sizes else 0.0
    heading_sizes = sorted((s for s in sizes if s >= body + HEADING_MIN_EXTRA_PT), reverse=True)

    blocks: list[Block] = []
    for page_no, items in enumerate(pages, start=1):
        text_width = max((obj["width"] for _, kind, obj in items if kind == "line"), default=0)
        current: Block | None = None  # the paragraph or list item later lines may continue
        prev: dict | None = None  # the previous text line

        for _, kind, obj in items:
            if kind == "table":
                blocks.extend(table_blocks(obj, page=page_no))
                current = prev = None
                continue

            line = obj
            close_to_prev = prev is not None and line["top"] - prev["bottom"] < (
                PARAGRAPH_GAP_RATIO * body
            )
            if line["size"] in heading_sizes:
                level = heading_sizes.index(line["size"]) + 1
                last = blocks[-1] if blocks else None
                wrapped = last is not None and last.kind == "heading" and last.level == level
                if wrapped and close_to_prev and prev["size"] == line["size"]:
                    last.text += " " + line["text"]  # a heading that wrapped onto a 2nd line
                else:
                    blocks.append(Block("heading", line["text"], level=level, page=page_no))
                current = None
            elif marker := LIST_MARKER.match(line["text"]):
                current = Block("list_item", line["text"][marker.end() :], page=page_no)
                blocks.append(current)
            elif current is not None and close_to_prev:
                # Same paragraph. Keep a line break only where the previous line was cut short
                # on purpose ("Date: 2026-03-18"), not where it just wrapped at the margin.
                short = prev["width"] < SHORT_LINE_RATIO * text_width
                current.text += ("\n" if short else " ") + line["text"]
            else:
                current = Block("paragraph", line["text"], page=page_no)
                blocks.append(current)
            prev = line
    return blocks


def _decode(value) -> str | None:
    """PDF info values are text strings, or raw bytes in UTF-16 (with BOM) or PDFDocEncoding."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-16") if value.startswith(b"\xfe\xff") else value.decode("latin-1")
    return str(value)


def _pdf_date(value: str | None) -> str | None:
    """'D:20260318143000Z' -> '2026-03-18T14:30:00' (the time zone is dropped)."""
    match = _PDF_DATE.match(value or "")
    if not match:
        return None
    year, month, day, hour, minute, second = (int(g) if g else None for g in match.groups())
    return (
        f"{year:04d}-{month or 1:02d}-{day or 1:02d}"
        f"T{hour or 0:02d}:{minute or 0:02d}:{second or 0:02d}"
    )
