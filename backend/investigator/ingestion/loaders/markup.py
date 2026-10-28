"""Markdown and plain-text loaders."""

import re

from investigator.ingestion.markdown import parse_markdown, split_front_matter, strip_inline
from investigator.ingestion.models import (
    Block,
    LoadedDocument,
    decode_text,
    pick_title,
    table_blocks,
)


def load_markdown(data: bytes, filename: str) -> LoadedDocument:
    text, encoding = decode_text(data)
    front_matter, body = split_front_matter(text)

    blocks: list[Block] = []
    for md in parse_markdown(body):
        if md.kind == "heading":
            blocks.append(Block("heading", strip_inline(md.text), level=md.level))
        elif md.kind == "paragraph":
            blocks.append(Block("paragraph", strip_inline(md.text)))
        elif md.kind in ("bullets", "numbered"):
            blocks.extend(Block("list_item", strip_inline(item)) for item in md.items)
        elif md.kind == "table":
            blocks.extend(table_blocks([[strip_inline(c) for c in row] for row in md.items]))
        elif md.kind == "code" and md.text.strip():
            blocks.append(Block("code", md.text.rstrip("\n")))

    # Front matter is the document's own metadata; 'date' is mapped to the common field name.
    metadata = {key: value for key, value in front_matter.items() if key not in ("title", "date")}
    metadata["created_at"] = front_matter.get("date")
    metadata["encoding"] = encoding if encoding != "utf-8" else None
    return LoadedDocument(
        pick_title(front_matter.get("title"), blocks, filename), "md", blocks, metadata
    )


def load_text(data: bytes, filename: str) -> LoadedDocument:
    text, encoding = decode_text(data)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    blocks = [Block("paragraph", p) for p in paragraphs]
    metadata = {"encoding": encoding if encoding != "utf-8" else None}
    return LoadedDocument(pick_title(None, blocks, filename), "txt", blocks, metadata)
