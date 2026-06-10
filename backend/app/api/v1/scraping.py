from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
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
    sb = request.app.state.supabase
    if sb is not None:
        try:
            await sb.table('scraping_jobs').insert({
                'id': job_id, 'query_raw': body.query, 'estado': 'pending',
            }).execute()
        except Exception:
            pass
    return StartScrapingResponse(job_id=job_id)


@router.get('/{job_id}/stream')
async def stream_scraping(job_id: str, query: str, request: Request) -> StreamingResponse:
    checkpointer = request.app.state.checkpointer
    sb = request.app.state.supabase
    graph = build_graph(checkpointer=checkpointer)
    config = {'configurable': {'thread_id': job_id, 'supabase': sb}}
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
            yield f'id: {seq}\nevent: error\ndata: {json.dumps({"event":"error","message":str(exc),"recoverable":False})}\n\n'

    return StreamingResponse(event_generator(), media_type='text/event-stream', headers=_sse_headers())


@router.post('/{job_id}/resume')
async def resume_scraping(job_id: str, body: ResumeScrapingRequest, request: Request) -> StreamingResponse:
    from langgraph.types import Command
    checkpointer = request.app.state.checkpointer
    sb = request.app.state.supabase
    graph = build_graph(checkpointer=checkpointer)
    config = {'configurable': {'thread_id': job_id, 'supabase': sb}}

    async def event_generator() -> AsyncGenerator[str, None]:
        seq = 0
        try:
            async for ev in graph.astream_events(Command(resume=body.selected_agency_ids), config, version='v2'):
                if ev['event'] != 'on_custom_event':
                    continue
                seq += 1
                yield f'id: {seq}\nevent: {ev["name"]}\ndata: {json.dumps(ev["data"])}\n\n'
        except Exception as exc:
            seq += 1
            yield f'id: {seq}\nevent: error\ndata: {json.dumps({"event":"error","message":str(exc),"recoverable":False})}\n\n'

    return StreamingResponse(event_generator(), media_type='text/event-stream', headers=_sse_headers())
