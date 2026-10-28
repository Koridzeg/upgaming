"""Documents: upload (add), list, view, replace (update) and delete."""

import asyncio
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel

from investigator.api.services import Services, get_services
from investigator.ingestion.loaders import SUPPORTED_EXTENSIONS, load_document
from investigator.ingestion.models import DocumentError
from investigator.ingestion.store import delete_document, ingest_document

router = APIRouter(prefix="/api/documents", tags=["documents"])


class DocumentSummary(BaseModel):
    id: UUID
    source_id: str
    title: str
    source_type: str
    file_format: str
    version: int
    chunks: int
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]


class DocumentPage(BaseModel):
    total: int
    items: list[DocumentSummary]


class DocumentDetail(DocumentSummary):
    content: str
    content_truncated: bool
    versions: list[dict[str, Any]]


class IngestResponse(BaseModel):
    status: str  # created | updated | unchanged | duplicate
    source_id: str
    document_id: UUID | None
    version: int | None
    chunk_count: int
    duplicate_of: str | None
    near_duplicates: list[dict[str, Any]]


_SUMMARY = """
    SELECT d.id, d.source_id, d.title, d.source_type, d.file_format, d.version,
           d.created_at, d.updated_at,
           d.metadata - 'sentence_spans' AS metadata,
           (SELECT count(*) FROM chunks c WHERE c.document_id = d.id) AS chunks
      FROM documents d
"""


@router.get("", response_model=DocumentPage)
async def list_documents(
    source_type: str | None = None,
    q: str | None = Query(None, description="filter by words in the title"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    services: Services = Depends(get_services),
):
    where = "WHERE ($1::text IS NULL OR d.source_type = $1)"
    where += " AND ($2::text IS NULL OR d.title ILIKE '%' || $2 || '%')"
    async with services.pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT count(*) FROM documents d {where}", source_type, q)
        rows = await conn.fetch(
            f"{_SUMMARY} {where} ORDER BY d.updated_at DESC, d.title LIMIT $3 OFFSET $4",
            source_type, q, limit, offset,
        )  # fmt: skip
    return DocumentPage(total=total, items=[DocumentSummary(**dict(r)) for r in rows])


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: UUID, services: Services = Depends(get_services)):
    async with services.pool.acquire() as conn:
        row = await conn.fetchrow(f"{_SUMMARY} WHERE d.id = $1", document_id)
        if row is None:
            raise HTTPException(404, "Document not found")
        content = await conn.fetchval("SELECT content FROM documents WHERE id = $1", document_id)
        versions = await conn.fetch(
            "SELECT version, title, replaced_at FROM document_versions"
            " WHERE document_id = $1 ORDER BY version DESC",
            document_id,
        )
    limit = 20_000
    return DocumentDetail(
        **dict(row),
        content=content[:limit],
        content_truncated=len(content) > limit,
        versions=[dict(v) for v in versions],
    )


@router.post("", response_model=IngestResponse)
async def upload_document(
    file: UploadFile,
    response: Response,
    background: BackgroundTasks,
    services: Services = Depends(get_services),
):
    """Add a document. Re-uploading a file with the same name updates it (new version)."""
    filename = Path(file.filename or "upload").name  # never trust a client-sent path
    result = await _ingest(services, filename, await _read(file, services), f"upload:{filename}")
    if result.status == "created":
        response.status_code = 201
    background.add_task(services.stats.refresh)
    return result


@router.put("/{document_id}", response_model=IngestResponse)
async def replace_document(
    document_id: UUID,
    file: UploadFile,
    background: BackgroundTasks,
    services: Services = Depends(get_services),
):
    """Update a document with new file content; it keeps its identity and gets a new version."""
    async with services.pool.acquire() as conn:
        source_id = await conn.fetchval(
            "SELECT source_id FROM documents WHERE id = $1", document_id
        )
    if source_id is None:
        raise HTTPException(404, "Document not found")
    filename = Path(file.filename or "upload").name
    result = await _ingest(services, filename, await _read(file, services), source_id)
    background.add_task(services.stats.refresh)
    return result


@router.delete("/{document_id}", status_code=204)
async def remove_document(
    document_id: UUID, background: BackgroundTasks, services: Services = Depends(get_services)
):
    async with services.pool.acquire() as conn:
        if not await delete_document(conn, document_id):
            raise HTTPException(404, "Document not found")
    background.add_task(services.stats.refresh)


async def _read(file: UploadFile, services: Services) -> bytes:
    limit = services.settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File is larger than {services.settings.max_upload_mb} MB")
    return data


async def _ingest(services: Services, filename: str, data: bytes, source_id: str) -> IngestResponse:
    try:
        # Parsing a PDF is CPU work: off the event loop, so other requests aren't blocked.
        document = await asyncio.to_thread(load_document, filename, data)
    except DocumentError as exc:
        raise HTTPException(422, f"{exc} (supported: {', '.join(SUPPORTED_EXTENSIONS)})") from exc
    async with services.pool.acquire() as conn:
        result = await ingest_document(
            conn,
            document,
            source_id=source_id,
            source_type="upload",
            strategies=services.settings.chunk_strategies,
            tokenizer=services.tokenizer,
            embedder=services.embedder,
        )
    return IngestResponse(**asdict(result))
