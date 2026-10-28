"""JSON, JSON Lines and CSV loaders: each record becomes one self-describing text block.

Structured exports (change logs, chat exports, alert lists) are lists of records. Each record
is written as `key: value; key: value`, so a record is understandable on its own when it is
retrieved without the rest of the file. Null values are kept as `null`: "approver: null" is a
fact (no approver was recorded), not a missing field.
"""

import csv
import io
import json
from typing import Any

from investigator.ingestion.models import (
    Block,
    DocumentParseError,
    LoadedDocument,
    decode_text,
    linearize_row,
    pick_title,
)

# Top-level keys that, when present in a JSON export, give the document's date.
_DATE_KEYS = ("date", "created_at", "created", "generated_at", "exported_at", "timestamp")


def load_json(data: bytes, filename: str) -> LoadedDocument:
    text, _ = decode_text(data)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DocumentParseError(f"Invalid JSON: {exc.msg} at line {exc.lineno}") from exc

    path, records = find_records(value)
    metadata: dict[str, Any] = {}
    if isinstance(value, dict):
        fields = {key: v for key, v in value.items() if _is_scalar(v)}
        metadata["fields"] = fields or None
        metadata["created_at"] = next((fields[k] for k in _DATE_KEYS if k in fields), None)

    if records:
        blocks = [Block("record", linearize_record(record)) for record in records]
        metadata |= {"records_path": path, "record_count": len(records)}
    elif isinstance(value, dict):
        blocks = [Block("record", linearize_record(value))]
    else:
        blocks = [Block("paragraph", json.dumps(value, ensure_ascii=False))]

    title = value.get("title") if isinstance(value, dict) else None
    title = title if isinstance(title, str) else None
    return LoadedDocument(pick_title(title, [], filename), "json", blocks, metadata)


def load_jsonl(data: bytes, filename: str) -> LoadedDocument:
    text, _ = decode_text(data)
    blocks = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DocumentParseError(f"Invalid JSON on line {number}: {exc.msg}") from exc
        content = linearize_record(value) if isinstance(value, dict) else json.dumps(value)
        blocks.append(Block("record", content))
    return LoadedDocument(
        pick_title(None, [], filename), "jsonl", blocks, {"record_count": len(blocks)}
    )


def load_csv(data: bytes, filename: str) -> LoadedDocument:
    text, encoding = decode_text(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel  # sniffing fails on e.g. single-column files; assume plain CSV
    rows = [row for row in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in row)]
    if not rows:
        return LoadedDocument(pick_title(None, [], filename), "csv", [], {})

    header, *body = [[cell.strip() for cell in row] for row in rows]
    blocks = [Block("record", linearize_row(header, row)) for row in body]
    metadata = {
        "columns": header,
        "row_count": len(body),
        "encoding": encoding if encoding != "utf-8" else None,
    }
    return LoadedDocument(pick_title(None, [], filename), "csv", blocks, metadata)


def find_records(value: Any, path: str = "$") -> tuple[str | None, list[dict]]:
    """The longest list of objects anywhere in the JSON, with its path (e.g. '$.changes')."""
    best: tuple[str | None, list[dict]] = (None, [])
    if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
        best = (path, value)
    if isinstance(value, dict):
        children = [(f"{path}.{key}", child) for key, child in value.items()]
    elif isinstance(value, list):
        children = [(f"{path}[{i}]", child) for i, child in enumerate(value)]
    else:
        children = []
    for child_path, child in children:
        if isinstance(child, (dict, list)):
            candidate = find_records(child, child_path)
            if len(candidate[1]) > len(best[1]):
                best = candidate
    return best


def linearize_record(record: dict, prefix: str = "") -> str:
    """{'id': 1, 'owner': {'name': 'Ana'}} -> 'id: 1; owner.name: Ana'."""
    parts = []
    for key, value in record.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            parts.append(linearize_record(value, f"{name}."))
        elif isinstance(value, list) and all(_is_scalar(v) for v in value):
            parts.append(f"{name}: {', '.join(_scalar_text(v) for v in value)}")
        elif isinstance(value, list):
            parts.append(f"{name}: {json.dumps(value, ensure_ascii=False)}")
        else:
            parts.append(f"{name}: {_scalar_text(value)}")
    return "; ".join(part for part in parts if part)


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _scalar_text(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
