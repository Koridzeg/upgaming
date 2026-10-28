"""A small Markdown parser for the subset our documents use.

Handles front matter, headings, paragraphs, bullet and numbered lists, pipe tables and fenced
code blocks. It is shared by the Markdown loader and the synthetic-corpus renderer. Nested
lists are flattened and inline formatting is stripped, not interpreted.
"""

import re
from dataclasses import dataclass, field

_NUMBERED = re.compile(r"\d+[.)]\s+")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")  # [text](url) and ![alt](src) -> text / alt


def split_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Split a `---`-delimited `key: value` header from the Markdown body."""
    if not text.startswith("---\n"):
        return {}, text
    header, _, body = text[4:].partition("\n---\n")
    meta = {}
    for line in header.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    return meta, body


@dataclass
class MarkdownBlock:
    kind: str  # 'heading' | 'paragraph' | 'bullets' | 'numbered' | 'table' | 'code'
    level: int = 0  # heading level
    text: str = ""  # heading, paragraph or code text
    items: list = field(default_factory=list)  # list items, or table rows (lists of cells)


def parse_markdown(markdown: str) -> list[MarkdownBlock]:
    """Consecutive lines of the same kind extend the current block; a blank line ends it."""
    blocks: list[MarkdownBlock] = []
    current: MarkdownBlock | None = None
    in_code = False

    for line in markdown.splitlines():
        s = line.strip()
        if s.startswith("```"):
            in_code = not in_code
            current = MarkdownBlock("code") if in_code else None
            if current:
                blocks.append(current)
            continue
        if in_code:
            current.text += line + "\n"  # code keeps its exact lines; '#' here is not a heading
            continue
        if not s:
            current = None
            continue
        if s.startswith("#"):
            level = len(s) - len(s.lstrip("#"))
            blocks.append(MarkdownBlock("heading", level=level, text=s[level:].strip()))
            current = None
            continue

        if s.startswith("|"):
            kind, item = "table", [cell.strip() for cell in s.strip("|").split("|")]
            if all(set(cell) <= set("-: ") for cell in item):
                continue  # the |---|---| row under a table header
        elif s[:2] in ("- ", "* ", "+ "):
            kind, item = "bullets", s[2:]
        elif m := _NUMBERED.match(s):
            kind, item = "numbered", s[m.end() :]
        else:
            kind, item = "paragraph", s

        if current is None or current.kind != kind:
            current = MarkdownBlock(kind)
            blocks.append(current)
        if kind == "paragraph":
            # Keep line breaks: short lines like "Author: X" / "Date: Y" are separate facts.
            current.text = f"{current.text}\n{item}" if current.text else item
        else:
            current.items.append(item)
    return blocks


def strip_inline(text: str) -> str:
    """Remove inline Markdown markers: links keep their text, emphasis and code marks go."""
    text = _LINK.sub(r"\1", text)
    return text.replace("**", "").replace("__", "").replace("`", "")
