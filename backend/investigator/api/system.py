"""Health and metrics: what a host's health check and an operator need."""

import asyncpg
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from investigator.api.services import Services, get_services

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health(services: Services = Depends(get_services)):
    """A real database round-trip, so the host's health check catches a dead database."""
    try:
        async with services.pool.acquire(timeout=5) as conn:
            documents = await conn.fetchval("SELECT count(*) FROM documents")
    except (OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError):
        return JSONResponse(status_code=503, content={"status": "error", "database": "unreachable"})
    return {
        "status": "ok" if services.embedding_model_matches else "degraded",
        "database": "ok",
        "documents": documents,
        "llm": "configured" if services.llm else "not configured",
        "embedding_model_matches_index": services.embedding_model_matches,
    }


@router.get("/metrics")
async def metrics(services: Services = Depends(get_services)):
    """Corpus size and LLM usage since the app started (calls, failures, tokens, latency)."""
    async with services.pool.acquire() as conn:
        documents = await conn.fetch(
            "SELECT source_type, count(*) AS documents FROM documents GROUP BY 1 ORDER BY 1"
        )
        chunks = await conn.fetch(
            "SELECT strategy, count(*) AS chunks FROM chunks GROUP BY 1 ORDER BY 1"
        )
    return {
        "documents": {r["source_type"]: r["documents"] for r in documents},
        "chunks": {r["strategy"]: r["chunks"] for r in chunks},
        "llm": services.llm.usage() if services.llm else {},
        "embedding_model": getattr(services.embedder, "name", None),
        "notes": services.notes,
    }
