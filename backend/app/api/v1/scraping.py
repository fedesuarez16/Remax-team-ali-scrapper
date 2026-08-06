from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.graphs.extraction.graph import build_graph

router = APIRouter()

# Strong refs to in-flight graph tasks — asyncio only keeps weak references, so
# without this a run can be garbage-collected mid-stream.
_graph_tasks: set[asyncio.Task[None]] = set()


def _spawn_graph_task(coro: Any) -> None:
    task = asyncio.ensure_future(coro)
    _graph_tasks.add(task)
    task.add_done_callback(_graph_tasks.discard)


class SourceSelection(BaseModel):
    """Where to scrape, picked by the user BEFORE the search runs.

    Defaults reproduce the pre-feature behaviour exactly (every portal + the
    inmobiliarias track over all zonas), so callers that omit the field — and
    job rows persisted before the column existed — keep working untouched.

    `portales=[]` with `buscar_portales=True` means "todos los portales": an
    empty subset is no restriction, not an empty search.
    """
    buscar_portales: bool = True
    portales: list[str] = []
    buscar_inmobiliarias: bool = True
    # None/blank = todas las zonas. Otherwise only the inmobiliarias we
    # manually classified into this zona are consulted.
    zona_inmobiliarias: str | None = None


class StartScrapingRequest(BaseModel):
    query: str
    polygon: list[list[float]] | None = None
    localidades: list[str] = []
    source_selection: SourceSelection = SourceSelection()


class StartScrapingResponse(BaseModel):
    job_id: str


class ResumeScrapingRequest(BaseModel):
    selected_agency_ids: list[str]


def _sse_headers() -> dict[str, str]:
    return {'Cache-Control': 'no-cache', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no'}


def _validated_selection(selection: SourceSelection) -> dict[str, Any]:
    """Reject selections that can only produce an empty search, then hand back
    the normalized dict that gets persisted on the job row."""
    from app.services.apify import PORTAL_SOURCES

    if not selection.buscar_portales and not selection.buscar_inmobiliarias:
        raise HTTPException(
            status_code=400,
            detail='Elegí al menos una fuente: portales inmobiliarios o inmobiliarias.',
        )
    unknown = [p for p in selection.portales if p not in PORTAL_SOURCES]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f'Portales desconocidos: {", ".join(unknown)}. '
                   f'Disponibles: {", ".join(PORTAL_SOURCES)}.',
        )
    zona = (selection.zona_inmobiliarias or '').strip()
    return {
        'buscar_portales': selection.buscar_portales,
        'portales': selection.portales,
        'buscar_inmobiliarias': selection.buscar_inmobiliarias,
        'zona_inmobiliarias': zona or None,
    }


@router.post('/start', response_model=StartScrapingResponse)
async def start_scraping(body: StartScrapingRequest, request: Request) -> StartScrapingResponse:
    job_id = str(uuid.uuid4())
    source_selection = _validated_selection(body.source_selection)
    sb = request.app.state.supabase
    if sb is not None:
        try:
            await sb.table('scraping_jobs').insert({
                'id': job_id, 'query_raw': body.query, 'estado': 'pending',
                'polygon': body.polygon, 'localidades': body.localidades or None,
                'source_selection': source_selection,
            }).execute()
        except Exception as exc:
            # Without the job row every downstream FK write fails — fail loudly.
            import logging
            logging.getLogger(__name__).exception('scraping_jobs insert failed for job %s', job_id)
            raise HTTPException(status_code=500, detail=f'No se pudo crear el job: {exc}') from exc
    return StartScrapingResponse(job_id=job_id)


async def _write_job_terminal(
    sb: Any,
    job_id: str,
    estado: str,
    prop_count: int = 0,
    cost_ledger: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Close the job row. When a `cost_ledger` is handed in, its tally lands on
    the row too — including an explicit 0 for searches served from cache or from
    the direct (non-Apify) sources, since "this search was free" is the number
    that justifies the cache. NULL stays reserved for "unknown" (legacy rows)."""
    if sb is None:
        return
    from datetime import datetime, timezone
    payload: dict[str, Any] = {
        'estado': estado,
        'prop_count': prop_count,
        'completado_at': datetime.now(timezone.utc).isoformat(),
    }
    if cost_ledger is not None:
        from app.services.apify import ledger_total_usd
        payload['apify_cost_usd'] = ledger_total_usd(cost_ledger)
        payload['apify_cost_breakdown'] = cost_ledger

    try:
        await sb.table('scraping_jobs').update(payload).eq('id', job_id).execute()
    except Exception as exc:
        import logging
        log = logging.getLogger(__name__)
        if 'apify_cost_usd' not in payload:
            log.warning('job status write-back failed: %s', exc)
            return
        # Cost migration not applied yet — never lose the estado/prop_count write
        # over an optional column.
        log.warning('job cost write-back failed (%s); retrying without cost columns', exc)
        for key in ('apify_cost_usd', 'apify_cost_breakdown'):
            payload.pop(key, None)
        try:
            await sb.table('scraping_jobs').update(payload).eq('id', job_id).execute()
        except Exception as retry_exc:
            log.warning('job status write-back failed: %s', retry_exc)


def _stamp_cost(data: dict[str, Any], cost_ledger: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Attach the search's Apify spend to a `done` payload so the operator sees
    it the moment the search ends, not only later in the historial.

    Only `done` gets stamped: mid-run the tally is half-formed, and a number that
    isn't yet the search's cost is worse than no number. The value is always
    present (0.0 for a cache-served or direct-source search) — an absent field
    reads as "unknown", which is a different fact.
    """
    from app.services.apify import ledger_total_usd

    return {
        **data,
        'apify_cost_usd': ledger_total_usd(cost_ledger),
        'apify_cost_breakdown': cost_ledger,
    }


async def _run_graph_into_queue(
    graph: Any,
    inputs: Any,
    config: dict[str, Any],
    queue: asyncio.Queue[Any],
    sb: Any,
    job_id: str,
    cost_ledger: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Run astream_events in a standalone task so client disconnects don't cancel it.

    `cost_ledger` is owned by the caller: this task SPENDS (every Apify run
    started under it books itself into the dict), while the SSE generator READS
    it to close the job row. Same object, two tasks — which is why the ledger is
    passed in rather than created here."""
    from app.services.apify import use_cost_ledger

    try:
        with use_cost_ledger(cost_ledger if cost_ledger is not None else {}):
            async for ev in graph.astream_events(inputs, config, version='v2'):
                await queue.put(('event', ev))
        await queue.put(('done', None))
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception('graph run failed for job %s', job_id)
        await queue.put(('error', exc))


async def _read_job_inputs(sb: Any, job_id: str) -> dict[str, Any]:
    """Best-effort read of the persisted job row's `localidades`, `polygon` and
    `source_selection` for injection into the graph's initial `inputs`. Any
    failure (no sb, no row, chat-originated job) returns `{}` so `inputs` stays
    exactly as it was pre-change — the chat path must be byte-identical.

    Keys with no value are omitted rather than set to `None`: the graph reads
    them with `.get(...)` defaults, and a legacy row lacking `source_selection`
    must fall through to "search everything"."""
    if sb is None:
        return {}
    try:
        res = await (
            sb.table('scraping_jobs')
            .select('localidades,polygon,source_selection')
            .eq('id', job_id)
            .execute()
        )
    except Exception:
        # `source_selection` column not applied yet — retry without it so the
        # polygon/localidades injection (already in production) keeps working.
        try:
            res = await sb.table('scraping_jobs').select('localidades,polygon').eq('id', job_id).execute()
        except Exception:
            return {}
    if not res.data:
        return {}
    row = res.data[0]
    return {k: row[k] for k in ('localidades', 'polygon', 'source_selection') if row.get(k)}


@router.get('/{job_id}/stream')
async def stream_scraping(job_id: str, query: str, request: Request) -> StreamingResponse:
    checkpointer = request.app.state.checkpointer
    sb = request.app.state.supabase
    graph = build_graph(checkpointer=checkpointer)
    config = {'configurable': {'thread_id': job_id, 'supabase': sb}}
    inputs: dict[str, Any] = {'query': query, 'job_id': job_id}
    inputs.update(await _read_job_inputs(sb, job_id))

    queue: asyncio.Queue[Any] = asyncio.Queue()
    cost_ledger: dict[str, dict[str, Any]] = {}
    _spawn_graph_task(_run_graph_into_queue(graph, inputs, config, queue, sb, job_id, cost_ledger))

    async def event_generator() -> AsyncGenerator[str, None]:
        seq = 0
        try:
            while True:
                kind, payload = await queue.get()
                if kind == 'done':
                    break
                if kind == 'error':
                    raise payload
                ev = payload
                if ev['event'] != 'on_custom_event':
                    continue
                name = ev['name']
                data = ev['data']
                if name == 'done':
                    await _write_job_terminal(sb, job_id, 'done', data.get('total_count', 0), cost_ledger)
                    data = _stamp_cost(data, cost_ledger)
                elif name == 'error' and not data.get('recoverable', True):
                    await _write_job_terminal(sb, job_id, 'error', 0, cost_ledger)
                seq += 1
                yield f'id: {seq}\nevent: {name}\ndata: {json.dumps(data)}\n\n'
        except GeneratorExit:
            # Client disconnected — graph task keeps running and will save the checkpoint
            return
        except Exception as exc:
            await _write_job_terminal(sb, job_id, 'error', 0, cost_ledger)
            seq += 1
            yield f'id: {seq}\nevent: error\ndata: {json.dumps({"event":"error","message":str(exc),"recoverable":False})}\n\n'

    return StreamingResponse(event_generator(), media_type='text/event-stream', headers=_sse_headers())


async def _seed_cost_ledger(sb: Any, job_id: str) -> dict[str, dict[str, Any]]:
    """Resume is a SEPARATE request with a fresh ledger — starting it empty would
    make the terminal write overwrite (and so lose) what the original run spent.
    Seed it from what the row already booked so the total keeps accumulating
    across every resume round. Best-effort: an unreadable row just starts at 0."""
    if sb is None:
        return {}
    try:
        res = await sb.table('scraping_jobs').select('apify_cost_breakdown').eq('id', job_id).execute()
    except Exception:
        return {}
    if not res.data:
        return {}
    booked = res.data[0].get('apify_cost_breakdown')
    return dict(booked) if isinstance(booked, dict) else {}


@router.post('/{job_id}/resume')
async def resume_scraping(job_id: str, body: ResumeScrapingRequest, request: Request) -> StreamingResponse:
    from langgraph.types import Command
    checkpointer = request.app.state.checkpointer
    sb = request.app.state.supabase
    graph = build_graph(checkpointer=checkpointer)
    config = {'configurable': {'thread_id': job_id, 'supabase': sb}}

    queue: asyncio.Queue[Any] = asyncio.Queue()
    cost_ledger = await _seed_cost_ledger(sb, job_id)
    _spawn_graph_task(
        _run_graph_into_queue(
            graph, Command(resume=body.selected_agency_ids), config, queue, sb, job_id, cost_ledger,
        )
    )

    async def event_generator() -> AsyncGenerator[str, None]:
        seq = 0
        try:
            while True:
                kind, payload = await queue.get()
                if kind == 'done':
                    break
                if kind == 'error':
                    raise payload
                ev = payload
                if ev['event'] != 'on_custom_event':
                    continue
                name = ev['name']
                data = ev['data']
                if name == 'done':
                    await _write_job_terminal(sb, job_id, 'done', data.get('total_count', 0), cost_ledger)
                    data = _stamp_cost(data, cost_ledger)
                elif name == 'error' and not data.get('recoverable', True):
                    await _write_job_terminal(sb, job_id, 'error', 0, cost_ledger)
                seq += 1
                yield f'id: {seq}\nevent: {name}\ndata: {json.dumps(data)}\n\n'
        except GeneratorExit:
            return
        except Exception as exc:
            await _write_job_terminal(sb, job_id, 'error', 0, cost_ledger)
            seq += 1
            yield f'id: {seq}\nevent: error\ndata: {json.dumps({"event":"error","message":str(exc),"recoverable":False})}\n\n'

    return StreamingResponse(event_generator(), media_type='text/event-stream', headers=_sse_headers())


def _classify_properties(
    properties: list[dict[str, Any]], polygon: list[Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
    """Tag each row `in_polygon` (True/False/None-for-ungeocoded) and compute
    per-bucket counts. Rows are never dropped — outside-polygon rows stay in
    the payload for map dimming. Falls back to `(properties, None)` (no
    classification, chat-like/backward-compat shape) when the polygon is
    missing/empty/malformed (`point_in_polygon`'s own MIN_POLYGON_POINTS gate
    covers malformed, but we short-circuit here to skip tagging entirely)."""
    from app.services.polygon import MIN_POLYGON_POINTS, point_in_polygon

    if not polygon or len(polygon) < MIN_POLYGON_POINTS:
        return properties, None

    inside = outside = ungeocoded = 0
    tagged: list[dict[str, Any]] = []
    for row in properties:
        lat, lng = row.get('lat'), row.get('lng')
        if lat is None or lng is None:
            row = {**row, 'in_polygon': None}
            ungeocoded += 1
        elif point_in_polygon(float(lat), float(lng), polygon):
            row = {**row, 'in_polygon': True}
            inside += 1
        else:
            row = {**row, 'in_polygon': False}
            outside += 1
        tagged.append(row)

    counts = {'inside': inside, 'outside': outside, 'ungeocoded': ungeocoded, 'total': len(tagged)}
    return tagged, counts


@router.get('/{job_id}/properties')
async def get_job_properties(job_id: str, request: Request) -> dict[str, Any]:
    import logging
    log = logging.getLogger(__name__)

    sb = request.app.state.supabase
    if sb is None:
        return {'properties': [], 'polygon': None, 'counts': None}

    job_res = await sb.table('scraping_jobs').select('id,query_raw,polygon').eq('id', job_id).execute()
    if not job_res.data:
        raise HTTPException(status_code=404, detail='Job not found')
    query_raw = job_res.data[0].get('query_raw')
    polygon = job_res.data[0].get('polygon')

    properties: list[dict[str, Any]] = []
    try:
        try:
            join_res = await sb.table('search_property_results').select(
                'property_id,matches_criteria,properties(*)'
            ).eq('job_id', job_id).execute()
        except Exception:
            # matches_criteria column missing (migration not applied) — links
            # still exist, fetch them without the flag.
            join_res = await sb.table('search_property_results').select(
                'property_id,properties(*)'
            ).eq('job_id', job_id).execute()
        for row in (join_res.data or []):
            prop = row.get('properties')
            if prop:
                prop['matches_criteria'] = row.get('matches_criteria') is not False
                properties.append(prop)
    except Exception as exc:
        log.warning('search_property_results unavailable (%s) — falling back to scraping_job_id lookup', exc)

    # Fallback: table missing or empty → return all properties scraped in this job
    if not properties:
        fallback = await sb.table('properties').select('*').eq('scraping_job_id', job_id).execute()
        properties = fallback.data or []

    # Rank by relevance to the original query so the best matches surface first.
    if query_raw and properties:
        try:
            from app.services.matcher import rank_properties
            properties = await rank_properties(query_raw, properties)
        except Exception as exc:
            log.warning('match ranking failed (%s) — returning unranked properties', exc)

    # Criteria-matching props always lead; stable sort keeps rank order within
    # each group. Rows without the flag (legacy links) count as matching.
    properties.sort(key=lambda p: p.get('matches_criteria', True) is False)

    properties, counts = _classify_properties(properties, polygon)
    return {'properties': properties, 'polygon': polygon, 'counts': counts}
