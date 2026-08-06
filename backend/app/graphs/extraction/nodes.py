from __future__ import annotations

import asyncio
from typing import Any

from anthropic import AsyncAnthropic
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.runnables import RunnableConfig
from langgraph.types import Send, interrupt

from app.core.config import settings
from app.models.property import Agency, NormalizedProperty, ScrapingFilters
from app.graphs.extraction.state import ScrapingState
from app.graphs.extraction.tools import (
    EXTRACT_FILTERS_TOOL, INSTAGRAM_EXTRACT_TOOL, INSTAGRAM_SYSTEM_PROMPT, SYSTEM_PROMPT,
)
from app.services.apify import PORTAL_SOURCES, get_apify_service, harvest_page_images
from app.services.zona import (
    address_fingerprint as _address_fingerprint,
    normalize_address as _normalize_address,
    normalize_zona as _normalize_zona,
)

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
    }


def _env_allowed_sources() -> tuple[str, ...]:
    """Portals this deployment is allowed to hit at all, before the user's pick."""
    if settings.APIFY_DISABLED:
        return ('mercadolibre', 'inmobusqueda', 'mudafy')  # direct httpx, no Apify actor
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
    descubrir_agencias = buscar_inmobiliarias and not selection['zona_inmobiliarias']

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
    cached = await _read_cached_agencies(sb, zona_norm)
    if len(cached) >= _AGENCY_CACHE_MIN_ROWS:
        await on_progress('googlemaps', 'running', 0)
        await on_progress('googlemaps', 'done', len(cached))
        return {'agencies': cached}

    # ── Cache miss → pay Apify (as today) ────────────────────────────────────
    try:
        agencies = await service.scrape_agencies(zona, on_progress)
    except Exception as exc:
        await adispatch_custom_event('error', {
            'event': 'error', 'source': 'googlemaps', 'message': str(exc), 'recoverable': True,
        }, config=config)
        return {'agencies': [], 'errors': [f'googlemaps: {exc}']}

    # ── Write-behind (awaited) then adopt DB ids so selection round-trips ────
    try:
        await _upsert_agencies(sb, agencies, zona_norm)
        fresh = await _read_cached_agencies(sb, zona_norm)
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
    seen: set[tuple[Any, ...]] = set()
    unique: list[NormalizedProperty] = []
    for p in state.get('normalized_properties', []):
        key = _dedup_key(p)
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return {'normalized_properties': unique}


def _matches_filters(p: NormalizedProperty, f: ScrapingFilters | None) -> bool:
    """Search-result criteria. Missing data on a property never excludes it."""
    if f is None:
        return True
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


def _prop_to_dict(p: NormalizedProperty, job_id: str | None) -> dict[str, Any]:
    return {
        'titulo': p.titulo, 'descripcion': p.descripcion,
        'direccion': p.direccion, 'direccion_norm': p.direccion_norm,
        'precio': float(p.precio) if p.precio is not None else None,
        'moneda': p.moneda, 'tipo_operacion': p.tipo_operacion, 'tipo_propiedad': p.tipo_propiedad,
        'ambientes': p.ambientes,
        'banos': p.banos,
        'cocheras': p.cocheras,
        'piso': p.piso,
        'expensas': float(p.expensas) if p.expensas is not None else None,
        'm2_total': float(p.m2_total) if p.m2_total is not None else None,
        'm2_cubiertos': float(p.m2_cubiertos) if p.m2_cubiertos is not None else None,
        'antiguedad': p.antiguedad, 'amenities': p.amenities, 'imagenes': p.imagenes,
        'fuente': p.fuente, 'url_origen': p.url_origen, 'scraping_job_id': job_id,
        'confianza_extraccion': float(p.confianza_extraccion),
    }


_AGENCY_CACHE_MIN_ROWS = 1  # >=1 fresh row for the zona → serve from cache, skip Apify


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


async def _read_cached_agencies(sb: Any, zona_norm: str) -> list[Agency]:
    if sb is None or not zona_norm:
        return []
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=settings.AGENCY_CACHE_TTL_DAYS)).isoformat()
    res = await (
        sb.table('real_estate_agencies')
        .select('*')
        .eq('zona_norm', zona_norm)
        .gte('scraped_at', cutoff)
        .execute()
    )
    return [_agency_row_to_model(r) for r in (res.data or [])]


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
        res = await sb.table('properties').select(
            'id,direccion,precio,tipo_operacion,imagenes'
        ).in_('direccion', list({d for d, _, _ in scraped})).execute()

        pending: list[tuple[str, list[str]]] = []
        for row in (res.data or []):
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


async def _upsert_properties(sb: Any, props: list[NormalizedProperty], job_id: str | None) -> None:
    if sb is None or not props:
        return
    data = [_prop_to_dict(p, job_id) for p in props]
    try:
        # insert-ignore: existing rows keep whatever the ficha editor curated.
        # `_fill_missing_images` then covers the rows that have no gallery yet.
        await sb.table('properties').upsert(
            data, on_conflict='direccion,precio,tipo_operacion', ignore_duplicates=True
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

        priced = [p for p in props if p.precio is not None]
        null_priced = [p for p in props if p.precio is None]

        id_flags: dict[str, bool] = {}

        if priced:
            direcciones = list({p.direccion for p in priced})
            res = await sb.table('properties').select(
                'id,direccion,precio,tipo_operacion'
            ).in_('direccion', direcciones).execute()
            priced_triples = {
                (p.direccion, float(p.precio), p.tipo_operacion)
                for p in priced
            }
            for row in (res.data or []):
                row_precio = float(row['precio']) if row['precio'] is not None else None
                triple = (row['direccion'], row_precio, row['tipo_operacion'])
                if triple in priced_triples:
                    id_flags[row['id']] = triple in matched_triples

        if null_priced:
            direcciones_null = list({p.direccion for p in null_priced})
            res_null = await sb.table('properties').select(
                'id,direccion,tipo_operacion'
            ).in_('direccion', direcciones_null).is_('precio', 'null').execute()
            null_pairs = {(p.direccion, p.tipo_operacion) for p in null_priced}
            for row in (res_null.data or []):
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

async def _fetch_active_manual_sources(sb: Any, zona: str | None = None) -> list[dict]:
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
    try:
        query = sb.table('manual_sources').select('nombre,url').eq('activo', True)
        if zona and zona.strip():
            query = query.eq('zona_norm', _normalize_zona(zona))
        res = await query.execute()
        return res.data or []
    except Exception:
        return []


async def review_agencies(state: ScrapingState, config: RunnableConfig) -> dict[str, Any]:
    agencies = state.get('agencies', [])
    sb = config['configurable'].get('supabase')
    selection = _read_selection(state)
    # Portales-only search: the inmobiliarias registry is never consulted.
    manual_sources = (
        await _fetch_active_manual_sources(sb, selection['zona_inmobiliarias'])
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

    if agencies:
        await adispatch_custom_event('agencies_review', {
            'event': 'agencies_review',
            'agencies': [a.model_dump() for a in agencies],
            'message': f'Encontré {len(agencies)} inmobiliarias locales. Seleccioná las que querés incluir para buscar propiedades en sus sitios web.',
        }, config=config)

        # INTERRUPT — graph pauses here, resumes when user sends selected_agency_ids
        selected: list[str] = interrupt({'type': 'agency_selection'})
    else:
        selected = []

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

    for a in selected_agencies:
        if a.sitio_web and websites_sent < settings.MAX_WEBSITE_URLS:
            sends.append(Send('run_website_scraper', {'nombre': a.nombre, 'url': a.sitio_web, 'job_id': job_id}))
            websites_sent += 1
        if a.instagram_handle and not settings.SCRAPE_GOOGLEMAPS_ONLY:
            sends.append(Send('run_instagram_scraper', {'nombre': a.nombre, 'handle': a.instagram_handle, 'job_id': job_id}))

    # Manually-registered sources reach the SAME website-scraping pipeline,
    # regardless of whether any agency was selected above — they share the
    # MAX_WEBSITE_URLS cap with agency websites.
    for src in manual_sources:
        if websites_sent >= settings.MAX_WEBSITE_URLS:
            break
        sends.append(Send('run_website_scraper', {'nombre': src['nombre'], 'url': src['url'], 'job_id': job_id}))
        websites_sent += 1

    return sends if sends else 'no_websites'


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


async def extract_website_properties_llm(state: ScrapingState, config: RunnableConfig) -> dict[str, Any]:
    pages: list[dict[str, str]] = state.get('website_pages', [])
    results: list[NormalizedProperty] = []
    total_pages = len(pages)

    for page_idx, page in enumerate(pages, 1):
        await adispatch_custom_event('progress', {
            'event': 'progress', 'source': 'extraccion', 'status': 'running',
            'count': len(results),
            'message': f'Analizando página {page_idx}/{total_pages}...',
        }, config=config)
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

        page_images: list[str] = page.get('images') or []
        page_props: list[NormalizedProperty] = []
        page_url = page.get('url') or ''

        for prop in (tool_use.input.get('propiedades') or []):
            filled = sum(1 for f in ['precio', 'tipo_operacion', 'tipo_propiedad', 'ambientes', 'm2', 'direccion']
                         if prop.get(f) is not None)
            confianza = min(1.0, filled / 6)
            page_props.append(NormalizedProperty(
                titulo=prop.get('titulo') or '',
                descripcion=prop.get('descripcion'),
                direccion=prop.get('direccion') or '',
                direccion_norm=_normalize_address(prop.get('direccion') or ''),
                precio=prop.get('precio'),
                moneda=prop.get('moneda') or 'USD',  # type: ignore[arg-type]
                tipo_operacion=prop.get('tipo_operacion') or 'venta',  # type: ignore[arg-type]
                tipo_propiedad=_normalize_tipo_propiedad(prop.get('tipo_propiedad')),
                ambientes=prop.get('ambientes'),
                banos=prop.get('banos'),
                cocheras=prop.get('cocheras'),
                piso=prop.get('piso'),
                expensas=prop.get('expensas'),
                amenities=prop.get('amenities') or [],
                m2_total=prop.get('m2'),
                fuente='googlemaps',
                url_origen=prop.get('url_ficha') or page.get('url'),
                confianza_extraccion=confianza,
            ))

        # A single-property page (a listing detail/ficha) owns ALL its images.
        # On multi-property pages the page-level pool mixes every card's photos in
        # network-arrival order, so positional assignment shuffles galleries between
        # properties — those get their gallery from their own ficha below instead.
        if len(page_props) == 1 and page_images:
            page_props[0].imagenes = page_images[:20]

        results.extend(page_props)

    # Fetch each property's real gallery from its detail page. Only fichas the LLM
    # linked explicitly qualify; the listing page itself would re-yield the mixed pool.
    # Also covers props stuck with a lone og:image from a scraped detail sub-page.
    scraped_urls = {p.get('url') for p in pages}
    pending = [p for p in results
               if len(p.imagenes) < 4 and p.url_origen and p.url_origen not in scraped_urls]
    if pending:
        await adispatch_custom_event('progress', {
            'event': 'progress', 'source': 'extraccion', 'status': 'running',
            'count': len(results),
            'message': f'Buscando fotos de {len(pending)} propiedades...',
        }, config=config)
        ficha_urls = list(dict.fromkeys(p.url_origen for p in pending))[:30]
        try:
            galleries = await harvest_page_images(ficha_urls)
        except Exception:
            galleries = {}
        for p in pending:
            gallery = galleries.get(p.url_origen or '')
            if gallery and len(gallery) > len(p.imagenes):
                p.imagenes = gallery[:20]

    if total_pages:
        await adispatch_custom_event('progress', {
            'event': 'progress', 'source': 'extraccion', 'status': 'done',
            'count': len(results),
            'message': f'{len(results)} propiedades extraídas',
        }, config=config)
    return {'website_properties': results}


async def save_website_properties(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    sb = config['configurable'].get('supabase')
    job_id = state.get('job_id')
    props: list[NormalizedProperty] = state.get('website_properties', [])

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
        raws = await service.scrape_instagram_profile(handle, on_progress)
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

    for post_idx, post in enumerate(posts, 1):
        await adispatch_custom_event('progress', {
            'event': 'progress', 'source': 'extraccion:instagram', 'status': 'running',
            'count': len(results),
            'message': f'Analizando post {post_idx}/{total_posts}...',
        }, config=config)
        caption = post.get('titulo', '')
        if not caption or len(caption) < 20:
            continue
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
        tool_use = next((b for b in msg.content if b.type == 'tool_use'), None)
        if not tool_use or not tool_use.input.get('es_propiedad'):
            continue
        data = tool_use.input
        results.append(NormalizedProperty(
            titulo=data.get('descripcion', caption)[:120],
            descripcion=data.get('descripcion') or caption,
            direccion=data.get('direccion_zona') or '',
            direccion_norm=_normalize_address(data.get('direccion_zona') or ''),
            precio=data.get('precio'),
            moneda=data.get('moneda') or 'USD',  # type: ignore[arg-type]
            tipo_operacion=data.get('tipo_operacion') or 'venta',  # type: ignore[arg-type]
            tipo_propiedad=_normalize_tipo_propiedad(data.get('tipo_propiedad')),
            ambientes=data.get('ambientes'),
            banos=data.get('banos'),
            cocheras=data.get('cocheras'),
            piso=data.get('piso'),
            expensas=data.get('expensas'),
            m2_total=data.get('m2'),
            amenities=data.get('amenities') or [],
            imagenes=post.get('imagenes') or [],
            fuente='instagram',
            url_origen=post.get('url_origen'),
        ))
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
