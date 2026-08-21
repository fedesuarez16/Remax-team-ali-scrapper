from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Request

from app.services.zona import normalize_zona

router = APIRouter()

# A paste is a hand-curated list, not an import job. The cap keeps one request
# (and its dedupe read) bounded instead of letting a stray paste stall the API.
MAX_BULK_URLS = 500

# URLs pasted in bulk arrive one-per-line, but a copy from a spreadsheet or a
# chat message can also be comma- or space-separated.
_URL_SEPARATORS = re.compile(r'[\s,;]+')


def _canonical_url(url: str) -> str:
    """Dedupe key: the same page pasted with and without a trailing slash is
    one source, not two. Only the trailing slash is normalized — query strings
    and casing can be load-bearing on portal URLs, so they stay untouched.
    """
    return url.rstrip('/')


def _derive_nombre(url: str) -> str:
    """Best-effort label for a URL pasted without one.

    Host plus last path segment: pasting 70 RE/MAX office links would otherwise
    produce 70 rows all named 'remax.com.ar'. Falls back to the raw URL if it
    has no parseable host. The user can rename it afterwards via PATCH.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix('www.')
    if not host:
        return url[:120]

    segments = [s for s in parsed.path.split('/') if s]
    if not segments:
        return host

    last = segments[-1].split('.')[0] if '.' in segments[-1] else segments[-1]
    return f'{host}/{last}'[:120] if last else host


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


@router.post('/bulk')
async def bulk_create_manual_sources(request: Request, body: dict) -> dict[str, Any]:
    """Register many URLs at once, one source per URL, no nombre and no zona.

    `urls` takes either raw pasted text (newline/comma/space separated) or a
    list. Each row gets a nombre derived from its URL — the list UI and the
    aria-labels need something to show — and lands with no zona, so it is only
    consulted by unscoped searches until someone files it via PATCH.

    Partial success is the norm: invalid and already-registered URLs are
    reported back instead of aborting the batch.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'agregadas': 0, 'duplicadas': [], 'invalidas': [], 'error': 'Supabase no configurado'}

    raw = body.get('urls')
    if isinstance(raw, list):
        candidates = [str(u).strip() for u in raw]
    else:
        candidates = _URL_SEPARATORS.split(str(raw or ''))
    candidates = [c for c in (c.strip() for c in candidates) if c]

    if not candidates:
        return {'agregadas': 0, 'duplicadas': [], 'invalidas': [], 'error': 'No pegaste ninguna URL'}
    if len(candidates) > MAX_BULK_URLS:
        return {
            'agregadas': 0, 'duplicadas': [], 'invalidas': [],
            'error': f'Máximo {MAX_BULK_URLS} URLs por vez (pegaste {len(candidates)})',
        }

    invalidas = [c for c in candidates if not c.startswith(('http://', 'https://'))]
    validas = [c for c in candidates if c.startswith(('http://', 'https://'))]

    try:
        res = await sb.table('manual_sources').select('url').execute()
        ya_cargadas = {_canonical_url(r['url']) for r in (res.data or []) if r.get('url')}
    except Exception as e:
        return {'agregadas': 0, 'duplicadas': [], 'invalidas': invalidas, 'error': str(e)}

    duplicadas: list[str] = []
    nuevas: list[dict[str, Any]] = []
    vistas: set[str] = set()
    for url in validas:
        key = _canonical_url(url)
        if key in ya_cargadas or key in vistas:
            duplicadas.append(url)
            continue
        vistas.add(key)
        nuevas.append({
            'nombre': _derive_nombre(url),
            'url': url,
            'zona': None,
            'zona_norm': None,
        })

    if not nuevas:
        return {'agregadas': 0, 'duplicadas': duplicadas, 'invalidas': invalidas}

    try:
        await sb.table('manual_sources').insert(nuevas).execute()
    except Exception as e:
        return {'agregadas': 0, 'duplicadas': duplicadas, 'invalidas': invalidas, 'error': str(e)}

    return {'agregadas': len(nuevas), 'duplicadas': duplicadas, 'invalidas': invalidas}


@router.patch('/{source_id}')
async def update_manual_source(request: Request, source_id: str, body: dict) -> dict[str, Any]:
    """Edit a source's nombre/url/zona and/or toggle it on/off. `activo=false`
    excludes it from every future search — the fan-out only reads
    `.eq('activo', True)` (see `_fetch_active_manual_sources` in
    app.graphs.extraction.nodes).

    Presence-checked (`'nombre' in body`) so editing one field doesn't clobber
    the others — same convention as the saved-zones PATCH.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'source': None, 'error': 'Supabase no configurado'}

    payload: dict[str, Any] = {}
    if 'nombre' in body:
        nombre = (body.get('nombre') or '').strip()
        if not nombre:
            return {'source': None, 'error': 'nombre no puede estar vacío'}
        payload['nombre'] = nombre
    if 'url' in body:
        url = (body.get('url') or '').strip()
        if not url.startswith(('http://', 'https://')):
            return {'source': None, 'error': 'url debe empezar con http:// o https://'}
        payload['url'] = url
    if 'zona' in body:
        # `zona_norm` is the key every zona-scoped query filters on, so it has
        # to be rewritten alongside `zona` — otherwise the source silently
        # stays in its old bucket. Blank clears both (standalone portal).
        zona = (body.get('zona') or '').strip()
        payload['zona'] = zona or None
        payload['zona_norm'] = normalize_zona(zona) if zona else None
    if 'activo' in body:
        payload['activo'] = bool(body['activo'])

    if not payload:
        return {'source': None, 'error': 'nada para actualizar'}

    try:
        res = (
            await sb.table('manual_sources')
            .update(payload)
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
