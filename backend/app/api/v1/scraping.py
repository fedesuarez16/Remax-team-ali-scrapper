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


@router.post('/start', response_model=StartScrapingResponse)
async def start_scraping(body: StartScrapingRequest, request: Request) -> StartScrapingResponse:
    pool = request.app.state.db_pool
    job_id = str(uuid.uuid4())
    async with pool.acquire() as conn:
        await conn.execute(
            '''insert into public.scraping_jobs (id, query_raw, estado)
               values ($1, $2, 'pending')''',
            uuid.UUID(job_id), body.query,
        )
    return StartScrapingResponse(job_id=job_id)


@router.get('/{job_id}/stream')
async def stream_scraping(job_id: str, query: str, request: Request) -> StreamingResponse:
    checkpointer = request.app.state.checkpointer
    pool = request.app.state.db_pool

    # 404 guard: verify the job exists before opening the stream
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            'select id from public.scraping_jobs where id=$1',
            uuid.UUID(job_id),
        )
    if row is None:
        raise HTTPException(status_code=404, detail='Job not found')

    graph = build_graph(checkpointer=checkpointer)

    config = {
        'configurable': {
            'thread_id': job_id,
            'db_pool': pool,
        }
    }
    inputs = {'query': query, 'job_id': job_id}

    async def event_generator() -> AsyncGenerator[str, None]:
        seq = 0
        # mark running
        async with pool.acquire() as conn:
            await conn.execute(
                "update public.scraping_jobs set estado='running' where id=$1",
                uuid.UUID(job_id),
            )
        try:
            async for ev in graph.astream_events(inputs, config, version='v2'):
                if ev['event'] != 'on_custom_event':
                    continue
                name = ev['name']  # progress | property_batch | done | error | clarification
                data = ev['data']
                seq += 1
                yield f'id: {seq}\nevent: {name}\ndata: {json.dumps(data)}\n\n'
        except Exception as exc:
            seq += 1
            err = {'event': 'error', 'source': None,
                   'message': str(exc), 'recoverable': False}
            async with pool.acquire() as conn:
                await conn.execute(
                    "update public.scraping_jobs set estado='error', error_msg=$2 where id=$1",
                    uuid.UUID(job_id), str(exc),
                )
            yield f'id: {seq}\nevent: error\ndata: {json.dumps(err)}\n\n'

    return StreamingResponse(
        event_generator(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        },
    )
