from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.runnables import RunnableConfig
from langgraph.types import Send, interrupt

from app.core.config import settings
from app.models.property import NormalizedProperty, ScrapingFilters
from app.graphs.extraction.state import ScrapingState
from app.graphs.extraction.tools import EXTRACT_FILTERS_TOOL, SYSTEM_PROMPT
from app.services.apify import PORTAL_SOURCES, get_apify_service

MODEL = 'claude-haiku-4-5-20251001'
_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


# ── Phase 1: Parse & portal scraping ──────────────────────────────────────────

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
        }, config=config)
        return {'clarification_needed': True, 'filters': None}

    filters = ScrapingFilters(**tool_use.input)
    if not filters.zona:
        await adispatch_custom_event('clarification', {
            'event': 'clarification',
            'message': 'No pude identificar la zona. ¿En qué barrio o ciudad buscás?',
        }, config=config)
        return {'clarification_needed': True, 'filters': filters}
    return {'clarification_needed': False, 'filters': filters}


def route_after_parse(state: ScrapingState) -> str | list[Any]:
    if state.get('clarification_needed'):
        return 'clarification'
    filters = state['filters']
    job_id = state.get('job_id')
    # Fan-out: portal scrapers only (Google Maps runs separately)
    sends: list[Any] = [
        Send('run_portal_scraper', {'__source': src, 'filters': filters, 'job_id': job_id})
        for src in PORTAL_SOURCES
    ]
    # Also discover agencies in parallel
    sends.append(Send('discover_agencies', {'filters': filters, 'job_id': job_id}))
    return sends


def clarification(state: ScrapingState) -> dict[str, Any]:
    return {}


async def run_portal_scraper(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    source = state['__source']
    filters: ScrapingFilters = state['filters']
    service = get_apify_service()

    async def on_progress(src: str, status: str, count: int) -> None:
        await adispatch_custom_event('progress', {
            'event': 'progress', 'source': src, 'status': status, 'count': count,
            'message': {
                'running': f'Buscando en {src}...',
                'done': f'{count} propiedades en {src}',
                'error': f'Error en {src}',
            }.get(status, ''),
        }, config=config)

    try:
        raws = await service.scrape_source(source, filters, on_progress)
    except Exception as exc:
        await adispatch_custom_event('error', {
            'event': 'error', 'source': source, 'message': str(exc), 'recoverable': True,
        }, config=config)
        return {'collected_properties': [], 'errors': [f'{source}: {exc}']}
    return {'collected_properties': raws}


async def discover_agencies(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    filters: ScrapingFilters = state['filters']
    zona = filters.zona or 'Buenos Aires'
    service = get_apify_service()

    async def on_progress(src: str, status: str, count: int) -> None:
        await adispatch_custom_event('progress', {
            'event': 'progress', 'source': 'googlemaps', 'status': status, 'count': count,
            'message': {
                'running': f'Buscando inmobiliarias en {zona}...',
                'done': f'{count} inmobiliarias encontradas',
                'error': 'Error buscando inmobiliarias',
            }.get(status, ''),
        }, config=config)

    try:
        agencies = await service.scrape_agencies(zona, on_progress)
    except Exception as exc:
        await adispatch_custom_event('error', {
            'event': 'error', 'source': 'googlemaps', 'message': str(exc), 'recoverable': True,
        }, config=config)
        return {'agencies': [], 'errors': [f'googlemaps: {exc}']}
    return {'agencies': agencies}


def aggregate_phase1(state: ScrapingState) -> dict[str, Any]:
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


def deduplicate_properties(state: ScrapingState) -> dict[str, Any]:
    seen: set[tuple[Any, ...]] = set()
    unique: list[NormalizedProperty] = []
    for p in state.get('normalized_properties', []):
        key = (p.direccion, p.precio, p.tipo_operacion)
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return {'normalized_properties': unique}


async def save_portal_properties(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    pool = config['configurable'].get('db_pool')
    job_id = state.get('job_id')
    props: list[NormalizedProperty] = state.get('normalized_properties', [])

    if pool is not None:
        async with pool.acquire() as conn:
            for p in props:
                await conn.execute(
                    '''insert into public.properties
                       (titulo, direccion, direccion_norm, precio, moneda, tipo_operacion,
                        tipo_propiedad, ambientes, m2_total, m2_cubiertos, antiguedad,
                        amenities, imagenes, fuente, url_origen, scraping_job_id, confianza_extraccion)
                       values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                       on conflict (direccion, precio, tipo_operacion) do nothing''',
                    p.titulo, p.direccion, p.direccion_norm, p.precio, p.moneda,
                    p.tipo_operacion, p.tipo_propiedad, p.ambientes, p.m2_total,
                    p.m2_cubiertos, p.antiguedad, p.amenities, p.imagenes,
                    p.fuente, p.url_origen, job_id, p.confianza_extraccion,
                )

    # Emit partial results — portales done, waiting for agency review
    await adispatch_custom_event('property_batch', {
        'event': 'property_batch', 'source': 'portales', 'count': len(props),
        'properties': [p.model_dump() for p in props],
    }, config=config)
    return {}


# ── Phase 1 → Phase 2 bridge: agency review interrupt ─────────────────────────

async def review_agencies(state: ScrapingState, config: RunnableConfig) -> dict[str, Any]:
    agencies = state.get('agencies', [])

    if not agencies:
        # No agencies found → skip Instagram, emit done
        await adispatch_custom_event('done', {
            'event': 'done',
            'job_id': state.get('job_id'),
            'total_count': len(state.get('normalized_properties', [])),
            'sources': list(PORTAL_SOURCES),
        }, config=config)
        return {'selected_agency_ids': []}

    await adispatch_custom_event('agencies_review', {
        'event': 'agencies_review',
        'agencies': [a.model_dump() for a in agencies],
        'message': f'Encontré {len(agencies)} inmobiliarias locales. Seleccioná las que querés incluir para buscar propiedades en sus sitios web.',
    }, config=config)

    # INTERRUPT — graph pauses here, resumes when user sends selected_agency_ids
    selected: list[str] = interrupt({'type': 'agency_selection'})
    return {'selected_agency_ids': selected}


def route_after_review(state: ScrapingState) -> str | list[Any]:
    selected = state.get('selected_agency_ids', [])
    agencies = state.get('agencies', [])
    if not selected or not agencies:
        return 'no_instagram'

    # Build a map of agency id → agency
    agency_map = {a.id: a for a in agencies}
    selected_agencies = [agency_map[aid] for aid in selected if aid in agency_map]
    # Fan-out: one website scraper per selected agency that has a website
    job_id = state.get('job_id')
    agency_map = {a.id: a for a in agencies}
    selected_agencies = [agency_map[aid] for aid in selected if aid in agency_map]
    websites = [(a.nombre, a.sitio_web) for a in selected_agencies if a.sitio_web]

    if not websites:
        return 'no_websites'

    return [
        Send('run_website_scraper', {'nombre': nombre, 'url': url, 'job_id': job_id})
        for nombre, url in websites
    ]


# ── Phase 2: Website scraping + LLM extraction ───────────────────────────────

async def run_website_scraper(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    url: str = state['url']
    nombre: str = state.get('nombre', url)
    service = get_apify_service()
    label = url.replace('https://', '').replace('http://', '').split('/')[0]

    async def on_progress(src: str, status: str, count: int) -> None:
        await adispatch_custom_event('progress', {
            'event': 'progress', 'source': f'web:{label}',
            'status': status, 'count': count,
            'message': {
                'running': f'Buscando propiedades en {nombre}...',
                'done': f'{count} páginas escaneadas en {nombre}',
                'error': f'Error en {nombre}',
            }.get(status, ''),
        }, config=config)

    try:
        pages = await service.scrape_website(url, on_progress)
    except Exception as exc:
        await adispatch_custom_event('error', {
            'event': 'error', 'source': f'web:{label}',
            'message': str(exc), 'recoverable': True,
        }, config=config)
        return {'website_pages': [], 'errors': [f'{url}: {exc}']}
    return {'website_pages': pages}


_WEBSITE_EXTRACT_TOOL = {
    'name': 'extract_properties_from_webpage',
    'description': 'Extrae todas las propiedades inmobiliarias listadas en el texto de una página web de una inmobiliaria argentina.',
    'input_schema': {
        'type': 'object',
        'properties': {
            'propiedades': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'titulo': {'type': ['string', 'null']},
                        'precio': {'type': ['number', 'null']},
                        'moneda': {'type': ['string', 'null'], 'enum': ['USD', 'ARS', None]},
                        'tipo_operacion': {'type': ['string', 'null'], 'enum': ['venta', 'alquiler', None]},
                        'tipo_propiedad': {'type': ['string', 'null'], 'enum': ['departamento', 'casa', 'ph', 'local', 'oficina', 'terreno', 'otro', None]},
                        'ambientes': {'type': ['integer', 'null']},
                        'm2': {'type': ['number', 'null']},
                        'direccion': {'type': ['string', 'null']},
                        'descripcion': {'type': ['string', 'null']},
                    },
                },
            },
        },
        'required': ['propiedades'],
    },
}

_WEBSITE_SYSTEM_PROMPT = (
    'Sos un extractor de propiedades inmobiliarias de páginas web argentinas. '
    'Dado el texto de una página, extraés TODAS las propiedades que aparezcan listadas. '
    'Si no hay propiedades en la página, devolvé propiedades=[]. '
    'Extraé datos de precios, ambientes, m², tipo y dirección cuando estén disponibles.'
)


async def extract_website_properties_llm(state: ScrapingState, config: RunnableConfig) -> dict[str, Any]:
    pages: list[dict[str, str]] = state.get('website_pages', [])
    results: list[NormalizedProperty] = []

    for page in pages:
        text = page.get('text', '')
        if not text or len(text) < 100:
            continue
        # Truncate very long pages — Claude context limit
        text = text[:6000]
        try:
            msg = await _client.messages.create(  # type: ignore[call-overload]
                model=MODEL,
                max_tokens=1024,
                system=_WEBSITE_SYSTEM_PROMPT,
                tools=[_WEBSITE_EXTRACT_TOOL],  # type: ignore[list-item]
                tool_choice={'type': 'tool', 'name': 'extract_properties_from_webpage'},
                messages=[{'role': 'user', 'content': f'Página: {page.get("url", "")}\n\n{text}'}],
            )
        except Exception:
            continue

        tool_use = next((b for b in msg.content if b.type == 'tool_use'), None)
        if not tool_use:
            continue

        for prop in (tool_use.input.get('propiedades') or []):
            filled = sum(1 for f in ['precio', 'tipo_operacion', 'tipo_propiedad', 'ambientes', 'm2', 'direccion']
                         if prop.get(f) is not None)
            confianza = min(1.0, filled / 6)
            results.append(NormalizedProperty(
                titulo=prop.get('titulo') or prop.get('descripcion', ''),
                direccion=prop.get('direccion') or '',
                direccion_norm=_normalize_address(prop.get('direccion') or ''),
                precio=prop.get('precio'),
                moneda=prop.get('moneda') or 'USD',  # type: ignore[arg-type]
                tipo_operacion=prop.get('tipo_operacion') or 'venta',  # type: ignore[arg-type]
                tipo_propiedad=prop.get('tipo_propiedad') or 'otro',  # type: ignore[arg-type]
                ambientes=prop.get('ambientes'),
                m2_total=prop.get('m2'),
                fuente='googlemaps',
                url_origen=page.get('url'),
                confianza_extraccion=confianza,
            ))

    return {'website_properties': results}


async def save_website_properties(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    pool = config['configurable'].get('db_pool')
    job_id = state.get('job_id')
    props: list[NormalizedProperty] = state.get('website_properties', [])

    if pool is not None:
        async with pool.acquire() as conn:
            for p in props:
                await conn.execute(
                    '''insert into public.properties
                       (titulo, direccion, direccion_norm, precio, moneda, tipo_operacion,
                        tipo_propiedad, ambientes, m2_total, amenities, imagenes,
                        fuente, url_origen, scraping_job_id, confianza_extraccion)
                       values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                       on conflict (direccion, precio, tipo_operacion) do nothing''',
                    p.titulo, p.direccion, p.direccion_norm, p.precio, p.moneda,
                    p.tipo_operacion, p.tipo_propiedad, p.ambientes, p.m2_total,
                    p.amenities, p.imagenes, p.fuente, p.url_origen, job_id, p.confianza_extraccion,
                )

    portal_count = len(state.get('normalized_properties', []))
    total = portal_count + len(props)

    if props:
        await adispatch_custom_event('property_batch', {
            'event': 'property_batch', 'source': 'local', 'count': len(props),
            'properties': [p.model_dump() for p in props],
        }, config=config)

    await adispatch_custom_event('done', {
        'event': 'done', 'job_id': job_id, 'total_count': total,
        'sources': [*list(PORTAL_SOURCES), 'local'],
    }, config=config)
    return {}


async def no_websites(state: ScrapingState, config: RunnableConfig) -> dict[str, Any]:
    await adispatch_custom_event('agent_message', {
        'event': 'agent_message',
        'message': 'Las inmobiliarias seleccionadas no tienen sitio web registrado.',
    }, config=config)
    await adispatch_custom_event('done', {
        'event': 'done',
        'job_id': state.get('job_id'),
        'total_count': len(state.get('normalized_properties', [])),
        'sources': list(PORTAL_SOURCES),
    }, config=config)
    return {}
