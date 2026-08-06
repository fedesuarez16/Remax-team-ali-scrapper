"""PORTALES INMOBILIARIOS — estado activo/inactivo del catálogo fijo.

Los portales (Zonaprop, Argenprop, Mercado Libre, RE/MAX, InmoBusqueda, Mudafy) no son
filas que el usuario crea: son un catálogo FIJO en código. Esta superficie lee y
alterna el flag `activo` de cada uno (tabla `portal_settings`), para alimentar
la tarjeta "Portales Inmobiliarios" de la pantalla de Fuentes.

Hoy es estado de configuración/display: el fan-out de búsqueda todavía NO lo
consulta. Ver la nota en la migración 20260804010000_portal_settings.sql.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()

# Catálogo fijo — debe mantenerse en sync con frontend/lib/sources.ts (PORTALES)
# y con PORTAL_SOURCES del scraper. `label` viaja para no duplicar el mapeo en
# el front, pero la fuente de verdad del set de ids sigue siendo el código.
PORTAL_CATALOG: list[dict[str, str]] = [
    {'id': 'zonaprop', 'label': 'Zonaprop'},
    {'id': 'argenprop', 'label': 'Argenprop'},
    {'id': 'mercadolibre', 'label': 'Mercado Libre'},
    {'id': 'remax', 'label': 'RE/MAX'},
    {'id': 'inmobusqueda', 'label': 'InmoBusqueda'},
    {'id': 'mudafy', 'label': 'Mudafy'},
]
_PORTAL_IDS = {p['id'] for p in PORTAL_CATALOG}


@router.get('')
async def list_portals(request: Request) -> dict[str, Any]:
    """Los portales del catálogo con su estado activo/inactivo.

    Un portal sin fila en `portal_settings` (catálogo nuevo, migración vieja) se
    reporta como activo: el default de la tabla y el comportamiento histórico.
    """
    sb = request.app.state.supabase
    if sb is None:
        # Sin base: el catálogo entero, todo activo (comportamiento histórico).
        return {'portals': [{**p, 'activo': True} for p in PORTAL_CATALOG]}

    try:
        res = await sb.table('portal_settings').select('id,activo').execute()
        estado = {row['id']: bool(row['activo']) for row in (res.data or [])}
    except Exception as e:
        return {'portals': [{**p, 'activo': True} for p in PORTAL_CATALOG], 'error': str(e)}

    portals = [{**p, 'activo': estado.get(p['id'], True)} for p in PORTAL_CATALOG]
    return {'portals': portals}


@router.patch('/{portal_id}')
async def update_portal(request: Request, portal_id: str, body: dict) -> dict[str, Any]:
    """Alterna un portal on/off. `portal_id` debe pertenecer al catálogo fijo."""
    sb = request.app.state.supabase
    if sb is None:
        return {'portal': None, 'error': 'Supabase no configurado'}

    if portal_id not in _PORTAL_IDS:
        return {'portal': None, 'error': f'portal desconocido: {portal_id}'}
    if 'activo' not in body:
        return {'portal': None, 'error': 'activo es requerido'}

    activo = bool(body['activo'])
    try:
        res = (
            await sb.table('portal_settings')
            .upsert({
                'id': portal_id,
                'activo': activo,
                'updated_at': datetime.now(timezone.utc).isoformat(),
            })
            .execute()
        )
        row = res.data[0] if res.data else {'id': portal_id, 'activo': activo}
        return {'portal': row}
    except Exception as e:
        return {'portal': None, 'error': str(e)}
