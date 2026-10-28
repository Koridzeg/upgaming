"""FastAPI application: the API under /api, the Angular UI at /.

Run locally (from backend/):  uvicorn investigator.main:app --reload
Interactive API docs:         http://localhost:8000/docs
"""

import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException  # what StaticFiles raises (FastAPI's subclasses it)

from investigator.api import documents, investigate, system
from investigator.api.services import Services, build_services
from investigator.config import Settings, get_settings
from investigator.db import create_pool, database_url
from investigator.migrate import apply_migrations

ServicesFactory = Callable[[Settings, asyncpg.Pool], Awaitable[Services]]


class SinglePageApp(StaticFiles):
    """Static files, but unknown paths get index.html so Angular's router can handle them
    (a browser refresh on /documents must load the app, not a 404). /api stays a real 404."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or path.startswith("api"):
                raise
            return await super().get_response("index.html", scope)


def create_app(services_factory: ServicesFactory = build_services) -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Startup: schema up to date, pool open, models loaded. Shutdown: close them."""
        logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s: %(message)s")
        async with database_url(settings) as url:
            await apply_migrations(url)
            pool = await create_pool(settings, url)
            app.state.services = await services_factory(settings, pool)
            try:
                yield
            finally:
                await app.state.services.aclose()
                await pool.close()

    app = FastAPI(
        title="AI Investigation Engine",
        version="1.0.0",
        description="Multi-document investigation: hybrid retrieval, a tool-calling agent, "
        "citations, contradiction detection and abstention.",
        lifespan=lifespan,
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    for module in (system, documents, investigate):
        app.include_router(module.router)
    if settings.frontend_dist.is_dir():  # mounted last: API routes take precedence
        app.mount("/", SinglePageApp(directory=settings.frontend_dist, html=True), name="ui")
    return app


app = create_app()
