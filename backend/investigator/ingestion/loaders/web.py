"""HTML loader: the visible text of a page as blocks.

Removed before extraction:
- code and embedded objects (script, style, svg, iframe, ...);
- page chrome (nav, footer, aside, forms): repeated on every page, never the content;
- comments and hidden elements (`hidden`, `aria-hidden="true"`, `display:none`,
  `visibility:hidden`, `opacity:0`, `font-size:0`). A reader can't see hidden text, so it
  can't be content the uploader meant to share, and it is a common place to plant prompt
  injections. The number removed is recorded in the metadata so it stays visible.

Not detected: text hidden by colour (white on white) or by positioning off-screen; that needs
a rendering engine. The agent's other defences still apply to it.
"""

import re

from bs4 import BeautifulSoup, Comment

from investigator.ingestion.models import (
    Block,
    LoadedDocument,
    decode_text,
    pick_title,
    table_blocks,
)

_DROP_TAGS = [
    "script", "style", "noscript", "template", "iframe", "object", "embed", "svg", "canvas",
    "nav", "footer", "aside", "form", "button",
]  # fmt: skip
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_TEXT_BLOCKS = _HEADINGS | {
    "p", "li", "pre", "blockquote", "dt", "dd", "figcaption", "address",
    "div", "section", "article", "main", "header",
}  # fmt: skip
# "opacity: 0" and "font-size: 0px" hide text; "opacity: 0.8" and "font-size: 0.9em" don't.
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|(opacity|font-size)\s*:\s*0(\.0+)?(?![.\d])",
    re.IGNORECASE,
)


def load_html(data: bytes, filename: str) -> LoadedDocument:
    text, encoding = decode_text(data)
    soup = BeautifulSoup(text, "html.parser")

    title = soup.title.get_text(strip=True) if soup.title else None
    meta = {
        tag["name"].lower(): tag["content"].strip()
        for tag in soup.find_all("meta")
        if tag.get("name") and tag.get("content")
    }
    language = soup.html.get("lang") if soup.html else None

    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()
    hidden = [tag for tag in soup.find_all(True) if _is_hidden(tag)]
    for tag in hidden:
        if not tag.decomposed:  # may already be gone inside a hidden parent
            tag.decompose()

    root = soup.body or soup
    blocks = _extract_blocks(root)
    if not blocks and root.get_text(strip=True):
        blocks = [Block("paragraph", root.get_text(" ", strip=True))]

    metadata = {
        "author": meta.get("author"),
        "created_at": meta.get("date") or meta.get("dcterms.created"),
        "description": meta.get("description"),
        "keywords": meta.get("keywords"),
        "language": language,
        "hidden_elements_removed": len(hidden),
        "encoding": encoding if encoding != "utf-8" else None,
    }
    return LoadedDocument(pick_title(title, blocks, filename), "html", blocks, metadata)


def _extract_blocks(root) -> list[Block]:
    """Walk elements in document order and emit the innermost text-bearing ones.

    A container (div, section, li with a nested p) is skipped when it holds other text
    blocks, because those children are visited on their own; otherwise text would repeat.
    """
    blocks: list[Block] = []
    for element in root.find_all(True):
        if element.name == "table":
            rows = [
                [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
                for row in element.find_all("tr")
            ]
            blocks.extend(table_blocks(rows))
            continue
        if element.name not in _TEXT_BLOCKS or element.find_parent("table"):
            continue
        if element.find([*_TEXT_BLOCKS, "table"]):
            continue
        text = element.get_text(" ", strip=True)
        if not text:
            continue
        if element.name in _HEADINGS:
            blocks.append(Block("heading", text, level=int(element.name[1])))
        elif element.name == "li":
            blocks.append(Block("list_item", text))
        elif element.name == "pre":
            blocks.append(Block("code", element.get_text()))
        else:
            blocks.append(Block("paragraph", text))
    return blocks


def _is_hidden(tag) -> bool:
    return (
        tag.has_attr("hidden")
        or tag.get("aria-hidden") == "true"
        or bool(_HIDDEN_STYLE.search(tag.get("style", "")))
    )
