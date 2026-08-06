from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.services.zona import normalize_zona

router = APIRouter()


@router.get('')
async def list_manual_sources(request: Request, zona: str | None = None) -> dict[str, Any]:
    """List manually-registered sources, most-recent-first.

    `zona` restricts the list to the inmobiliarias WE classified into that
    zona (matched on the normalized key, so 'city bell, La Plata' finds
    'City Bell'). Omitted/blank means every registered source.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'sources': [], 'total': 0, 'error': 'Supabase no configurado'}

    zona_norm = normalize_zona(zona) if zona and zona.strip() else None

    try:
        query = sb.table('manual_sources').select('*')
        if zona_norm:
            query = query.eq('zona_norm', zona_norm)
        res = await query.order('created_at', desc=True).execute()
        sources = res.data or []
        return {'sources': sources, 'total': len(sources)}
    except Exception as e:
        return {'sources': [], 'total': 0, 'error': str(e)}


@router.get('/zonas')
async def list_manual_source_zonas(request: Request) -> dict[str, Any]:
    """Zonas that actually have inmobiliarias loaded, alphabetical, with counts
    — this is what the pre-search "elegí la zona" step renders. Sources with no
    zona (standalone portals) are excluded: they belong to no zona bucket.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'zonas': [], 'total': 0, 'error': 'Supabase no configurado'}

    try:
        res = await sb.table('manual_sources').select('zona,zona_norm').execute()
    except Exception as e:
        return {'zonas': [], 'total': 0, 'error': str(e)}

    # Group in Python: PostgREST has no GROUP BY, and the registry is small
    # (hand-curated) so a full read is cheap.
    buckets: dict[str, dict[str, Any]] = {}
    for row in (res.data or []):
        zona = (row.get('zona') or '').strip()
        if not zona:
            continue
        key = row.get('zona_norm') or normalize_zona(zona)
        bucket = buckets.setdefault(key, {'zona': zona, 'zona_norm': key, 'count': 0})
        bucket['count'] += 1

    zonas = sorted(buckets.values(), key=lambda z: z['zona'].lower())
    return {'zonas': zonas, 'total': len(zonas)}


@router.post('')
async def create_manual_source(request: Request, body: dict) -> dict[str, Any]:
    """Register a source (agency or portal website) to fold into the
    existing `run_website_scraper` fan-out on every future search — see
    `app.graphs.extraction.nodes.route_after_review`.

    `zona` is the manual classification: whatever we type here decides which
    zona-scoped searches will consult this source.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'source': None, 'error': 'Supabase no configurado'}

    nombre = (body.get('nombre') or '').strip()
    url = (body.get('url') or '').strip()
    zona = (body.get('zona') or '').strip()
    if not nombre:
        return {'source': None, 'error': 'nombre es requerido'}
    if not url.startswith(('http://', 'https://')):
        return {'source': None, 'error': 'url debe empezar con http:// o https://'}

    payload: dict[str, Any] = {
        'nombre': nombre,
        'url': url,
        'zona': zona or None,
        'zona_norm': normalize_zona(zona) if zona else None,
    }
    try:
        res = await sb.table('manual_sources').insert(payload).execute()
        source = res.data[0] if res.data else None
        return {'source': source}
    except Exception as e:
        return {'source': None, 'error': str(e)}


@router.patch('/{source_id}')
async def update_manual_source(request: Request, source_id: str, body: dict) -> dict[str, Any]:
    """Toggle a source on/off. `activo=false` excludes it from every future
    search — the fan-out only reads `.eq('activo', True)` (see
    `_fetch_active_manual_sources` in app.graphs.extraction.nodes).
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'source': None, 'error': 'Supabase no configurado'}

    if 'activo' not in body:
        return {'source': None, 'error': 'activo es requerido'}

    try:
        res = (
            await sb.table('manual_sources')
            .update({'activo': bool(body['activo'])})
            .eq('id', source_id)
            .execute()
        )
        source = res.data[0] if res.data else None
        return {'source': source}
    except Exception as e:
        return {'source': None, 'error': str(e)}


@router.delete('/{source_id}')
async def delete_manual_source(request: Request, source_id: str) -> dict[str, Any]:
    sb = request.app.state.supabase
    if sb is None:
        return {'deleted': False, 'error': 'Supabase no configurado'}

    try:
        await sb.table('manual_sources').delete().eq('id', source_id).execute()
        return {'deleted': True}
    except Exception as e:
        return {'deleted': False, 'error': str(e)}
