from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request

from app.services.ficha import enrich_ficha as _enrich_ficha
from app.services.importer import import_property_from_url as _import_property
from app.services.geocode import backfill_state as _backfill_state
from app.services.geocode import run_backfill as _run_backfill
from app.services.matcher import match_properties as _match_properties

router = APIRouter()


@router.get('')
async def list_properties(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    fuente: str | None = None,
    tipo_operacion: str | None = None,
    tipo_propiedad: str | None = None,
    moneda: str | None = None,
    precio_min: float | None = None,
    precio_max: float | None = None,
    ambientes_min: int | None = None,
    banos_min: int | None = None,
    cocheras_min: int | None = None,
    m2_min: float | None = None,
    m2_max: float | None = None,
    q: str | None = None,
    enviada: bool | None = None,
) -> dict[str, Any]:
    """List properties from Supabase, most recent first, with pagination.

    Supports the essential Zonaprop-style filters: tipo/operación/fuente/moneda
    as exact matches, precio and m2_total as ranges, ambientes/banos/cocheras
    as minimums. When ``q`` is provided, matches it (case-insensitive, partial)
    against the free-text columns ``titulo``, ``direccion`` and ``direccion_norm``.
    ``enviada`` splits the list by the "ya se la mandé al cliente" mark
    (``enviada_at``): ``true`` keeps only the sent ones, ``false`` only the
    pending ones.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'properties': [], 'total': 0, 'message': 'Supabase no configurado'}

    try:
        query = (
            sb.table('properties')
            .select('*', count='exact')
            .order('created_at', desc=True)
        )
        for column, value in (
            ('fuente', fuente),
            ('tipo_operacion', tipo_operacion),
            ('tipo_propiedad', tipo_propiedad),
            ('moneda', moneda),
        ):
            if value:
                query = query.eq(column, value)
        for column, value in (
            ('precio', precio_min),
            ('ambientes', ambientes_min),
            ('banos', banos_min),
            ('cocheras', cocheras_min),
            ('m2_total', m2_min),
        ):
            if value is not None:
                query = query.gte(column, value)
        for column, value in (('precio', precio_max), ('m2_total', m2_max)):
            if value is not None:
                query = query.lte(column, value)
        if enviada is not None:
            query = (
                query.not_.is_('enviada_at', 'null') if enviada
                else query.is_('enviada_at', 'null')
            )
        if q:
            term = _sanitize_term(q)
            if term:
                query = query.or_(
                    f'titulo.ilike.*{term}*,'
                    f'direccion.ilike.*{term}*,'
                    f'direccion_norm.ilike.*{term}*'
                )
        result = await query.range(offset, offset + limit - 1).execute()
        return {'properties': result.data, 'total': result.count or 0}
    except Exception as e:
        return {'properties': [], 'total': 0, 'error': str(e)}


@router.get('/map')
async def properties_map(request: Request, limit: int = 2000) -> dict[str, Any]:
    """Geocoded properties only — backs the `/map` dashboard tab.

    NOTE: this route MUST stay declared before the `/{property_id}` catch-all
    below, or FastAPI would match `map` as a `property_id`.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'properties': [], 'total': 0, 'missing': 0, 'message': 'Supabase no configurado'}

    try:
        located = await (
            sb.table('properties')
            .select(
                'id,titulo,precio,moneda,direccion,tipo_operacion,tipo_propiedad,'
                'imagenes,lat,lng,fuente,ambientes,banos,cocheras,m2_total,enviada_at'
            )
            .not_.is_('lat', 'null')
            .not_.is_('lng', 'null')
            .limit(limit)
            .execute()
        )
        missing_res = await (
            sb.table('properties').select('id', count='exact').is_('lat', 'null').execute()
        )
        cartera: list[dict[str, Any]] = []
        cartera_missing = 0
        try:
            cartera_res = await (
                sb.table('propiedades')
                .select('id,direccion,zona,valor,tipo_de_propiedad,dormitorios,link,lat,lng')
                .not_.is_('lat', 'null')
                .not_.is_('lng', 'null')
                .limit(limit)
                .execute()
            )
            cartera = cartera_res.data or []
            cartera_missing_res = await (
                sb.table('propiedades').select('id', count='exact').is_('lat', 'null').execute()
            )
            cartera_missing = cartera_missing_res.count or 0
        except Exception:
            # cartera es best-effort: si la tabla propiedades falta o cambia,
            # el mapa de scrapeadas tiene que seguir funcionando igual
            pass
        return {
            'properties': located.data or [],
            'total': len(located.data or []),
            'missing': missing_res.count or 0,
            'cartera': cartera,
            'cartera_missing': cartera_missing,
        }
    except Exception as e:
        return {
            'properties': [], 'total': 0, 'missing': 0,
            'cartera': [], 'cartera_missing': 0, 'error': str(e),
        }


@router.post('/geocode/backfill')
async def trigger_backfill(request: Request, limit: int = 200, force: bool = False) -> dict[str, Any]:
    """Kick off a background geocoding pass; fire-and-forget like scraping.py's pattern."""
    sb = request.app.state.supabase
    if sb is None:
        return {'error': 'Supabase no configurado'}
    asyncio.ensure_future(_run_backfill(sb, limit=limit, force=force))
    return _backfill_state()


@router.get('/geocode/status')
async def geocode_status(request: Request) -> dict[str, Any]:
    return _backfill_state()


# Ficha Propio caps imports per request: each URL costs a fetch + LLM call and
# possibly a headless render, and the batch runs inside one HTTP request.
_IMPORT_MAX_URLS = 10


@router.post('/import')
async def import_properties(request: Request, body: dict) -> dict[str, Any]:
    """Ficha Propio — import portal listing URLs into the own-brand catalog.

    Body: ``{"urls": ["https://..."], "agente_email": "..."}``. Each URL is
    processed independently; a failing page yields a per-URL error without
    aborting the batch. ``agente_email`` (optional) is the team agent the ficha
    is generated under — it is stamped on every property of the batch, also on
    re-imports, because re-generating a ficha means re-assigning its agent.

    NOTE: declared before the `/{property_id}` catch-all, like `/map`.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'results': [], 'error': 'Supabase no configurado'}

    raw = body.get('urls') or []
    urls = list(dict.fromkeys(u.strip() for u in raw if isinstance(u, str) and u.strip()))
    if not urls:
        raise HTTPException(status_code=400, detail='urls requerido')
    if len(urls) > _IMPORT_MAX_URLS:
        raise HTTPException(status_code=400, detail=f'Máximo {_IMPORT_MAX_URLS} links por vez')

    agente_email = body.get('agente_email')
    if not isinstance(agente_email, str) or not agente_email.strip():
        agente_email = None
    else:
        agente_email = agente_email.strip()

    results: list[dict[str, Any]] = []
    for u in urls:
        try:
            r = await _import_property(sb, u)
            prop = r['property']
            if agente_email and prop.get('id') and prop.get('agente_email') != agente_email:
                try:
                    upd = await (
                        sb.table('properties')
                        .update({'agente_email': agente_email})
                        .eq('id', prop['id'])
                        .execute()
                    )
                    if upd.data:
                        prop = upd.data[0]
                except Exception as exc:
                    # Columna sin migrar o tabla caída: la ficha sale igual,
                    # con el titular por defecto en vez del agente elegido.
                    logging.getLogger(__name__).warning('agente_email update failed: %s', exc)
            results.append({
                'url': u, 'status': 'ok',
                'created': r['created'], 'property': prop,
            })
        except Exception as e:
            results.append({'url': u, 'status': 'error', 'error': str(e)})
    return {'results': results}


@router.post('/mark-sent')
async def mark_properties_sent(request: Request, body: dict) -> dict[str, Any]:
    """Marcar (o desmarcar) propiedades como ENVIADAS al cliente.

    Body: ``{"ids": ["..."], "enviada": true}``. Se llama cuando el usuario
    selecciona propiedades y toca "Preparar y enviar": sella ``enviada_at`` con
    el instante del envío para que, al volver a la misma búsqueda, se distingan
    de las que todavía no mandó. ``enviada: false`` limpia la marca.

    NOTE: declarado antes del catch-all `/{property_id}`, igual que `/map`.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'updated': 0, 'properties': [], 'error': 'Supabase no configurado'}

    raw = body.get('ids') or []
    ids = list(dict.fromkeys(
        i.strip() for i in raw if isinstance(i, str) and i.strip()
    ))
    if not ids:
        raise HTTPException(status_code=400, detail='ids requerido')

    enviada = body.get('enviada', True)
    stamp = datetime.now(timezone.utc).isoformat() if enviada else None

    try:
        res = await sb.table('properties').update({'enviada_at': stamp}).in_('id', ids).execute()
    except Exception as e:
        return {'updated': 0, 'properties': [], 'error': str(e)}
    rows = res.data or []
    return {'updated': len(rows), 'properties': rows}


@router.get('/ficha-propio/stats')
async def ficha_propio_stats(request: Request) -> dict[str, Any]:
    """Contadores automáticos de la solapa Ficha Propio.

    Ambos números son DERIVADOS, nunca mantenidos a mano, así que no pueden
    quedar desfasados: la cantidad sale de contar las propiedades `fuente='manual'`
    y el gasto de sumar `llm_usage` con scope `ficha_propio`. Cada generación los
    mueve sola.

    NOTE: declarado antes del catch-all `/{property_id}` — si no, FastAPI
    resolvería 'ficha-propio' como un id de propiedad.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'total_fichas': 0, 'gasto_usd': 0.0, 'llamadas': 0}

    total_fichas = 0
    try:
        res = await (
            sb.table('properties')
            .select('id', count='exact')
            .eq('fuente', 'manual')
            .execute()
        )
        # PostgREST devuelve `count` con count='exact'; si no viene, contamos filas.
        total_fichas = res.count if getattr(res, 'count', None) is not None else len(res.data or [])
    except Exception as exc:
        logging.getLogger(__name__).warning('ficha propio count failed: %s', exc)

    gasto = 0.0
    llamadas = 0
    try:
        res = await sb.table('llm_usage').select('cost_usd').eq('scope', 'ficha_propio').execute()
        rows = res.data or []
        llamadas = len(rows)
        gasto = round(sum(float(r.get('cost_usd') or 0.0) for r in rows), 6)
    except Exception as exc:
        # Migración sin aplicar o tabla caída: el contador de fichas igual se muestra.
        logging.getLogger(__name__).warning('ficha propio spend failed: %s', exc)

    return {'total_fichas': total_fichas, 'gasto_usd': gasto, 'llamadas': llamadas}


@router.get('/{property_id}')
async def get_property(property_id: str, request: Request) -> dict[str, Any]:
    """Fetch a single property by id — backs the shareable per-property ficha page."""
    sb = request.app.state.supabase
    if sb is None:
        return {'property': None, 'error': 'Supabase no configurado'}
    try:
        res = await sb.table('properties').select('*').eq('id', property_id).limit(1).execute()
    except Exception as e:
        return {'property': None, 'error': str(e)}
    if not res.data:
        raise HTTPException(status_code=404, detail='Property not found')
    return {'property': res.data[0]}


# Fields the ficha editor may overwrite. Everything else (id, fuente, url_origen,
# created_at, geocoding, scores) is system-owned and silently dropped from patches.
_EDITABLE_FIELDS = {
    'titulo', 'descripcion', 'direccion', 'precio', 'moneda',
    'tipo_operacion', 'tipo_propiedad', 'ambientes', 'banos', 'cocheras',
    'piso', 'expensas', 'm2_total', 'antiguedad', 'amenities', 'imagenes',
    'destacados', 'agente_email',
}


@router.patch('/{property_id}')
async def update_property(property_id: str, request: Request, body: dict) -> dict[str, Any]:
    """Manually edit a property's ficha — e.g. drop junk images (agency logos)
    harvested by the Google Maps scraper, fix the title or price.

    Only whitelisted fields are applied; the rest of the payload is ignored.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'property': None, 'error': 'Supabase no configurado'}

    patch = {k: v for k, v in body.items() if k in _EDITABLE_FIELDS}
    if not patch:
        raise HTTPException(status_code=400, detail='No editable fields in payload')
    imagenes = patch.get('imagenes')
    if imagenes is not None and (
        not isinstance(imagenes, list) or not all(isinstance(u, str) for u in imagenes)
    ):
        raise HTTPException(status_code=400, detail='imagenes must be a list of URLs')

    try:
        res = await sb.table('properties').update(patch).eq('id', property_id).execute()
    except Exception as e:
        return {'property': None, 'error': str(e)}
    if not res.data:
        raise HTTPException(status_code=404, detail='Property not found')
    return {'property': res.data[0]}


@router.post('/{property_id}/enrich')
async def enrich_property_ficha(property_id: str, request: Request) -> dict[str, Any]:
    """Parse the property's description into amenities + destacados (LLM), cache, return.

    Called when a ficha is prepared. Idempotent — a previously enriched property is
    returned from cache without re-invoking the model.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'property': None, 'error': 'Supabase no configurado'}
    try:
        res = await sb.table('properties').select('*').eq('id', property_id).limit(1).execute()
    except Exception as e:
        return {'property': None, 'error': str(e)}
    if not res.data:
        raise HTTPException(status_code=404, detail='Property not found')
    enriched = await _enrich_ficha(res.data[0], sb)
    return {'property': enriched}


@router.post('/match')
async def match_properties_endpoint(request: Request, body: dict) -> dict[str, Any]:
    """Parse a natural-language query with Claude, then score and rank stored properties."""
    query = (body.get('query') or '').strip()
    if not query:
        return {'results': [], 'total': 0, 'criteria': {}}
    sb = request.app.state.supabase
    if sb is None:
        return {'results': [], 'total': 0, 'error': 'Supabase no configurado'}
    return await _match_properties(query, sb)


def _sanitize_term(raw: str) -> str:
    """Strip characters that would break PostgREST ``or_`` filter syntax.

    Commas separate filter clauses and parentheses group them, so a user term
    containing them must be neutralized before interpolation.
    """
    cleaned = raw.strip().translate(str.maketrans('', '', ',()*'))
    return cleaned[:80]
