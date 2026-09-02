from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from typing import Any

from anthropic import AsyncAnthropic
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.runnables import RunnableConfig
from langgraph.types import Send, interrupt

from app.core.config import settings
from app.core.database import chunk_for_in_filter
from app.models.property import Agency, NormalizedProperty, ScrapingFilters
from app.graphs.extraction.state import ScrapingState
from app.graphs.extraction.tools import (
    EXTRACT_FILTERS_TOOL, INSTAGRAM_EXTRACT_TOOL, INSTAGRAM_SYSTEM_PROMPT, SYSTEM_PROMPT,
)
from app.services.apify import (
    PORTAL_SOURCES,
    ApifyBudgetExceeded,
    agency_matches_zona,
    get_apify_service,
    harvest_page_images,
)
from app.services.dedup import collapse_duplicates
from app.services.llm_costs import (
    SCOPE_EXTRACT_INSTAGRAM,
    SCOPE_EXTRACT_WEBSITE,
    SCOPE_SEARCH_PARSE,
    llm_budget_exhausted,
    record_llm_usage,
)
from app.services.zona import (
    address_fingerprint as _address_fingerprint,
    normalize_address as _normalize_address,
    normalize_zona as _normalize_zona,
)

MODEL = 'claude-haiku-4-5-20251001'
_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

_log = logging.getLogger(__name__)


# ── Fan-out de inmobiliarias: cola + contador compartido ──────────────────────
#
# LangGraph despacha los N `Send` de `route_after_review` de un saque. Con 260
# inmobiliarias tildadas eso son 260 scrapers simultáneos, cada uno abriendo
# hasta 6 conexiones: el proceso se queda sin sockets y la búsqueda muere. El
# semáforo los pone en fila sin descartar ninguno.
#
# El contador vive acá y no en el estado del grafo porque las ramas de un
# fan-out NO comparten estado hasta el fan-in: una rama no puede saber cuántas
# hermanas terminaron. Es un dict de proceso, igual que `_graph_tasks` en la
# capa de API, y se limpia solo cuando el job llega al total.
_website_semaphore: asyncio.Semaphore | None = None
# Instagram tiene el suyo aparte: cada unidad ahí es un RUN DE APIFY que pollea
# hasta 300 s, no un GET. Compartir el tope con los sitios web dejaría a un
# perfil lento ocupando un lugar que le hace falta a un sitio.
_instagram_semaphore: asyncio.Semaphore | None = None
_website_progress: dict[str, dict[str, int]] = {}

# Jobs que ya avisaron que se quedaron sin presupuesto de Apify. Cuando el tope
# corta, corta para TODAS las ramas del fan-out a la vez: sin este registro una
# zona con 50 inmobiliarias mandaría 50 avisos idénticos por el mismo motivo.
# Mismo criterio que `_website_progress` — de proceso, porque las ramas no
# comparten estado hasta el fan-in.
_budget_notified: set[str] = set()
# Acotado a mano: acá no hay evento de "job terminado" del que colgar la
# limpieza. Al llenarse se vacía entero; el peor caso es un aviso repetido en
# un job viejo que siga vivo, que es una burbuja de más, no un error.
_BUDGET_NOTICE_MEMORY = 512


def _claim_budget_notice(job_id: str | None, kind: str = 'apify') -> bool:
    """True sólo para la PRIMERA rama de este job que se queda sin presupuesto.

    `kind` distingue quedarse sin créditos de Apify de quedarse sin tokens de
    Anthropic: son dos hechos distintos y el operador necesita enterarse de los
    dos. Con la llave sólo en el job, el primero silenciaba al segundo.
    """
    key = f'{kind}:{job_id or ""}'
    if key in _budget_notified:
        return False
    if len(_budget_notified) >= _BUDGET_NOTICE_MEMORY:
        _budget_notified.clear()
    _budget_notified.add(key)
    return True


async def _announce_budget_stop(
    exc: Exception, job_id: str | None, config: RunnableConfig,
) -> None:
    """Avisa el corte por tope como `progress`, no como `error`.

    La búsqueda no falló: gastó lo que se le dijo que podía gastar y sigue con
    lo que ya juntó. Los números viajan en el mensaje de la excepción porque
    "se agotó el presupuesto" a secas no le dice al usuario si subir el tope o
    achicar la zona.
    """
    if not _claim_budget_notice(job_id, 'apify'):
        return
    await adispatch_custom_event('progress', {
        'event': 'progress', 'source': 'apify_budget', 'status': 'done', 'count': 0,
        'message': f'Tope de gasto alcanzado ({exc}). Sigo con lo que encontré hasta acá.',
    }, config=config)


async def _announce_llm_budget_stop(job_id: str | None, config: RunnableConfig) -> None:
    """Avisa que se acabó el presupuesto de tokens, una vez por búsqueda.

    Como `progress` y no `error` por lo mismo que el de Apify: la búsqueda
    gastó lo que se le dijo que podía gastar. Lo extraído hasta acá ya está en
    la base — cada página se persiste apenas sale — así que el corte no
    descarta nada de lo que se pagó.
    """
    if not _claim_budget_notice(job_id, 'llm'):
        return
    cap = settings.LLM_MAX_USD_PER_SEARCH
    await adispatch_custom_event('progress', {
        'event': 'progress', 'source': 'llm_budget', 'status': 'done', 'count': 0,
        'message': (
            f'Alcancé el tope de USD {cap} en análisis con IA. '
            'Dejo de analizar páginas nuevas y te muestro lo que ya extraje.'
        ),
    }, config=config)


def _get_website_semaphore() -> asyncio.Semaphore:
    global _website_semaphore
    if _website_semaphore is None:
        _website_semaphore = asyncio.Semaphore(max(1, settings.WEBSITE_SCRAPE_CONCURRENCY))
    return _website_semaphore


def _get_instagram_semaphore() -> asyncio.Semaphore:
    global _instagram_semaphore
    if _instagram_semaphore is None:
        _instagram_semaphore = asyncio.Semaphore(max(1, settings.INSTAGRAM_SCRAPE_CONCURRENCY))
    return _instagram_semaphore


def _reset_website_progress(job_id: str | None, total: int) -> None:
    """Arranca (o reinicia) el contador del fan-out. `total=0` lo borra: sin
    total no hay agregado y cada sitio vuelve a reportarse solo."""
    if not job_id:
        return
    if total > 0:
        _website_progress[job_id] = {'total': total, 'done': 0, 'announced': 0}
    else:
        _website_progress.pop(job_id, None)


def _website_progress_total(job_id: str | None) -> int:
    entry = _website_progress.get(job_id or '')
    return entry['total'] if entry else 0


def _claim_website_announcement(job_id: str | None) -> int:
    """El primer scraper del fan-out se queda con el derecho a anunciar el
    `0 de N` inicial; los demás reciben 0. Sin `await` en el medio, así que en
    un loop de asyncio esto es atómico."""
    entry = _website_progress.get(job_id or '')
    if entry is None or entry['announced']:
        return 0
    entry['announced'] = 1
    return entry['total']


def _bump_website_progress(job_id: str | None) -> tuple[int, int]:
    """Marca un sitio como terminado (haya traído páginas o haya fallado) y
    devuelve `(hechos, total)`. `(0, 0)` = este job no tiene agregado."""
    entry = _website_progress.get(job_id or '')
    if entry is None:
        return 0, 0
    entry['done'] += 1
    done, total = entry['done'], entry['total']
    if done >= total:
        _website_progress.pop(job_id or '', None)
    return done, total


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
    # Booked before the tool_use check on purpose: a query too vague to parse still
    # burned tokens, and the clarification branches below return early.
    await record_llm_usage(
        config['configurable'].get('supabase'),
        scope=SCOPE_SEARCH_PARSE,
        model=MODEL,
        usage=getattr(msg, 'usage', None),
        job_id=state.get('job_id'),
    )

    tool_use = next((b for b in msg.content if b.type == 'tool_use'), None)
    if tool_use is None:
        await adispatch_custom_event('clarification', {
            'event': 'clarification',
            'message': '¿Qué tipo de propiedad buscás y en qué zona?',
        }, config=config)
        return {'clarification_needed': True, 'filters': None}

    filters = ScrapingFilters(**tool_use.input)
    if not filters.zonas and filters.zona:
        filters.zonas = [filters.zona]
    if not filters.zonas:
        await adispatch_custom_event('clarification', {
            'event': 'clarification',
            'message': 'No pude identificar la zona. ¿En qué barrio o ciudad buscás?',
        }, config=config)
        return {'clarification_needed': True, 'filters': filters}
    return {'clarification_needed': False, 'filters': filters}


def _read_selection(state: ScrapingState) -> dict[str, Any]:
    """Normalize the user's pre-search source pick (`POST /scraping/start` →
    `source_selection` on the job row → graph `inputs`).

    An absent key means "search everything", which is what every caller did
    before the selector existed — so legacy job rows and the map flow behave
    exactly as they always did."""
    sel = state.get('source_selection') or {}
    zona = (sel.get('zona_inmobiliarias') or '').strip()
    return {
        'buscar_portales': bool(sel.get('buscar_portales', True)),
        'portales': [str(p) for p in (sel.get('portales') or [])],
        'buscar_inmobiliarias': bool(sel.get('buscar_inmobiliarias', True)),
        'zona_inmobiliarias': zona or None,
        'solo_fuentes_cargadas': bool(sel.get('solo_fuentes_cargadas', False)),
    }


def _hay_que_descubrir_agencias(selection: dict[str, Any]) -> bool:
    """¿Sale Google Maps a buscar inmobiliarias nuevas?

    El descubrimiento es lo que llena la búsqueda de inmobiliarias que nadie
    eligió — 390 en una zona — con todo lo que cuesta scrapearlas y
    analizarlas. Se apaga en tres casos, y conviene que estén juntos y con
    nombre:

    - no se buscan inmobiliarias en absoluto;
    - `solo_fuentes_cargadas`: el operador pidió su registro y nada más;
    - hay una `zona_inmobiliarias` elegida, que ya significaba "consultá sólo
      lo que clasifiqué en esa zona". Ese caso funcionaba de rebote, como
      efecto secundario de un flag que hacía dos cosas — por eso no había forma
      de pedir "sólo las cargadas, en cualquier zona".
    """
    if not selection['buscar_inmobiliarias']:
        return False
    if selection['solo_fuentes_cargadas']:
        return False
    return not selection['zona_inmobiliarias']


def _env_allowed_sources() -> tuple[str, ...]:
    """Portals this deployment is allowed to hit at all, before the user's pick."""
    if settings.APIFY_DISABLED:
        # Everything that talks to a portal over plain httpx. ZonaProp belongs
        # here now that it reads `__PRELOADED_STATE__` itself — and especially
        # here: this flag exists for when Apify is down or out of credits,
        # which is the very situation that motivated moving it off the actor.
        # Back through the actor (`ZONAPROP_USE_APIFY`), it is an Apify source
        # again and stays out.
        direct = ['mercadolibre', 'inmobusqueda', 'mudafy', 'century21']
        if not settings.ZONAPROP_USE_APIFY:
            direct.insert(0, 'zonaprop')
        return tuple(direct)
    if settings.SCRAPE_GOOGLEMAPS_ONLY:
        return ()
    if settings.SCRAPE_ZONAPROP_ONLY:
        return ('zonaprop',)
    return PORTAL_SOURCES


def route_after_parse(state: ScrapingState) -> str | list[Any]:
    if state.get('clarification_needed'):
        return 'clarification'
    filters = state['filters']
    job_id = state.get('job_id')
    localidades = state.get('localidades') or []
    # Fan-out unit: localidad when present (polygon search — portal-known slug,
    # ADR-1), else per-barrio exactly as before (chat path / legacy callers).
    fanout_units = localidades or filters.zonas or ([filters.zona] if filters.zona else [])

    # The user's pre-search pick narrows what the deployment already allows —
    # env gates are a hard ceiling, the selection can only subtract from it.
    selection = _read_selection(state)
    sources: tuple[str, ...] = _env_allowed_sources() if selection['buscar_portales'] else ()
    if picked := selection['portales']:
        sources = tuple(s for s in sources if s in set(picked))
    buscar_inmobiliarias = selection['buscar_inmobiliarias']
    # A zona-scoped run consults ONLY the inmobiliarias we filed under that
    # zona, so Google-Maps discovery (which surfaces agencies belonging to no
    # curated zona) is skipped entirely — the curated registry, fetched in
    # `review_agencies`, becomes the single inmobiliaria source. "Todas las
    # zonas" keeps discovery on: it's the broadest search, unchanged from
    # before the selector existed.
    descubrir_agencias = _hay_que_descubrir_agencias(selection)

    # Fan-out: one portal-scraper + agency-discovery branch per (unit × source)
    sends: list[Any] = []
    for unit in fanout_units:
        if localidades:
            # localidad branch: zona=localidad (drives URL slug + ML q), keep the
            # full original barrio list on `zonas` for the guard's phrase-set
            # (ADR-1: set(zonas) | set(localidades) | {zona}) — `localidades` is
            # scoped to THIS branch's own localidad only (spec Open Question 2:
            # no cross-localidad guard leakage in multi-localidad polygons).
            zfilters = filters.model_copy(update={'zona': unit, 'localidades': [unit]})
        else:
            zfilters = filters.model_copy(update={'zona': unit})
        for src in sources:
            sends.append(Send('run_portal_scraper', {'__source': src, 'filters': zfilters, 'job_id': job_id}))
        if descubrir_agencias and not settings.SCRAPE_ZONAPROP_ONLY and not settings.APIFY_DISABLED:
            sends.append(Send('discover_agencies', {'filters': zfilters, 'job_id': job_id}))
    if sends:
        return sends

    if buscar_inmobiliarias:
        # Nothing to scrape in phase 1 (zona-scoped inmobiliarias, no portales),
        # but the curated registry is only read downstream in `review_agencies`.
        # Pass through the aggregation chain so the graph actually gets there
        # instead of terminating on an empty fan-out.
        return [Send('aggregate_phase1', {'job_id': job_id})]

    # Everything the user picked is unavailable here (e.g. a portal this
    # deployment gates off, with inmobiliarias unchecked). Route to a terminal
    # node instead of returning an empty Send list, which would leave the SSE
    # stream hanging without a `done`.
    return 'no_sources'


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
    zona_norm = _normalize_zona(zona)
    sb = config['configurable'].get('supabase')
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

    # ── Cache read (read-through) ──────────────────────────────────────────────
    cached = await _read_cached_agencies(sb, zona_norm, zona)
    if len(cached) >= _AGENCY_CACHE_MIN_ROWS:
        await on_progress('googlemaps', 'running', 0)
        await on_progress('googlemaps', 'done', len(cached))
        return {'agencies': cached}

    # ── Cache miss → pay Apify (as today) ────────────────────────────────────
    try:
        agencies = await service.scrape_agencies(zona, on_progress)
    except ApifyBudgetExceeded as exc:
        await _announce_budget_stop(exc, state.get('job_id'), config)
        return {'agencies': []}
    except Exception as exc:
        await adispatch_custom_event('error', {
            'event': 'error', 'source': 'googlemaps', 'message': str(exc), 'recoverable': True,
        }, config=config)
        return {'agencies': [], 'errors': [f'googlemaps: {exc}']}

    # ── Write-behind (awaited) then adopt DB ids so selection round-trips ────
    try:
        await _upsert_agencies(sb, agencies, zona_norm)
        fresh = await _read_cached_agencies(sb, zona_norm, zona)
        if fresh:
            agencies = fresh
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning('_upsert_agencies failed: %s', exc)

    return {'agencies': agencies}


def aggregate_phase1(state: ScrapingState) -> dict[str, Any]:
    return {}


_VALID_TIPOS = {'departamento', 'casa', 'ph', 'local', 'oficina', 'terreno', 'otro'}
_TIPO_PROP_ALIASES: dict[str, str] = {
    'piso': 'departamento',
    'apartment': 'departamento',
    'departament': 'departamento',
    'depto': 'departamento',
    'dpto': 'departamento',
    'flat': 'departamento',
    'house': 'casa',
    'vivienda': 'casa',
    'chalet': 'casa',
    'lote': 'terreno',
    'comercial': 'local',
    'shop': 'local',
}


def _normalize_tipo_propiedad(valor: str | None) -> str:
    if not valor:
        return 'otro'
    v = valor.strip().lower()
    if v in _VALID_TIPOS:
        return v
    return _TIPO_PROP_ALIASES.get(v, 'otro')


def normalize_properties(state: ScrapingState) -> dict[str, Any]:
    out: list[NormalizedProperty] = []
    for r in state.get('collected_properties', []):
        out.append(NormalizedProperty(
            titulo=r.titulo,
            descripcion=r.descripcion,
            direccion=r.direccion,
            direccion_norm=_normalize_address(r.direccion),
            precio=r.precio, moneda=r.moneda,
            tipo_operacion=r.tipo_operacion or 'venta',
            tipo_propiedad=_normalize_tipo_propiedad(r.tipo_propiedad),
            ambientes=r.ambientes, banos=r.banos, cocheras=r.cocheras,
            piso=r.piso, expensas=r.expensas,
            m2_total=r.m2_total, m2_cubiertos=r.m2_cubiertos,
            antiguedad=r.antiguedad, amenities=r.amenities, imagenes=r.imagenes,
            fuente=r.fuente, url_origen=r.url_origen,
        ))
    return {'normalized_properties': out}


def _dedup_key(p: NormalizedProperty) -> tuple[Any, ...]:
    """What makes two scraped rows the SAME listing.

    The address is reduced to a canonical `street number` so the one property
    that Zonaprop, Argenprop and the aggregators each publish their own way
    collapses into a single row instead of one per portal. Price, currency,
    operation and property type stay in the key on purpose: they are what tells
    apart the several distinct units that legitimately share one street
    address, so widening the address match cannot silently swallow them.
    """
    anchor = _address_fingerprint(p.direccion) or _normalize_address(p.direccion)
    return (anchor, p.precio, p.moneda, p.tipo_operacion, p.tipo_propiedad)


def deduplicate_properties(state: ScrapingState) -> dict[str, Any]:
    """Collapse the one property that several portals each publish — and ONLY
    that.

    `_dedup_key` anchors on a canonical street address, which stops being a
    building the moment an address names a block instead: La Plata is a
    numbered grid, "Calle 47 e/ 12 y 13" is forty houses, and inside a 50k
    price band their round asking prices collide. Measured on a real search,
    that cost 33 of 54 ZonaProp listings — every one of them killed by another
    ZonaProp listing.

    A portal already deduplicates its own catalogue, so two distinct
    `url_origen` on ONE `fuente` are two distinct properties no matter how
    alike their keys look. Only a copy from a DIFFERENT `fuente` is the
    republication the key was built to catch. With no URL there is no evidence
    they differ, so the key alone decides, as before.
    """
    key_sources: dict[tuple[Any, ...], set[str]] = {}
    seen_listings: set[tuple[str, str]] = set()
    unique: list[NormalizedProperty] = []
    dropped: Counter[str] = Counter()
    for p in state.get('normalized_properties', []):
        key = _dedup_key(p)
        fuente = p.fuente or 'desconocida'
        sources = key_sources.get(key)

        if p.url_origen and (fuente, p.url_origen) in seen_listings:
            dropped[fuente] += 1          # the very same listing, seen twice
            continue
        if sources is not None and (fuente not in sources or not p.url_origen):
            dropped[fuente] += 1          # another portal's copy, or unprovable
            continue

        key_sources.setdefault(key, set()).add(fuente)
        if p.url_origen:
            seen_listings.add((fuente, p.url_origen))
        unique.append(p)

    # Silent shrinkage here reads downstream as "the portal had few listings".
    # Attribute it per `fuente` so the real culprit is identifiable; stay quiet
    # when nothing collapsed, so the line only appears when it means something.
    if dropped:
        total = sum(dropped.values())
        by_fuente = ', '.join(f'{k}={v}' for k, v in sorted(dropped.items()))
        _log.info(
            'dedup funnel in=%d out=%d dropped=%d by_fuente=[%s]',
            len(unique) + total, len(unique), total, by_fuente,
        )
    return {'normalized_properties': unique}


def _zonas_pedidas(f: ScrapingFilters) -> list[str]:
    """Las zonas que el usuario pidió, por orden de precisión.

    `zona_pedida` es lo que se preguntó y nunca se reescribe; `zona` se mueve
    cuando la cadena de candidatos ensancha la URL de búsqueda. Filtrar por
    `zona` dejaría que ensanchar la BÚSQUEDA ensanche también la RESPUESTA.
    """
    return [z for z in ([f.zona_pedida] + f.zonas + f.localidades + [f.zona]) if z]


def _matches_filters(p: NormalizedProperty, f: ScrapingFilters | None) -> bool:
    """Search-result criteria. Missing data on a property never excludes it."""
    if f is None:
        return True
    # La zona no se miraba acá, y los portales la filtran en el ORIGEN
    # (`_item_matches_zona`) mientras que el track de inmobiliarias no la
    # filtra en ningún lado. Resultado: una propiedad de Mar del Plata llegaba
    # marcada como COINCIDENTE en una búsqueda del casco de La Plata y salía
    # arriba de todo, mezclada con las buenas.
    #
    # Una dirección ilegible no excluye, igual que un precio ausente: sin dato
    # no hay evidencia de que NO sea de la zona, y esto decide el ORDEN, no qué
    # se descarta — lo que no coincide se sigue guardando y mostrando.
    zonas = _zonas_pedidas(f)
    if zonas and (p.direccion or '').strip():
        if not any(agency_matches_zona(p.direccion, z) for z in zonas):
            return False
    if p.precio is not None:
        if f.precio_min is not None and p.precio < f.precio_min:
            return False
        if f.precio_max is not None and p.precio > f.precio_max:
            return False
    if p.ambientes is not None:
        if f.ambientes_min is not None and p.ambientes < f.ambientes_min:
            return False
        if f.ambientes_max is not None and p.ambientes > f.ambientes_max:
            return False
    return True


def _split_by_criteria(
    props: list[NormalizedProperty], filters: ScrapingFilters | None,
) -> tuple[list[NormalizedProperty], list[NormalizedProperty]]:
    """Partition scraped props into (matched, rest) preserving order, so the
    search shows everything scraped with the matching ones first."""
    matched: list[NormalizedProperty] = []
    rest: list[NormalizedProperty] = []
    for p in props:
        (matched if _matches_filters(p, filters) else rest).append(p)
    return matched, rest


# What each numeric column in `properties` can actually hold. Postgres rejects
# the whole INSERT on the first value that does not fit, so one mis-parsed
# figure takes the entire batch with it — observed live as
# `numeric field overflow (22003)` losing 420 already-paid-for properties.
# A value that cannot fit its column is not data: dropping that FIELD costs one
# attribute, dropping the batch costs everything.
_NUMERIC_CEILINGS: dict[str, float] = {
    'precio': 10 ** 12,            # numeric(14,2)
    'expensas': 10 ** 8,           # numeric(10,2)
    'm2_total': 10 ** 8,
    'm2_cubiertos': 10 ** 8,
    'confianza_extraccion': 10,    # numeric(4,3)
    'ambientes': 32_768,           # smallint
    'banos': 32_768,
    'cocheras': 32_768,
    'piso': 32_768,
    'antiguedad': 32_768,
}


def _fits(campo: str, valor: Any, direccion: str) -> Any:
    """`valor`, or None when the column cannot hold it (and say so)."""
    if valor is None:
        return None
    if abs(valor) < _NUMERIC_CEILINGS[campo]:
        return valor
    _log.warning(
        'valor fuera de rango descartado: %s=%r no entra en la columna (%s)',
        campo, valor, direccion,
    )
    return None


def _prop_to_dict(p: NormalizedProperty, job_id: str | None) -> dict[str, Any]:
    d = p.direccion
    return {
        'titulo': p.titulo, 'descripcion': p.descripcion,
        'direccion': p.direccion, 'direccion_norm': p.direccion_norm,
        'precio': _fits('precio', float(p.precio) if p.precio is not None else None, d),
        'moneda': p.moneda, 'tipo_operacion': p.tipo_operacion, 'tipo_propiedad': p.tipo_propiedad,
        'ambientes': _fits('ambientes', p.ambientes, d),
        'banos': _fits('banos', p.banos, d),
        'cocheras': _fits('cocheras', p.cocheras, d),
        'piso': _fits('piso', p.piso, d),
        'expensas': _fits('expensas', float(p.expensas) if p.expensas is not None else None, d),
        'm2_total': _fits('m2_total', float(p.m2_total) if p.m2_total is not None else None, d),
        'm2_cubiertos': _fits(
            'm2_cubiertos',
            float(p.m2_cubiertos) if p.m2_cubiertos is not None else None, d,
        ),
        'antiguedad': _fits('antiguedad', p.antiguedad, d),
        'amenities': p.amenities, 'imagenes': p.imagenes,
        'fuente': p.fuente, 'url_origen': p.url_origen, 'scraping_job_id': job_id,
        'confianza_extraccion': _fits(
            'confianza_extraccion', float(p.confianza_extraccion), d,
        ),
    }


_AGENCY_CACHE_MIN_ROWS = 1  # >=1 fresh row for the zona → serve from cache, skip Apify
# La pertenencia a una zona la decide la DIRECCIÓN, y eso se evalúa en Python
# (`agency_matches_zona`), no en SQL. Así que la lectura trae las agencias
# frescas y filtra acá. El tope existe para que la consulta no crezca sin
# límite a medida que la base se llena; con ~1000 agencias hoy, sobra.
_AGENCY_CACHE_MAX_ROWS = 5000


def _agency_row_to_model(row: dict[str, Any]) -> Agency:
    cal = row.get('calificacion')
    return Agency(
        id=str(row['id']),
        nombre=row['nombre'],
        direccion=row.get('direccion'),
        telefono=row.get('telefono'),
        sitio_web=row.get('sitio_web'),
        google_maps_url=row.get('google_maps_url'),
        instagram_handle=row.get('instagram_handle'),
        calificacion=float(cal) if cal is not None else None,
        zona=row.get('zona') or '',
    )


def _agency_to_row(a: Agency, zona_norm: str, scraped_at: str) -> dict[str, Any]:
    return {
        'nombre': a.nombre,
        'direccion': a.direccion,
        'telefono': a.telefono,
        'sitio_web': a.sitio_web,
        'google_maps_url': a.google_maps_url,
        'instagram_handle': a.instagram_handle,
        'calificacion': float(a.calificacion) if a.calificacion is not None else None,
        'zona': a.zona,
        'zona_norm': zona_norm,
        'scraped_at': scraped_at,
        # id omitted → gen_random_uuid() on insert, preserved on conflict-update.
        # dedup_key omitted → generated column.
    }


async def _read_cached_agencies(sb: Any, zona_norm: str, zona: str = '') -> list[Agency]:
    """Inmobiliarias frescas de esta zona, filtradas por dirección.

    La guarda de `_norm_googlemaps_agency` sólo protege lo que entra de acá en
    adelante. Las filas que ya se guardaron con la zona equivocada — cuando el
    normalizador no miraba la dirección — siguen ahí y siguen contestando: con
    UNA fila fresca `discover_agencies` sirve el caché entero y ni llama a
    Apify, durante los 30 días del TTL.

    Por eso el filtro corre también al leer, con la MISMA guarda y el MISMO
    campo que usa la escritura: leer con un criterio más laxo que el de
    escritura haría que el caché se comportara distinto según quién lo llenó.
    Las filas no se borran, sólo dejan de contestar por una zona que no es la
    suya.
    """
    if sb is None or not zona_norm or not (zona or '').strip():
        return []
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=settings.AGENCY_CACHE_TTL_DAYS)).isoformat()
    res = await (
        sb.table('real_estate_agencies')
        .select('*')
        # SIN `.eq('zona_norm', ...)`, y es el corazón del arreglo. Ese campo
        # guarda la zona que se BUSCÓ cuando la agencia se descubrió, no dónde
        # está: `Barreira` tiene dirección en City Bell y quedó fichada bajo
        # 'melchor romero' porque esa fue la búsqueda que la encontró. Filtrar
        # con él convertía un accidente de descubrimiento en el criterio de
        # pertenencia, y se llevaba puesto el 95% de las agencias del casco.
        .gte('scraped_at', cutoff)
        .limit(_AGENCY_CACHE_MAX_ROWS)
        .execute()
    )
    return [
        a for r in (res.data or [])
        if agency_matches_zona((a := _agency_row_to_model(r)).direccion, zona)
    ]


async def _upsert_agencies(sb: Any, agencies: list[Agency], zona_norm: str) -> None:
    if sb is None or not agencies:
        return
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = [_agency_to_row(a, zona_norm, now_iso) for a in agencies]
    await sb.table('real_estate_agencies').upsert(
        rows, on_conflict='zona_norm,dedup_key'
    ).execute()


def _dedup_triple(
    direccion: Any, precio: Any, tipo_operacion: Any,
) -> tuple[Any, float | None, Any]:
    """The `properties_dedup_idx (direccion, precio, tipo_operacion)` key, with
    `precio` coerced so a Decimal from Postgres and a float from the scraper
    compare equal."""
    return (direccion, float(precio) if precio is not None else None, tipo_operacion)


async def _fill_missing_images(sb: Any, props: list[NormalizedProperty]) -> None:
    """Backfill galleries onto already-stored rows that still have none.

    `_upsert_properties` writes insert-ignore, so it can never touch an
    existing row — deliberately, because a blind `DO UPDATE` would wipe the
    manual curation `PATCH /properties/{id}` exists to support. The side effect
    was that any property first stored before its portal had gallery extraction
    stayed photoless forever: the conflicting insert does nothing, so a
    re-scrape produced correct photos and discarded them.

    This pass closes that hole from the other side — it writes ONLY where the
    stored gallery is empty, so a curated row (non-empty by definition) is
    never overwritten. Best-effort: a failure here must not fail the run.
    """
    scraped = {
        _dedup_triple(p.direccion, p.precio, p.tipo_operacion): p.imagenes
        for p in props if p.imagenes
    }
    if sb is None or not scraped:
        return
    try:
        rows: list[dict[str, Any]] = []
        for chunk in chunk_for_in_filter(list({d for d, _, _ in scraped})):
            res = await sb.table('properties').select(
                'id,direccion,precio,tipo_operacion,imagenes'
            ).in_('direccion', chunk).execute()
            rows.extend(res.data or [])

        pending: list[tuple[str, list[str]]] = []
        for row in rows:
            if row.get('imagenes'):  # curated or already filled — hands off
                continue
            imgs = scraped.get(_dedup_triple(
                row.get('direccion'), row.get('precio'), row.get('tipo_operacion'),
            ))
            if imgs:
                pending.append((row['id'], imgs))
        if not pending:
            return

        sem = asyncio.Semaphore(5)

        async def _write(prop_id: str, imgs: list[str]) -> None:
            async with sem:
                await sb.table('properties').update(
                    {'imagenes': imgs}
                ).eq('id', prop_id).execute()

        await asyncio.gather(*(_write(pid, imgs) for pid, imgs in pending))
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning('_fill_missing_images failed: %s', exc)


def _dedup_row_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """The key `properties_dedup_idx` enforces, as a Python tuple."""
    return (row['direccion'], row['precio'], row['tipo_operacion'], row['url_origen'])


async def _upsert_properties(sb: Any, props: list[NormalizedProperty], job_id: str | None) -> None:
    if sb is None or not props:
        return
    data = [_prop_to_dict(p, job_id) for p in props]

    # The write below is insert-ignore against `properties_dedup_idx`, so
    # Postgres silently discards rows that share its key. Mirror that key
    # EXACTLY — it now includes `url_origen` — or this line reports a collapse
    # that stopped happening and sends the next investigation the wrong way.
    keys = {
        (r['direccion'], r['precio'], r['tipo_operacion'], r['url_origen'])
        for r in data
    }
    if len(keys) < len(data):
        colliding = Counter(
            r['direccion'] for r in data
            if sum(1 for o in data if _dedup_row_key(o) == _dedup_row_key(r)) > 1
        )
        worst = [d for d, _ in colliding.most_common(5)]
        _log.warning(
            'upsert collision: filas=%d distintas=%d (el indice unico se come %d) '
            'direcciones=[%s]',
            len(data), len(keys), len(data) - len(keys), ', '.join(worst),
        )

    try:
        # insert-ignore: existing rows keep whatever the ficha editor curated.
        # `_fill_missing_images` then covers the rows that have no gallery yet.
        await sb.table('properties').upsert(
            data,
            on_conflict='direccion,precio,tipo_operacion,url_origen',
            ignore_duplicates=True,
        ).execute()
    except Exception:
        import logging
        logging.getLogger(__name__).exception('property upsert failed (%d rows, job %s)', len(data), job_id)
        return

    await _fill_missing_images(sb, props)

    # Best-effort geocoding of newly ingested rows — fire-and-forget so it never
    # delays the SSE `done` event or fails the scraping run. The backfill lock
    # makes concurrent kicks (multiple save_* calls in flight) a no-op.
    from app.services.geocode import run_backfill
    asyncio.ensure_future(run_backfill(sb, limit=50))


async def _link_job_properties(
    sb: Any,
    props: list[NormalizedProperty],
    job_id: str | None,
    matched: list[NormalizedProperty] | None = None,
) -> None:
    """Link EVERY scraped prop to the job. `matched` (subset of `props`) marks
    which ones satisfy the user's criteria — the rest link with
    `matches_criteria=False` so the results view can order matched-first
    without dropping anything. `matched=None` flags everything as matching."""
    if sb is None or not props or not job_id:
        return
    try:
        matched_props = props if matched is None else matched
        matched_triples = {
            (p.direccion, float(p.precio) if p.precio is not None else None, p.tipo_operacion)
            for p in matched_props
        }

        # `url_origen` is the listing's identity, and every scraped row has
        # one. The triple stopped being usable here the moment the unique
        # index widened: several rows now legitimately share an address and
        # price, so matching on it would both miss this job's other listings
        # and drag in rows another search wrote at the same address.
        with_url = [p for p in props if p.url_origen]
        without_url = [p for p in props if not p.url_origen]

        id_flags: dict[str, bool] = {}

        if with_url:
            urls = [u for u in {p.url_origen for p in with_url} if u]
            matched_urls = {p.url_origen for p in matched_props if p.url_origen}
            for chunk in chunk_for_in_filter(urls):
                res = await sb.table('properties').select(
                    'id,url_origen'
                ).in_('url_origen', chunk).execute()
                for row in (res.data or []):
                    id_flags[row['id']] = row.get('url_origen') in matched_urls

        priced = [p for p in without_url if p.precio is not None]
        null_priced = [p for p in without_url if p.precio is None]

        if priced:
            direcciones = list({p.direccion for p in priced})
            priced_rows: list[dict[str, Any]] = []
            for chunk in chunk_for_in_filter(direcciones):
                res = await sb.table('properties').select(
                    'id,direccion,precio,tipo_operacion'
                ).in_('direccion', chunk).execute()
                priced_rows.extend(res.data or [])
            priced_triples = {
                (p.direccion, float(p.precio), p.tipo_operacion)
                for p in priced
            }
            for row in priced_rows:
                row_precio = float(row['precio']) if row['precio'] is not None else None
                triple = (row['direccion'], row_precio, row['tipo_operacion'])
                if triple in priced_triples:
                    id_flags[row['id']] = triple in matched_triples

        if null_priced:
            direcciones_null = list({p.direccion for p in null_priced})
            null_rows: list[dict[str, Any]] = []
            for chunk in chunk_for_in_filter(direcciones_null):
                res_null = await sb.table('properties').select(
                    'id,direccion,tipo_operacion'
                ).in_('direccion', chunk).is_('precio', 'null').execute()
                null_rows.extend(res_null.data or [])
            null_pairs = {(p.direccion, p.tipo_operacion) for p in null_priced}
            for row in null_rows:
                if (row['direccion'], row['tipo_operacion']) in null_pairs:
                    id_flags[row['id']] = (row['direccion'], None, row['tipo_operacion']) in matched_triples

        if not id_flags:
            return

        rows = [
            {'job_id': job_id, 'property_id': pid, 'matches_criteria': flag}
            for pid, flag in id_flags.items()
        ]
        try:
            await sb.table('search_property_results').upsert(
                rows, on_conflict='job_id,property_id', ignore_duplicates=True
            ).execute()
        except Exception:
            # matches_criteria column missing (migration not applied) — never
            # lose the job links over the flag.
            bare = [{'job_id': job_id, 'property_id': pid} for pid in id_flags]
            await sb.table('search_property_results').upsert(
                bare, on_conflict='job_id,property_id', ignore_duplicates=True
            ).execute()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning('_link_job_properties failed: %s', exc)


async def save_portal_properties(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    sb = config['configurable'].get('supabase')
    job_id = state.get('job_id')
    props: list[NormalizedProperty] = state.get('normalized_properties', [])

    # Save EVERYTHING scraped to the global catalog, regardless of criteria
    await _upsert_properties(sb, props, job_id)

    # Everything scraped is a search result; the matched ones lead the list
    filters = state.get('filters')
    matched, rest = _split_by_criteria(props, filters)
    ordered = matched + rest
    await _link_job_properties(sb, ordered, job_id, matched)

    # Emit partial results — portales done, waiting for agency review
    await adispatch_custom_event('property_batch', {
        'event': 'property_batch', 'source': 'portales',
        'count': len(matched), 'total': len(ordered),
        'properties': [p.model_dump() for p in ordered],
    }, config=config)
    return {'normalized_properties': ordered}


# ── Phase 1 → Phase 2 bridge: agency review interrupt ─────────────────────────

async def _fetch_active_manual_sources(
    sb: Any, zona: str | None = None, *, incluir_sin_zona: bool = False,
) -> list[dict]:
    """Manually-registered sources (backend/app/api/v1/manual_sources.py) —
    e.g. a RE/MAX office or small inmobiliaria not surfaced by the Google
    Maps 'inmobiliarias en {zona}' search.

    `zona` scopes the fetch to the inmobiliarias WE classified into that zona
    (matched on `zona_norm`, so 'city bell, La Plata' finds 'City Bell').
    None/blank means every registered source ("todas las zonas").

    Best-effort: an empty list on any failure just means no manual sources get
    folded in this run."""
    if sb is None:
        return []
    acotar_en_sql = bool(zona and zona.strip()) and not incluir_sin_zona
    try:
        query = (
            sb.table('manual_sources')
            .select('id,nombre,url,zona,zona_norm')
            .eq('activo', True)
        )
        if acotar_en_sql:
            query = query.eq('zona_norm', _normalize_zona(zona or ''))
        res = await query.execute()
        filas = res.data or []
    except Exception:
        return []
    if acotar_en_sql or not (zona or '').strip():
        return filas
    # Camino permisivo: una fuente cargada SIN zona vale para toda búsqueda, no
    # para ninguna. Medido sobre la base real, las 248 fuentes cargadas tienen
    # `zona_norm` en NULL — acotarlas por la zona del prompt las borraba a
    # todas y el registro curado quedaba vacío sin que nada lo dijera.
    objetivo = _normalize_zona(zona or '')
    return [f for f in filas if not (f.get('zona_norm') or '') or f.get('zona_norm') == objetivo]


def _read_agency_selection(resumed: Any) -> tuple[list[str], list[str] | None]:
    """Normalize what POST /{job_id}/resume sent back into the interrupt.

    Current shape is a dict with both picks. A bare list is the pre-registry
    shape (a job interrupted by an older build, resumed after deploy): it only
    ever carried agency ids, and back then every curated source was folded in
    unconditionally — so its manual selection is None ("keep them all"), which
    is NOT the same as an explicit empty list ("the user unchecked them")."""
    if isinstance(resumed, dict):
        raw_manual = resumed.get('manual_source_ids')
        return (
            list(resumed.get('agency_ids') or []),
            list(raw_manual) if raw_manual is not None else None,
        )
    return list(resumed or []), None


def _review_message(agencies_count: int, manual_count: int) -> str:
    """The card's copy has to name BOTH origins — the operator needs to see that
    their hand-loaded inmobiliarias are in this run, which is exactly what the
    agencies-only wording used to hide."""
    curadas = f'{manual_count} cargadas por vos en Fuentes'
    if agencies_count and manual_count:
        encontradas = f'Encontré {agencies_count} inmobiliarias locales, más {curadas}.'
    elif manual_count:
        encontradas = f'Voy a consultar las {curadas}.'
    else:
        encontradas = f'Encontré {agencies_count} inmobiliarias locales.'
    return f'{encontradas} Seleccioná las que querés incluir para buscar propiedades en sus sitios web.'


# Lo que cuesta analizar UNA página con Haiku, medido sobre el prompt y el
# texto reales: ~442 tokens de sistema + herramienta, ~1500 del texto de la
# página (`text[:6000]`) y ~300 de salida. A USD 1/MTok de entrada y USD 5/MTok
# de salida eso da ~USD 0.0034.
_USD_LLM_POR_PAGINA = 0.0034
# El Website Content Crawler es pay-per-usage: ~USD 0.5-5 por cada 1.000
# páginas con navegador (apify.com/apify/website-content-crawler, 2026-09-02).
# Se toma el medio del rango, que es lo honesto para una estimación.
_USD_APIFY_POR_PAGINA = 0.002


def _costo_estimado_por_sitio() -> float:
    """USD aproximados de scrapear y analizar UNA inmobiliaria.

    Existe para que el número esté donde se toma la decisión. El selector llega
    con TODAS las inmobiliarias tildadas: con 552 en pantalla, "Continuar"
    autoriza una decena de dólares sin que nada lo diga. Tildar 50 en vez de
    552 es 10x — ninguna optimización de prompt compite con eso, pero el
    operador no puede elegir lo que no ve.

    Es una ESTIMACIÓN: sitios distintos traen páginas de tamaños distintos. El
    orden de magnitud es lo que importa, y es lo que el test fija.
    """
    from app.core.config import settings
    if settings.WEBSITE_USE_APIFY:
        paginas = max(1, settings.WEBSITE_APIFY_MAX_PAGES)
        return paginas * (_USD_LLM_POR_PAGINA + _USD_APIFY_POR_PAGINA)
    # Camino directo: la home más las sub-páginas que se sigan. No cuesta
    # Apify, sólo tokens.
    paginas = 1 + max(0, settings.WEBSITE_MAX_SUBPAGES)
    return paginas * _USD_LLM_POR_PAGINA


async def review_agencies(state: ScrapingState, config: RunnableConfig) -> dict[str, Any]:
    agencies = state.get('agencies', [])
    sb = config['configurable'].get('supabase')
    selection = _read_selection(state)
    # Portales-only search: the inmobiliarias registry is never consulted.
    # `zona_inmobiliarias` es un desplegable APARTE del prompt, y su default es
    # "todas las zonas". Con eso vacío se traían las inmobiliarias curadas de
    # TODA la base — 249 de zonas que el usuario nunca nombró, en una búsqueda
    # del casco de La Plata. Nadie pidió eso: lo pidió un campo vacío.
    #
    # Vacío ahora significa "la zona que escribí en el prompt", que es lo que
    # el operador cree que está pidiendo. Para buscar en todas las zonas se
    # elige explícitamente en el selector.
    filtros_zona = state.get('filters')
    zona_registro = selection['zona_inmobiliarias'] or (
        (filtros_zona.zona_pedida or filtros_zona.zona) if filtros_zona else None
    )
    # Estricto cuando el operador ELIGIÓ una zona en el selector ("sólo las que
    # clasifiqué ahí"); permisivo cuando la zona sale del prompt, porque ahí
    # nunca pidió esa exigencia y una fuente sin clasificar sigue siendo suya.
    manual_sources = (
        await _fetch_active_manual_sources(
            sb, zona_registro,
            incluir_sin_zona=not selection['zona_inmobiliarias'],
        )
        if selection['buscar_inmobiliarias'] else []
    )

    if not agencies and not manual_sources:
        # No agencies found, no manual sources registered → skip Instagram, emit done
        await adispatch_custom_event('done', {
            'event': 'done',
            'job_id': state.get('job_id'),
            'total_count': len(state.get('normalized_properties', [])),
            'sources': list(PORTAL_SOURCES),
        }, config=config)
        return {'selected_agency_ids': [], 'manual_sources': []}

    # Curated-only runs pause here too: the whole point of the review step is
    # that nothing gets scraped without the operator seeing it first.
    await adispatch_custom_event('agencies_review', {
        'event': 'agencies_review',
        # Por SITIO, no el total: el cliente lo multiplica por lo que haya
        # tildado, así el número se actualiza mientras destilda sin volver acá.
        'usd_por_sitio': _costo_estimado_por_sitio(),
        'agencies': [a.model_dump() for a in agencies],
        'manual_sources': manual_sources,
        'message': _review_message(len(agencies), len(manual_sources)),
    }, config=config)

    # INTERRUPT — graph pauses here, resumes with the user's two picks
    selected, selected_manual_ids = _read_agency_selection(interrupt({'type': 'agency_selection'}))

    if selected_manual_ids is not None:
        keep = set(selected_manual_ids)
        manual_sources = [s for s in manual_sources if s.get('id') in keep]

    return {'selected_agency_ids': selected, 'manual_sources': manual_sources}


def route_after_review(state: ScrapingState) -> str | list[Any]:
    selected = state.get('selected_agency_ids', [])
    agencies = state.get('agencies', [])
    manual_sources = state.get('manual_sources', [])
    job_id = state.get('job_id')

    agency_map = {a.id: a for a in agencies}
    selected_agencies = [agency_map[aid] for aid in selected if aid in agency_map]

    sends: list[Any] = []
    websites_sent = 0
    # `0` = NO CAP (same convention as the portal paging knobs): every website
    # the user confirmed gets scraped. The old default of 10 was a self-imposed
    # ceiling that dropped the rest with no error and no event.
    cap = settings.MAX_WEBSITE_URLS

    # Manually-registered sources reach the SAME website-scraping pipeline as
    # agency websites and share the cap — but they go FIRST. Someone filed these
    # by hand for this zona; a discovered Google Maps result did not. It still
    # matters uncapped (order of results) and matters a lot if a deployment
    # re-caps via env: with the selector defaulting to every agency checked,
    # taking them last meant a zona with cap+ agencies never scraped a single
    # curated source, silently.
    for src in manual_sources:
        if cap and websites_sent >= cap:
            break
        sends.append(Send('run_website_scraper', {'nombre': src['nombre'], 'url': src['url'], 'job_id': job_id}))
        websites_sent += 1

    for a in selected_agencies:
        if a.sitio_web and (not cap or websites_sent < cap):
            sends.append(Send('run_website_scraper', {'nombre': a.nombre, 'url': a.sitio_web, 'job_id': job_id}))
            websites_sent += 1
        # Instagram is a separate actor with its own budget — the website cap
        # never gated it, so a full cap must not silence it either.
        if a.instagram_handle and not settings.SCRAPE_GOOGLEMAPS_ONLY:
            sends.append(Send('run_instagram_scraper', {'nombre': a.nombre, 'handle': a.instagram_handle, 'job_id': job_id}))

    # El total del fan-out sólo se conoce acá. Las ramas lo leen del registro
    # para poder reportar "132 de 260" en vez de 260 filas sueltas.
    _reset_website_progress(job_id, websites_sent)

    if websites_sent:
        from app.services.apify import proxy_fingerprint

        # El embudo de este track no se veía en ningún lado, y sin proxy la
        # búsqueda vuelve vacía desde Railway sin decir por qué. Que el log lo
        # diga ANTES de empezar, no después de 20 minutos.
        _log.info(
            'inmobiliarias: %d sitios al fan-out (%d curadas + %d descubiertas), '
            'concurrencia %d, proxy=%s',
            websites_sent, len(manual_sources), len(selected_agencies),
            settings.WEBSITE_SCRAPE_CONCURRENCY,
            proxy_fingerprint(settings.SCRAPER_PROXY_URL),
        )

    return sends if sends else 'no_websites'


# ── Phase 2: Website scraping + LLM extraction ───────────────────────────────

async def run_website_scraper(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    url: str = state['url']
    nombre: str = state.get('nombre', url)
    job_id: str | None = state.get('job_id')
    service = get_apify_service()
    label = url.replace('https://', '').replace('http://', '').split('/')[0]

    # Con agregado, una fila por sitio son 520 eventos y 520 re-renders de una
    # lista de 260 ítems en el cliente. Los per-sitio quedan sólo para el caso
    # sin contador (job sin id), donde son el único feedback que hay.
    aggregated = _website_progress_total(job_id) > 0

    async def on_progress(src: str, status: str, count: int) -> None:
        if aggregated:
            return
        await adispatch_custom_event('progress', {
            'event': 'progress', 'source': f'web:{label}',
            'status': status, 'count': count,
            'message': {
                'running': f'Buscando propiedades en {nombre}...',
                'done': f'{count} páginas escaneadas en {nombre}',
                'error': f'Error en {nombre}',
            }.get(status, ''),
        }, config=config)

    async def emit_total(done: int, total: int) -> None:
        await adispatch_custom_event('progress', {
            'event': 'progress', 'source': 'inmobiliarias',
            'status': 'done' if done >= total else 'running',
            'count': done, 'done': done, 'total': total,
            'message': f'{done} de {total} inmobiliarias escaneadas',
        }, config=config)

    if (announced_total := _claim_website_announcement(job_id)):
        await emit_total(0, announced_total)

    try:
        # Los N `Send` ya existen todos; el semáforo decide cuántos corren.
        async with _get_website_semaphore():
            pages = await service.scrape_website(url, on_progress)
    except Exception as exc:
        await adispatch_custom_event('error', {
            'event': 'error', 'source': f'web:{label}',
            'message': str(exc), 'recoverable': True,
        }, config=config)
        # Un sitio caído igual avanza la barra: si no, con 260 inmobiliarias la
        # barra se clava en 258/260 para siempre.
        if (counted := _bump_website_progress(job_id))[1]:
            await emit_total(*counted)
        return {'website_pages': [], 'errors': [f'{url}: {exc}']}

    if (counted := _bump_website_progress(job_id))[1]:
        await emit_total(*counted)
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
                        'banos': {'type': ['integer', 'null']},
                        'cocheras': {'type': ['integer', 'null']},
                        'piso': {'type': ['integer', 'null']},
                        'expensas': {'type': ['number', 'null']},
                        'amenities': {'type': ['array', 'null'], 'items': {'type': 'string'}},
                        'm2': {'type': ['number', 'null']},
                        'direccion': {'type': ['string', 'null']},
                        'descripcion': {'type': ['string', 'null']},
                        'url_ficha': {'type': ['string', 'null']},
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
    'Extraé datos de precios, ambientes, m², tipo y dirección cuando estén disponibles. '
    'El texto puede contener links en formato markdown [texto](url). '
    'Si identificás un link que lleva a la ficha individual de una propiedad específica, '
    'usá esa URL en el campo url_ficha de esa propiedad — es MUY importante: de esa ficha se obtienen las fotos. '
    'Si no podés asociar un link específico, dejá url_ficha en null.'
)


# ── Filtro previo al LLM ──────────────────────────────────────────────────────
#
# `_scrape_website_direct` trae la home de cada inmobiliaria más hasta 5
# sub-páginas, y TODAS iban a Haiku a precio completo: "quiénes somos",
# contacto, tasaciones, política de privacidad. El system prompt lo admite
# ("Si no hay propiedades en la página, devolvé propiedades=[]") — esa respuesta
# vacía cuesta lo mismo que una con veinte propiedades, porque lo que se paga es
# el texto de ENTRADA, que es el 77% del costo de la llamada.
#
# El sesgo es explícito y va hacia mandar de más: un falso positivo cuesta una
# llamada, que es exactamente lo que se paga hoy; un falso negativo pierde una
# propiedad real. Por eso alcanza con CUALQUIERA de las dos señales.

# Un precio publicado: moneda pegada a un número de tres dígitos o más. Toda
# ficha con precio lo escribe así; ningún teléfono ni año lleva moneda adelante.
_PRECIO_RE = re.compile(r'(?:u\$s|us\$|usd|ars|\$)\s*\d[\d.,]{2,}', re.IGNORECASE)

# Vocabulario que una ficha usa y una página institucional no. Se piden DOS
# DISTINTOS: uno solo aparece en cualquier menú o pie de página ("departamentos,
# casas y terrenos"), y ese menú está en todas las páginas del sitio.
_TERMINOS_INMOBILIARIOS = (
    'ambiente', 'monoambiente', 'dormitorio', 'cochera', 'quincho', 'balcon',
    'balcón', 'baño', 'bano', 'm2', 'm²', 'metros cuadrados', 'cubiertos',
    'apto credito', 'apto crédito', 'expensas', 'contrafrente', 'duplex', 'dúplex',
)

_MIN_PAGE_CHARS = 100


def page_is_worth_extracting(text: str | None) -> bool:
    """¿Vale la pena pagarle al LLM por esta página?

    Ver el bloque de arriba para el sesgo. `False` sólo cuando la página no
    muestra NINGUNA señal de listar propiedades — ahí la llamada sólo puede
    devolver `[]`, y se paga igual.
    """
    if not text or len(text.strip()) < _MIN_PAGE_CHARS:
        return False
    bajo = text.lower()
    if _PRECIO_RE.search(bajo):
        return True
    return sum(1 for t in _TERMINOS_INMOBILIARIOS if t in bajo) >= 2


# ── Números que vienen de un LLM ─────────────────────────────────────────────
#
# El tool schema pide un entero y el modelo igual manda '<UNKNOWN>', 'N/A', 'PB'
# o ''. No es un caso raro: es lo que hace un LLM cuando el dato no está en la
# página. Estos valores entraban CRUDOS a `NormalizedProperty` y Pydantic los
# rechazaba — la excepción subía por el nodo, LangGraph la propagaba y la
# corrida entera moría (job 342cc50e: 552 sitios tirados por un `piso` de un
# post de Instagram).
#
# Un campo OPCIONAL que no se pudo leer vale None. Nunca una excepción.

_NUM_RE = re.compile(r'\d+(?:[.,]\d+)*')


def _llm_float(value: Any) -> float | None:
    """Número opcional de una respuesta del LLM. Ilegible → None.

    Lee el primer número del texto, así '3°' es 3 y '2 ambientes' son 2 — el
    dato está ahí y tirarlo por el sufijo sería perderlo. Los separadores de
    miles argentinos ('120.000') se resuelven por posición: un grupo de
    exactamente 3 dígitos después del último separador es miles, no decimales.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    texto = str(value)
    m = _NUM_RE.search(texto)
    if not m:
        return None
    # Un signo pegado al número lo descarta entero. '-3' de piso es un subsuelo,
    # y devolver 3 diría "tercer piso" — un dato equivocado es peor que ninguno.
    if m.start() and texto[m.start() - 1] == '-':
        return None
    crudo = m.group(0)
    cabeza, sep, cola = crudo.rpartition('.') if '.' in crudo else crudo.rpartition(',')
    if sep and len(cola) != 3:
        crudo = f'{cabeza.replace(".", "").replace(",", "")}.{cola}'
    else:
        crudo = crudo.replace('.', '').replace(',', '')
    try:
        return float(crudo)
    except ValueError:
        return None


def _llm_int(value: Any) -> int | None:
    """Entero opcional de una respuesta del LLM. Ilegible → None."""
    n = _llm_float(value)
    return None if n is None else int(n)


async def _extract_page_properties(
    page: dict[str, str],
    sb: Any,
    job_id: str | None,
) -> list[NormalizedProperty]:
    """Extrae las propiedades de UNA página. Nunca propaga: una página que
    falla vale [] y la búsqueda sigue — con 1500 páginas en juego, una sola
    excepción no puede tirar abajo la corrida entera."""
    text = page.get('text', '')
    if not text or len(text) < 100:
        return []
    # Truncate very long pages — Claude context limit
    text = text[:6000]
    try:
        msg = await asyncio.wait_for(
            _client.messages.create(  # type: ignore[call-overload]
                model=MODEL,
                max_tokens=1024,
                system=_WEBSITE_SYSTEM_PROMPT,
                tools=[_WEBSITE_EXTRACT_TOOL],  # type: ignore[list-item]
                tool_choice={'type': 'tool', 'name': 'extract_properties_from_webpage'},
                messages=[{'role': 'user', 'content': f'Página: {page.get("url", "")}\n\n{text}'}],
            ),
            timeout=settings.WEBSITE_EXTRACT_TIMEOUT,
        )
    except Exception:
        # Never reached Anthropic (o se colgó) → nothing billed, nothing to book.
        return []

    await record_llm_usage(
        sb,
        scope=SCOPE_EXTRACT_WEBSITE,
        model=MODEL,
        usage=getattr(msg, 'usage', None),
        job_id=job_id,
        url=page.get('url') or None,
    )

    tool_use = next((b for b in msg.content if b.type == 'tool_use'), None)
    if not tool_use:
        return []

    page_images: list[str] = page.get('images') or []
    page_props: list[NormalizedProperty] = []

    for prop in (tool_use.input.get('propiedades') or []):
        filled = sum(1 for f in ['precio', 'tipo_operacion', 'tipo_propiedad', 'ambientes', 'm2', 'direccion']
                     if prop.get(f) is not None)
        confianza = min(1.0, filled / 6)
        # El docstring de esta función prometía "nunca propaga", pero el `try`
        # de arriba sólo envolvía la llamada HTTP: armar los modelos quedaba
        # afuera, y una propiedad mala mataba la página entera — y con ella la
        # búsqueda. Ahora la promesa se cumple.
        try:
            page_props.append(NormalizedProperty(
                titulo=prop.get('titulo') or '',
                descripcion=prop.get('descripcion'),
                direccion=prop.get('direccion') or '',
                direccion_norm=_normalize_address(prop.get('direccion') or ''),
                precio=_llm_float(prop.get('precio')),
                moneda=prop.get('moneda') or 'USD',  # type: ignore[arg-type]
                tipo_operacion=prop.get('tipo_operacion') or 'venta',  # type: ignore[arg-type]
                tipo_propiedad=_normalize_tipo_propiedad(prop.get('tipo_propiedad')),
                ambientes=_llm_int(prop.get('ambientes')),
                banos=_llm_int(prop.get('banos')),
                cocheras=_llm_int(prop.get('cocheras')),
                piso=_llm_int(prop.get('piso')),
                expensas=_llm_float(prop.get('expensas')),
                amenities=prop.get('amenities') or [],
                m2_total=_llm_float(prop.get('m2')),
                fuente='googlemaps',
                url_origen=prop.get('url_ficha') or page.get('url'),
                confianza_extraccion=confianza,
            ))
        except Exception as exc:
            _log.warning('propiedad descartada de %s: %s', page.get('url'), exc)
            continue

    # A single-property page (a listing detail/ficha) owns ALL its images.
    # On multi-property pages the page-level pool mixes every card's photos in
    # network-arrival order, so positional assignment shuffles galleries between
    # properties — those get their gallery from their own ficha below instead.
    if len(page_props) == 1 and page_images:
        page_props[0].imagenes = page_images[:20]

    return page_props


async def extract_website_properties_llm(state: ScrapingState, config: RunnableConfig) -> dict[str, Any]:
    pages: list[dict[str, str]] = state.get('website_pages', [])
    total_pages = len(pages)
    # One ledger row per page rather than one aggregate for the node: this loop is
    # where token spend concentrates, and an aggregate would hide which site was
    # expensive. Writing inside the loop also means a crash mid-run keeps the rows
    # for the pages already paid for.
    sb = config['configurable'].get('supabase')
    job_id = state.get('job_id')

    # Antes esto era un `for` secuencial. Con 260 inmobiliarias el fan-in trae
    # ~1500 páginas y a ~4 s por llamada eso son más de 90 minutos con el
    # stream abierto: la búsqueda moría antes de llegar al final. En paralelo
    # acotado son minutos, y el tope existe para no reventar el rate limit de
    # Anthropic (que sería el error siguiente).
    sem = asyncio.Semaphore(max(1, settings.WEBSITE_EXTRACT_CONCURRENCY))
    analyzed = 0
    # Páginas que el filtro previo descartó sin pagarlas. Se reporta al cerrar:
    # un filtro que trabaja en silencio es un filtro que nadie va a poder
    # ajustar el día que descarte de más.
    skipped = 0

    async def emit(status: str, done: int, count: int, message: str) -> None:
        await adispatch_custom_event('progress', {
            'event': 'progress', 'source': 'extraccion', 'status': status,
            'count': count, 'done': done, 'total': total_pages,
            'message': message,
        }, config=config)

    filters = state.get('filters')

    async def persist(props: list[NormalizedProperty]) -> None:
        """Guarda lo de ESTA página apenas sale, sin esperar al fan-in.

        Antes la fase entera se escribía en `save_website_properties`, el nodo
        siguiente: hasta que las ~1500 páginas no terminaban no había una sola
        fila en la base, y cortar ahí — el botón de detener, un deploy, un
        timeout — tiraba minutos de trabajo ya pagado.

        Este loop ya escribía el ledger de tokens por página por exactamente
        este motivo ("a crash mid-run keeps the rows for the pages already paid
        for"). El razonamiento estaba hecho para el GASTO; esto lo aplica a las
        propiedades, que es lo que el usuario espera.

        Best-effort a propósito: el guardado incremental es una mejora de
        resiliencia y no puede convertirse en un punto nuevo de falla total.
        Una página que no se pueda persistir no se lleva puestas a las otras —
        `save_website_properties` la rescata al cerrar, porque el nodo sigue
        devolviendo la lista completa.
        """
        if not props:
            return
        try:
            await _upsert_properties(sb, props, job_id)
            matched, _ = _split_by_criteria(props, filters)
            await _link_job_properties(sb, props, job_id, matched)
        except Exception as exc:
            _log.warning('guardado incremental falló (se reintenta al cerrar): %s', exc)

    async def advance() -> None:
        """Avanza el contador y reporta cada 5. Lo comparten la página que se
        analiza y la que se descarta: si la descartada no avanzara, una
        búsqueda que filtró 500 de 1500 quedaría clavada en 1000/1500 para
        siempre."""
        nonlocal analyzed
        analyzed += 1
        # Un evento cada 5 páginas: con 1500 páginas, uno por página es ruido
        # que el cliente no llega a renderizar. El último siempre se manda.
        if analyzed % 5 == 0 or analyzed == total_pages:
            await emit('running', analyzed, analyzed,
                       f'Analizando páginas {analyzed}/{total_pages}...')

    async def extract(page: dict[str, str]) -> list[NormalizedProperty]:
        nonlocal skipped
        # ANTES del semáforo: una página que no se va a analizar no tiene por
        # qué ocupar un lugar de la concurrencia ni esperar a que se libere.
        if not page_is_worth_extracting(page.get('text', '')):
            skipped += 1
            await advance()
            return []
        async with sem:
            # El corte va ACÁ, antes de la llamada, y no cancelando el `gather`:
            # cancelarlo tiraría también las páginas en vuelo, que ya están
            # pagadas. La que llega con el presupuesto agotado sale vacía y las
            # demás drenan igual de rápido, sin gastar un token.
            if llm_budget_exhausted():
                await _announce_llm_budget_stop(job_id, config)
                return []
            props = await _extract_page_properties(page, sb, job_id)
        await persist(props)
        await advance()
        return props

    if total_pages:
        await emit('running', 0, 0, f'Analizando páginas 0/{total_pages}...')

    batches = await asyncio.gather(*(extract(page) for page in pages))
    results: list[NormalizedProperty] = [prop for batch in batches for prop in batch]

    # Fetch each property's real gallery from its detail page. Only fichas the LLM
    # linked explicitly qualify; the listing page itself would re-yield the mixed pool.
    # Also covers props stuck with a lone og:image from a scraped detail sub-page.
    scraped_urls = {p.get('url') for p in pages}
    pending = [p for p in results
               if len(p.imagenes) < 4 and p.url_origen and p.url_origen not in scraped_urls]
    if pending:
        # `done == total`: las páginas ya están todas analizadas, esto es la
        # cola de fotos. Si mandáramos otro par de números la barra retrocedería.
        await emit('running', total_pages, len(results),
                   f'Buscando fotos de {len(pending)} propiedades...')
        ficha_urls = list(dict.fromkeys(p.url_origen for p in pending))
        # El tope era un `30` clavado acá: con 300 propiedades, 270 salían sin
        # una sola foto. Ahora es un knob, y `0` significa sin tope.
        if settings.FICHA_IMAGE_HARVEST_MAX > 0:
            ficha_urls = ficha_urls[:settings.FICHA_IMAGE_HARVEST_MAX]
        try:
            galleries = await harvest_page_images(ficha_urls)
        except Exception:
            galleries = {}
        for p in pending:
            gallery = galleries.get(p.url_origen or '')
            if gallery and len(gallery) > len(p.imagenes):
                p.imagenes = gallery[:20]

    if total_pages:
        cierre = f'{len(results)} propiedades extraídas'
        if skipped:
            cierre += f' · {skipped} páginas sin propiedades, no analizadas'
        await emit('done', total_pages, len(results), cierre)
    return {'website_properties': results}


async def save_website_properties(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    sb = config['configurable'].get('supabase')
    job_id = state.get('job_id')
    props: list[NormalizedProperty] = state.get('website_properties', [])

    # Dos inmobiliarias publicando la misma propiedad son UNA propiedad. El
    # dedup de portales no cubría este track: todas estas filas llevan
    # `fuente='googlemaps'`, así que para su regla los 552 sitios eran UN
    # catálogo (ver app.services.dedup).
    #
    # Se colapsa acá ADEMÁS de al servir, para que el `property_batch` que el
    # cliente ve durante la corrida cuente lo mismo que la lista final: decir
    # "300 encontradas" y después mostrar 180 se lee como resultados perdidos.
    props = collapse_duplicates(props)

    # Save EVERYTHING scraped to the global catalog, regardless of criteria
    await _upsert_properties(sb, props, job_id)

    # Everything scraped is a search result; the matched ones lead the list
    filters = state.get('filters')
    matched, rest = _split_by_criteria(props, filters)
    ordered = matched + rest
    await _link_job_properties(sb, ordered, job_id, matched)

    if ordered:
        await adispatch_custom_event('property_batch', {
            'event': 'property_batch', 'source': 'local',
            'count': len(matched), 'total': len(ordered),
            'properties': [p.model_dump() for p in ordered],
        }, config=config)

    # Emit done only if no instagram track is running (never dispatched in
    # googlemaps-only test mode, regardless of agency handles)
    agencies = state.get('agencies', [])
    selected_ids = state.get('selected_agency_ids', [])
    agency_map = {a.id: a for a in agencies}
    has_instagram = not settings.SCRAPE_GOOGLEMAPS_ONLY and any(
        agency_map[aid].instagram_handle
        for aid in selected_ids
        if aid in agency_map
    )
    if not has_instagram:
        portal_count = len(state.get('normalized_properties', []))
        total = portal_count + len(ordered)
        await adispatch_custom_event('done', {
            'event': 'done', 'job_id': job_id, 'total_count': total,
            'sources': [*list(PORTAL_SOURCES), 'local'],
        }, config=config)
    return {'website_properties': ordered}


async def run_instagram_scraper(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    handle: str = state['handle']
    service = get_apify_service()

    async def on_progress(src: str, status: str, count: int) -> None:
        await adispatch_custom_event('progress', {
            'event': 'progress', 'source': f'instagram:{handle}',
            'status': status, 'count': count,
            'message': {
                'running': f'Buscando propiedades en @{handle}...',
                'done': f'{count} posts de @{handle}',
                'error': f'Error en @{handle}',
            }.get(status, ''),
        }, config=config)

    try:
        # Los N `Send` ya existen todos; el semáforo decide cuántos runs de
        # Apify corren a la vez. Mismo criterio que el fan-out de sitios web.
        async with _get_instagram_semaphore():
            raws = await service.scrape_instagram_profile(handle, on_progress)
    except ApifyBudgetExceeded as exc:
        await _announce_budget_stop(exc, state.get('job_id'), config)
        return {'instagram_posts': []}
    except Exception as exc:
        await adispatch_custom_event('error', {
            'event': 'error', 'source': f'instagram:{handle}', 'message': str(exc), 'recoverable': True,
        }, config=config)
        return {'instagram_posts': [], 'errors': [f'instagram:{handle}: {exc}']}
    return {'instagram_posts': [r.model_dump() for r in raws]}


async def extract_instagram_properties_llm(state: ScrapingState, config: RunnableConfig) -> dict[str, Any]:
    posts: list[dict] = state.get('instagram_posts', [])
    results: list[NormalizedProperty] = []
    total_posts = len(posts)
    sb = config['configurable'].get('supabase')
    job_id = state.get('job_id')

    for post_idx, post in enumerate(posts, 1):
        await adispatch_custom_event('progress', {
            'event': 'progress', 'source': 'extraccion:instagram', 'status': 'running',
            'count': len(results),
            'message': f'Analizando post {post_idx}/{total_posts}...',
        }, config=config)
        caption = post.get('titulo', '')
        if not caption or len(caption) < 20:
            continue
        # Instagram come del MISMO dólar que los sitios web: el presupuesto es
        # de la búsqueda, no de cada loop.
        if llm_budget_exhausted():
            await _announce_llm_budget_stop(job_id, config)
            break
        try:
            msg = await _client.messages.create(  # type: ignore[call-overload]
                model=MODEL,
                max_tokens=512,
                system=INSTAGRAM_SYSTEM_PROMPT,
                tools=[INSTAGRAM_EXTRACT_TOOL],  # type: ignore[list-item]
                tool_choice={'type': 'tool', 'name': 'extract_property_from_instagram'},
                messages=[{'role': 'user', 'content': caption}],
            )
        except Exception:
            continue

        # Booked before the es_propiedad check: classifying a post as "not a listing"
        # costs exactly as much as classifying it as one.
        await record_llm_usage(
            sb,
            scope=SCOPE_EXTRACT_INSTAGRAM,
            model=MODEL,
            usage=getattr(msg, 'usage', None),
            job_id=job_id,
            url=post.get('url') or None,
        )

        tool_use = next((b for b in msg.content if b.type == 'tool_use'), None)
        if not tool_use or not tool_use.input.get('es_propiedad'):
            continue
        data = tool_use.input
        # Mismo contrato que el loop de sitios web: un post que no se puede
        # armar vale cero posts, no cero búsqueda. La coerción de arriba cubre
        # lo conocido; esto cubre lo que todavía no vimos.
        try:
            prop_ig = NormalizedProperty(
                titulo=data.get('descripcion', caption)[:120],
                descripcion=data.get('descripcion') or caption,
                direccion=data.get('direccion_zona') or '',
                direccion_norm=_normalize_address(data.get('direccion_zona') or ''),
                precio=_llm_float(data.get('precio')),
                moneda=data.get('moneda') or 'USD',  # type: ignore[arg-type]
                tipo_operacion=data.get('tipo_operacion') or 'venta',  # type: ignore[arg-type]
                tipo_propiedad=_normalize_tipo_propiedad(data.get('tipo_propiedad')),
                ambientes=_llm_int(data.get('ambientes')),
                banos=_llm_int(data.get('banos')),
                cocheras=_llm_int(data.get('cocheras')),
                piso=_llm_int(data.get('piso')),
                expensas=_llm_float(data.get('expensas')),
                m2_total=_llm_float(data.get('m2')),
                amenities=data.get('amenities') or [],
                imagenes=post.get('imagenes') or [],
                fuente='instagram',
                url_origen=post.get('url_origen'),
            )
        except Exception as exc:
            _log.warning('post de Instagram descartado (%s): %s', post.get('url_origen'), exc)
            continue
        results.append(prop_ig)
    if total_posts:
        await adispatch_custom_event('progress', {
            'event': 'progress', 'source': 'extraccion:instagram', 'status': 'done',
            'count': len(results),
            'message': f'{len(results)} propiedades extraídas de Instagram',
        }, config=config)
    return {'instagram_properties': results}


async def save_instagram_properties(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    sb = config['configurable'].get('supabase')
    job_id = state.get('job_id')
    props: list[NormalizedProperty] = state.get('instagram_properties', [])

    # Mismo motivo que en el track de sitios web: dos perfiles que postean la
    # misma propiedad son una sola, y este track tampoco pasaba por el dedup.
    props = collapse_duplicates(props)

    # Save EVERYTHING scraped to the global catalog, regardless of criteria
    await _upsert_properties(sb, props, job_id)

    # Everything scraped is a search result; the matched ones lead the list
    filters = state.get('filters')
    matched, rest = _split_by_criteria(props, filters)
    ordered = matched + rest
    await _link_job_properties(sb, ordered, job_id, matched)

    if ordered:
        await adispatch_custom_event('property_batch', {
            'event': 'property_batch', 'source': 'instagram',
            'count': len(matched), 'total': len(ordered),
            'properties': [p.model_dump() for p in ordered],
        }, config=config)

    portal_count = len(state.get('normalized_properties', []))
    website_count = len(state.get('website_properties', []))
    total = portal_count + website_count + len(ordered)
    await adispatch_custom_event('done', {
        'event': 'done', 'job_id': job_id, 'total_count': total,
        'sources': [*list(PORTAL_SOURCES), 'local', 'instagram'],
    }, config=config)
    return {}


async def no_sources(state: ScrapingState, config: RunnableConfig) -> dict[str, Any]:
    """Terminal node for a source selection that leaves nothing to scrape —
    keeps the SSE stream well-formed (message + `done`) instead of ending on an
    empty fan-out."""
    await adispatch_custom_event('agent_message', {
        'event': 'agent_message',
        'message': 'Ninguna de las fuentes que elegiste está disponible para esta búsqueda.',
    }, config=config)
    await adispatch_custom_event('done', {
        'event': 'done',
        'job_id': state.get('job_id'),
        'total_count': 0,
        'sources': [],
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
