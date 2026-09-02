"""Carpetas para agrupar Fichas Propio.

Una ficha propia es una fila de `properties` con `fuente='manual'`. Con el
tiempo se acumulan decenas de fichas de clientes y búsquedas distintas en una
sola lista, y ya no se sabe qué se le mandó a quién. Las carpetas resuelven
eso con el mismo gesto que el historial de búsquedas: nombre libre, "mover a",
y una ficha vive en UNA carpeta o en ninguna.

El vínculo es `properties.ficha_folder_id` (FK `on delete set null`, ver
supabase/migrations/20260902000000_ficha_folders.sql): borrar una carpeta
nunca borra fichas, sólo las devuelve a "Sin carpeta".

Vive en su propio router (`/ficha-folders`) y no bajo `/properties` para no
pelear con el catch-all `/{property_id}` de aquel módulo.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.database import chunk_for_in_filter

router = APIRouter()


@router.get('')
async def list_ficha_folders(request: Request) -> dict[str, Any]:
    """Todas las carpetas, la más nueva primero. Nunca rompe la pestaña: sin
    tabla (migración no aplicada) o sin Supabase devuelve lista vacía + error."""
    sb = request.app.state.supabase
    if sb is None:
        return {'folders': [], 'total': 0, 'error': 'Supabase no configurado'}

    try:
        res = await sb.table('ficha_folders').select('*').order('created_at', desc=True).execute()
        folders = res.data or []
        return {'folders': folders, 'total': len(folders)}
    except Exception as e:
        return {'folders': [], 'total': 0, 'error': str(e)}


@router.post('')
async def create_ficha_folder(request: Request, body: dict) -> dict[str, Any]:
    """Crear una carpeta. Body: ``{"name": "Cliente Pérez"}``."""
    sb = request.app.state.supabase
    if sb is None:
        return {'folder': None, 'error': 'Supabase no configurado'}

    name = (body.get('name') or '').strip()
    if not name:
        raise HTTPException(status_code=400, detail='name es requerido')

    try:
        res = await sb.table('ficha_folders').insert({'name': name}).execute()
        return {'folder': res.data[0] if res.data else None}
    except Exception as e:
        return {'folder': None, 'error': str(e)}


@router.post('/assign')
async def assign_fichas_to_folder(request: Request, body: dict) -> dict[str, Any]:
    """Mover fichas a una carpeta, o sacarlas de cualquiera.

    Body: ``{"ids": ["..."], "folder_id": "uuid" | null}``. Es la acción de la
    barra de selección: el usuario marca N fichas y elige la carpeta. Un solo
    `in_` por lote — mover 30 fichas no puede costar 30 round-trips.

    Sin ids devolvemos 400: un `in_` vacío corre riesgo de barrer la tabla.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'updated': 0, 'properties': [], 'error': 'Supabase no configurado'}

    raw = body.get('ids') or []
    ids = list(dict.fromkeys(i.strip() for i in raw if isinstance(i, str) and i.strip()))
    if not ids:
        raise HTTPException(status_code=400, detail='ids requerido')

    folder_id = body.get('folder_id')

    # Chunked: `in_` values ride in the URL, which PostgREST rejects past ~39 KB
    # (~1000 uuids). Batches already executed are ALREADY moved, so a failure
    # partway reports what really moved — never `0` over updated rows.
    rows: list[dict[str, Any]] = []
    for chunk in chunk_for_in_filter(ids):
        try:
            res = await (
                sb.table('properties')
                .update({'ficha_folder_id': folder_id})
                .in_('id', chunk)
                .execute()
            )
        except Exception as e:
            return {'updated': len(rows), 'properties': rows, 'error': str(e)}
        rows.extend(res.data or [])
    return {'updated': len(rows), 'properties': rows}


@router.delete('/{folder_id}')
async def delete_ficha_folder(request: Request, folder_id: str) -> dict[str, Any]:
    """Borrar una carpeta. Sus fichas NO se borran: quedan sin carpeta.

    La FK `properties.ficha_folder_id` es `on delete set null`, así que Postgres
    suelta las fichas en la misma transacción del delete. Por eso acá no
    tocamos `properties`: hacerlo a mano duplicaría la garantía y abriría el
    caso feo de dejar fichas desasignadas si el delete fallara después.

    Idempotente: borrar un id inexistente no es un error.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'deleted': False, 'error': 'Supabase no configurado'}

    try:
        await sb.table('ficha_folders').delete().eq('id', folder_id).execute()
        return {'deleted': True}
    except Exception as e:
        return {'deleted': False, 'error': str(e)}
