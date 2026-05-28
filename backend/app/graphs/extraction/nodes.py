from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic
from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Send
from langchain_core.runnables import RunnableConfig

from app.core.config import settings
from app.models.property import (
    NormalizedProperty, ScrapingFilters,
)
from app.graphs.extraction.state import ScrapingState
from app.graphs.extraction.tools import (
    EXTRACT_FILTERS_TOOL, SYSTEM_PROMPT,
)
from app.services.apify import SOURCES, get_apify_service

MODEL = 'claude-haiku-4-5-20251001'
_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


async def parse_query(state: ScrapingState, config: RunnableConfig) -> dict[str, Any]:
    msg = await _client.messages.create(  # type: ignore[call-overload]
        model=MODEL,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        tools=[EXTRACT_FILTERS_TOOL],  # type: ignore[list-item]
        tool_choice={'type': 'tool', 'name': 'extract_search_filters'},
        messages=[{'role': 'user', 'content': state['query']}],
    )
    tool_use = next((b for b in msg.content if b.type == 'tool_use'), None)
    if tool_use is None:
        await adispatch_custom_event('clarification', {
            'event': 'clarification',
            'message': '¿Qué tipo de propiedad buscás y en qué zona?',
        })
        return {'clarification_needed': True, 'filters': None}

    filters = ScrapingFilters(**tool_use.input)
    if not filters.zona:
        await adispatch_custom_event('clarification', {
            'event': 'clarification',
            'message': 'No pude identificar la zona. ¿En qué barrio o ciudad buscás?',
        })
        return {'clarification_needed': True, 'filters': filters}
    return {'clarification_needed': False, 'filters': filters}


def route_after_parse(state: ScrapingState) -> str | list[Any]:
    if state.get('clarification_needed'):
        return 'clarification'
    # fan-out: one run_scraper branch per source, source passed via Send payload
    filters = state['filters']
    return [
        Send('run_scraper', {'__source': src, 'filters': filters,
                             'job_id': state.get('job_id')})
        for src in SOURCES
    ]


async def run_scraper(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    # CRITICAL: the source name arrives in the Send payload merged into `state`.
    source = state['__source']
    filters: ScrapingFilters = state['filters']
    service = get_apify_service()

    async def on_progress(src: str, status: str, count: int) -> None:
        await adispatch_custom_event('progress', {
            'event': 'progress', 'source': src, 'status': status,
            'count': count,
            'message': {'running': f'Buscando en {src}...',
                        'done': f'{count} propiedades en {src}',
                        'error': f'Error en {src}'}.get(status, ''),
        })

    try:
        raws = await service.scrape_source(source, filters, on_progress)
    except Exception as exc:  # graceful degradation: one source failing != fatal
        await adispatch_custom_event('error', {
            'event': 'error', 'source': source,
            'message': str(exc), 'recoverable': True,
        })
        return {'collected_properties': [], 'errors': [f'{source}: {exc}']}
    return {'collected_properties': raws}


def clarification(state: ScrapingState) -> dict[str, Any]:
    # terminal passthrough; the clarification event was already dispatched in parse_query
    return {}


def aggregate_results(state: ScrapingState) -> dict[str, Any]:
    # fan-in barrier: operator.add already merged collected_properties. No-op merge point.
    return {}


def _normalize_address(direccion: str) -> str:
    import unicodedata
    s = unicodedata.normalize('NFKD', direccion).encode('ascii', 'ignore').decode()
    return ' '.join(s.lower().split())


def normalize_properties(state: ScrapingState) -> dict[str, Any]:
    out: list[NormalizedProperty] = []
    for r in state.get('collected_properties', []):
        out.append(NormalizedProperty(
            titulo=r.titulo,
            direccion=r.direccion,
            direccion_norm=_normalize_address(r.direccion),
            precio=r.precio, moneda=r.moneda,
            tipo_operacion=r.tipo_operacion or 'venta',
            tipo_propiedad=r.tipo_propiedad or 'otro',
            ambientes=r.ambientes, m2_total=r.m2_total, m2_cubiertos=r.m2_cubiertos,
            antiguedad=r.antiguedad, amenities=r.amenities, imagenes=r.imagenes,
            fuente=r.fuente, url_origen=r.url_origen,
        ))
    return {'normalized_properties': out}


def deduplicate_properties(state: ScrapingState) -> dict:
    seen: set[tuple] = set()
    unique: list[NormalizedProperty] = []
    for p in state.get('normalized_properties', []):
        key = (p.direccion, p.precio, p.tipo_operacion)
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return {'normalized_properties': unique}


async def save_to_db(state: dict, config: RunnableConfig) -> dict:
    pool = config['configurable']['db_pool']
    job_id = state.get('job_id')
    props = state.get('normalized_properties', [])
    async with pool.acquire() as conn:
        for p in props:
            await conn.execute(
                '''insert into public.properties
                   (titulo, direccion, direccion_norm, precio, moneda, tipo_operacion,
                    tipo_propiedad, ambientes, m2_total, m2_cubiertos, antiguedad,
                    amenities, imagenes, fuente, url_origen, scraping_job_id,
                    confianza_extraccion)
                   values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                   on conflict (direccion, precio, tipo_operacion) do nothing''',
                p.titulo, p.direccion, p.direccion_norm, p.precio, p.moneda,
                p.tipo_operacion, p.tipo_propiedad, p.ambientes, p.m2_total,
                p.m2_cubiertos, p.antiguedad, p.amenities, p.imagenes, p.fuente,
                p.url_origen, job_id, p.confianza_extraccion,
            )
        await conn.execute(
            '''update public.scraping_jobs
               set estado='done', prop_count=$2, completado_at=now() where id=$1''',
            job_id, len(props),
        )
    # emit property_batch + done over SSE
    await adispatch_custom_event('property_batch', {
        'event': 'property_batch', 'source': 'all', 'count': len(props),
        'properties': [p.model_dump() for p in props],
    })
    await adispatch_custom_event('done', {
        'event': 'done', 'job_id': job_id, 'total_count': len(props),
        'sources': list(SOURCES),
    })
    return {}
