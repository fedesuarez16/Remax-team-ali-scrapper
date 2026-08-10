from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()

MIN_VERTICES = 3


def _clean_polygon(raw: Any) -> list[list[float]] | None:
    """Validate + normalize a delineation into plain `[[lat, lng], ...]` floats.

    Returns None when the geometry is unusable — fewer than 3 vertices, a
    non-numeric coordinate, or coordinates outside the lat/lng domain. The map
    renders these straight into a Leaflet `Polygon`, so garbage here breaks the
    UI later instead of at write time.
    """
    if not isinstance(raw, list) or len(raw) < MIN_VERTICES:
        return None

    cleaned: list[list[float]] = []
    for vertex in raw:
        if not isinstance(vertex, (list, tuple)) or len(vertex) != 2:
            return None
        lat, lng = vertex
        # bool is an int subclass — reject it explicitly, it's never a coord.
        if isinstance(lat, bool) or isinstance(lng, bool):
            return None
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            return None
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
            return None
        cleaned.append([float(lat), float(lng)])
    return cleaned


@router.get('')
async def list_saved_zones(request: Request) -> dict[str, Any]:
    """List saved zone delineations, most-recent-first."""
    sb = request.app.state.supabase
    if sb is None:
        return {'zones': [], 'total': 0, 'error': 'Supabase no configurado'}

    try:
        res = await (
            sb.table('saved_zones')
            .select('*')
            .order('created_at', desc=True)
            .execute()
        )
        zones = res.data or []
        return {'zones': zones, 'total': len(zones)}
    except Exception as e:
        return {'zones': [], 'total': 0, 'error': str(e)}


@router.post('')
async def create_saved_zone(request: Request, body: dict) -> dict[str, Any]:
    """Save a named delineation — geometry only, never search results.

    Unlike `search_history`, nothing here is tied to a job: the zone is meant
    to be re-drawn on the map and re-searched as many times as the user wants.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'zone': None, 'error': 'Supabase no configurado'}

    name = (body.get('name') or '').strip()
    if not name:
        return {'zone': None, 'error': 'name es requerido'}

    polygon = _clean_polygon(body.get('polygon'))
    if polygon is None:
        return {'zone': None, 'error': f'polygon inválido (mínimo {MIN_VERTICES} vértices [lat, lng])'}

    try:
        res = await sb.table('saved_zones').insert({'name': name, 'polygon': polygon}).execute()
        zone = res.data[0] if res.data else None
        return {'zone': zone}
    except Exception as e:
        return {'zone': None, 'error': str(e)}


@router.patch('/{zone_id}')
async def update_saved_zone(request: Request, zone_id: str, body: dict) -> dict[str, Any]:
    """Rename a zone and/or replace its delineation.

    Presence-checked (`'name' in body`) so a client can update one field
    without clobbering the other — same convention as search-history PATCH.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'zone': None, 'error': 'Supabase no configurado'}

    payload: dict[str, Any] = {}
    if 'name' in body:
        name = (body.get('name') or '').strip()
        if not name:
            return {'zone': None, 'error': 'name no puede estar vacío'}
        payload['name'] = name
    if 'polygon' in body:
        polygon = _clean_polygon(body.get('polygon'))
        if polygon is None:
            return {'zone': None, 'error': f'polygon inválido (mínimo {MIN_VERTICES} vértices [lat, lng])'}
        payload['polygon'] = polygon

    if not payload:
        return {'zone': None, 'error': 'nada para actualizar'}

    try:
        res = await sb.table('saved_zones').update(payload).eq('id', zone_id).execute()
        rows = res.data or []
        if not rows:
            return {'zone': None, 'error': 'zona no encontrada'}
        return {'zone': rows[0]}
    except Exception as e:
        return {'zone': None, 'error': str(e)}


@router.delete('/{zone_id}')
async def delete_saved_zone(request: Request, zone_id: str) -> dict[str, Any]:
    """Delete a saved zone. Idempotent — deleting an unknown id is not an error."""
    sb = request.app.state.supabase
    if sb is None:
        return {'deleted': False, 'error': 'Supabase no configurado'}

    try:
        await sb.table('saved_zones').delete().eq('id', zone_id).execute()
        return {'deleted': True}
    except Exception as e:
        return {'deleted': False, 'error': str(e)}
