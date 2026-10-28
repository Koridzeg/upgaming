"""Search and investigations. An investigation can return one JSON result or stream events.

Streaming uses server-sent events (SSE): one HTTP response that stays open and carries
`event: <type>` / `data: <json>` messages as the agent works - each search, the evidence it
found, warnings, verification, then the final result. The browser shows progress live instead
of a spinner for 10-60 seconds. SSE rather than WebSockets: the flow is one-way (server to
browser), and SSE is plain HTTP, built into browsers (EventSource), and passes proxies.
"""

import json
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from investigator.agent.investigation import DatabaseRetriever, Investigator
from investigator.api.ratelimit import client_key
from investigator.api.services import Services, get_services, require_llm
from investigator.retrieval.search import SearchConfig, search

router = APIRouter(prefix="/api", tags=["investigate"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    mode: Literal["hybrid", "dense", "keyword"] = "hybrid"
    k: int = Field(10, ge=1, le=50)
    source_types: list[str] | None = None


class InvestigateRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    source_types: list[str] | None = None
    verify: bool = True


@router.post("/search")
async def search_documents(body: SearchRequest, services: Services = Depends(get_services)):
    """Hybrid search with each result's score breakdown (ranks per retriever, fusion, re-rank)."""
    config = SearchConfig(mode=body.mode, k=body.k, source_types=body.source_types)
    async with services.pool.acquire() as conn:
        hits = await search(conn, services.embedder, body.query, config)
    return [
        {
            **{k: v for k, v in asdict(h).items() if k != "embedding"},
            "document_id": str(h.document_id),
        }
        for h in hits
    ]


@router.post("/investigate")
async def investigate(
    body: InvestigateRequest, request: Request, services: Services = Depends(get_services)
):
    investigator = _investigator(services, request, body.source_types, body.verify)
    result = await investigator.run(body.question)
    return asdict(result)


@router.get("/investigate/stream")
async def investigate_stream(
    request: Request,
    question: str = Query(min_length=3, max_length=1000),
    source_type: list[str] | None = Query(None),
    services: Services = Depends(get_services),
):
    investigator = _investigator(services, request, source_type, verify=True)

    async def events():
        # If the browser goes away, Starlette stops this generator, which cancels the
        # investigation: no LLM tokens are spent on an answer nobody will read.
        async for event in investigator.stream(question):
            yield f"event: {event.type}\ndata: {json.dumps(event.data, default=str)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},  # no proxy buffering
    )


def _investigator(
    services: Services, request: Request, source_types: list[str] | None, verify: bool
) -> Investigator:
    llm = require_llm(services)
    wait = services.limiter.acquire(client_key(request))
    if wait is not None:
        raise HTTPException(
            429,
            "Too many investigations from this address; the demo runs on a free LLM quota.",
            headers={"Retry-After": str(int(wait) + 1)},
        )
    retriever = DatabaseRetriever(
        services.pool, services.embedder, SearchConfig(source_types=source_types)
    )
    return Investigator(llm, retriever, verify=verify)
