from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.graphs.extraction.graph import build_graph

router = APIRouter()


class StartScrapingRequest(BaseModel):
    query: str


class StartScrapingResponse(BaseModel):
    job_id: str


class ResumeScrapingRequest(BaseModel):
    selected_agency_ids: list[str]


def _sse_headers() -> dict[str, str]:
    return {'Cache-Control': 'no-cache', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no'}


@router.post('/start', response_model=StartScrapingResponse)
async def start_scraping(body: StartScrapingRequest, request: Request) -> StartScrapingResponse:
    job_id = str(uuid.uuid4())
    pool = request.app.state.db_pool
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "insert into public.scraping_jobs (id, query_raw, estado) values ($1, $2, 'pending')",
                    uuid.UUID(job_id), body.query,
                )
        except Exception:
            pass  # DB write optional — job still works without it
    return StartScrapingResponse(job_id=job_id)


@router.get('/{job_id}/stream')
async def stream_scraping(job_id: str, query: str, request: Request) -> StreamingResponse:
    checkpointer = request.app.state.checkpointer
    pool = request.app.state.db_pool

    graph = build_graph(checkpointer=checkpointer)
    config = {'configurable': {'thread_id': job_id, 'db_pool': pool}}
    inputs = {'query': query, 'job_id': job_id}

    async def event_generator() -> AsyncGenerator[str, None]:
        seq = 0
        try:
            async for ev in graph.astream_events(inputs, config, version='v2'):
                if ev['event'] != 'on_custom_event':
                    continue
                seq += 1
                yield f'id: {seq}\nevent: {ev["name"]}\ndata: {json.dumps(ev["data"])}\n\n'
        except Exception as exc:
            seq += 1
            yield f'id: {seq}\nevent: error\ndata: {json.dumps({"event": "error", "message": str(exc), "recoverable": False})}\n\n'

    return StreamingResponse(event_generator(), media_type='text/event-stream', headers=_sse_headers())


@router.post('/{job_id}/resume')
async def resume_scraping(
    job_id: str, body: ResumeScrapingRequest, request: Request
) -> StreamingResponse:
    """Resume a paused graph after agency review. Streams the Instagram phase."""
    from langgraph.types import Command

    checkpointer = request.app.state.checkpointer
    pool = request.app.state.db_pool

    if checkpointer is None:
        raise HTTPException(status_code=503, detail='Checkpointer not available')

    graph = build_graph(checkpointer=checkpointer)
    config = {'configurable': {'thread_id': job_id, 'db_pool': pool}}

    async def event_generator() -> AsyncGenerator[str, None]:
        seq = 0
        try:
            async for ev in graph.astream_events(
                Command(resume=body.selected_agency_ids),
                config,
                version='v2',
            ):
                if ev['event'] != 'on_custom_event':
                    continue
                seq += 1
                yield f'id: {seq}\nevent: {ev["name"]}\ndata: {json.dumps(ev["data"])}\n\n'
        except Exception as exc:
            seq += 1
            yield f'id: {seq}\nevent: error\ndata: {json.dumps({"event": "error", "message": str(exc), "recoverable": False})}\n\n'

    return StreamingResponse(event_generator(), media_type='text/event-stream', headers=_sse_headers())
