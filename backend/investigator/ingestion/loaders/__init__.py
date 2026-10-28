"""File type dispatch. `load_document(filename, data)` is the single entry point for loading.

Usage (inspect what a file turns into): python -m investigator.ingestion.loaders FILE
"""

from collections.abc import Callable
from pathlib import Path

from investigator.ingestion.loaders.markup import load_markdown, load_text
from investigator.ingestion.loaders.pdf import load_pdf
from investigator.ingestion.loaders.structured import load_csv, load_json, load_jsonl
from investigator.ingestion.loaders.web import load_html
from investigator.ingestion.loaders.word import load_docx
from investigator.ingestion.models import (
    DocumentError,
    DocumentParseError,
    LoadedDocument,
    UnsupportedFormatError,
)

Loader = Callable[[bytes, str], LoadedDocument]

LOADERS: dict[str, Loader] = {
    ".pdf": load_pdf,
    ".docx": load_docx,
    ".html": load_html,
    ".htm": load_html,
    ".md": load_markdown,
    ".markdown": load_markdown,
    ".txt": load_text,
    ".json": load_json,
    ".jsonl": load_jsonl,
    ".csv": load_csv,
}
SUPPORTED_EXTENSIONS = sorted(LOADERS)


def load_document(filename: str, data: bytes) -> LoadedDocument:
    """Parse an uploaded file. Raises a DocumentError subclass with a user-readable message."""
    extension = Path(filename).suffix.lower()
    loader = LOADERS.get(extension)
    if loader is None:
        raise UnsupportedFormatError(
            f"Unsupported file type '{extension or filename}'. "
            f"Supported: {', '.join(SUPPORTED_EXTENSIONS)}"
        )
    if not data:
        raise DocumentParseError(f"{filename} is empty")

    try:
        document = loader(data, filename)
    except DocumentError:
        raise
    except Exception as exc:
        # Every parser library raises its own exception types (pdfminer, zipfile, XML, ...).
        # To the caller they all mean the same thing, so they become one domain error here.
        raise DocumentParseError(f"Could not read {filename}: {exc}") from exc

    document.blocks = [block for block in document.blocks if block.text.strip()]
    if not document.blocks:
        hint = " (scanned PDFs need OCR, which is not supported)" if extension == ".pdf" else ""
        raise DocumentParseError(f"No text found in {filename}{hint}")

    document.metadata = {
        key: value for key, value in document.metadata.items() if value not in (None, "", [], {})
    } | {"source_filename": filename, "size_bytes": len(data)}
    return document
