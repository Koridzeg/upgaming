"""FastAPI application entry point.

Run locally (from backend/):  uvicorn investigator.main:app --reload
"""

import logging
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from investigator.config import get_settings
from investigator.db import create_pool
from investigator.migrate import apply_migrations

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: bring the schema up to date, then open the connection pool. Shutdown: close it."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s: %(message)s")

    await apply_migrations(settings.database_url.get_secret_value())
    app.state.pool = await create_pool(settings)
    try:
        yield
    finally:
        await app.state.pool.close()


app = FastAPI(title="AI Investigation Engine", version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
async def health(request: Request):
    """Liveness plus a real database round-trip, so the host's health check catches a dead DB."""
    try:
        async with request.app.state.pool.acquire(timeout=5) as conn:
            documents = await conn.fetchval("SELECT count(*) FROM documents")
    except (OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        logger.warning("Health check failed: %s", exc)
        return JSONResponse(status_code=503, content={"status": "error", "database": "unreachable"})
    return {"status": "ok", "database": "ok", "documents": documents}
