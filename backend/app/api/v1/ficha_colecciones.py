"""Fichas de selección: varias Fichas Propio en UNA ficha para un cliente.

El agente marca N fichas propias y genera una sola página compartible
("Propiedades para Romina García"). La personaliza — título, subtítulo,
comentario por propiedad, orden, mapa — y el cliente, desde el link público,
califica cada propiedad con "Me interesa" / "No me interesa".

Modelo (ver supabase/migrations/20261104000000_ficha_colecciones.sql):
- `ficha_colecciones`      la ficha en sí (cliente, textos, agente, mapa, envío)
- `ficha_coleccion_items`  qué propiedades lleva, en qué orden, el comentario
                           del agente y la calificación del cliente.

La colección NO copia las propiedades: apunta a `properties`. Editar una ficha
propia (fotos, precio) se refleja en todas las selecciones donde aparece, y
borrarla la saca de ellas (FK `on delete cascade` en el item).

Vive en su propio router (`/ficha-colecciones`) para no pelear con el
catch-all `/{property_id}` de `/properties`.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.database import chunk_for_in_filter

router = APIRouter()

COLECCIONES = 'ficha_colecciones'
ITEMS = 'ficha_coleccion_items'

CALIFICACIONES = {'interesa', 'no_interesa'}
# Lo único que el agente puede tocar de la colección por PATCH. `enviada_at`
# tiene su propio endpoint: es un sello del sistema, no un texto editable.
EDITABLES = {'cliente', 'titulo', 'subtitulo', 'agente_email', 'mostrar_mapa'}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean_ids(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(i.strip() for i in raw if isinstance(i, str) and i.strip()))


def _titulo_para(cliente: str) -> str:
    return f'Propiedades para {cliente}' if cliente else 'Propiedades seleccionadas'


async def _fetch_properties(sb: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Propiedades por id. Chunked: el `in_` viaja en la URL."""
    out: dict[str, dict[str, Any]] = {}
    for chunk in chunk_for_in_filter(ids):
        res = await sb.table('properties').select('*').in_('id', chunk).execute()
        for row in res.data or []:
            out[row['id']] = row
    return out


async def _get_coleccion(sb: Any, coleccion_id: str) -> dict[str, Any]:
    res = await sb.table(COLECCIONES).select('*').eq('id', coleccion_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail='La selección no existe o fue eliminada.')
    return dict(res.data[0])


def _require_sb(request: Request) -> Any:
    sb = request.app.state.supabase
    if sb is None:
        raise HTTPException(status_code=503, detail='Supabase no configurado')
    return sb


# ── listar / crear ──────────────────────────────────────────────────────────


@router.get('')
async def list_colecciones(request: Request) -> dict[str, Any]:
    """Todas las selecciones, la más nueva primero, con su resumen: cuántas
    propiedades lleva, cuántas calificó el cliente y una foto de portada.

    Nunca rompe la pestaña: sin Supabase o sin la tabla devuelve lista vacía."""
    sb = request.app.state.supabase
    if sb is None:
        return {'colecciones': [], 'total': 0, 'error': 'Supabase no configurado'}

    try:
        # Las archivadas (borradas desde la UI) no se listan; su link sigue vivo.
        res = await (
            sb.table(COLECCIONES).select('*')
            .is_('archivada_at', 'null')
            .order('created_at', desc=True).execute()
        )
        colecciones = [dict(c) for c in res.data or []]
        ids = [c['id'] for c in colecciones]

        items: list[dict[str, Any]] = []
        for chunk in chunk_for_in_filter(ids):
            r = await sb.table(ITEMS).select('*').in_('coleccion_id', chunk).execute()
            items.extend(r.data or [])

        por_coleccion: dict[str, list[dict[str, Any]]] = {}
        for it in items:
            por_coleccion.setdefault(it['coleccion_id'], []).append(it)

        primeras = {
            cid: min(its, key=lambda i: i.get('orden') or 0)['property_id']
            for cid, its in por_coleccion.items() if its
        }
        props = await _fetch_properties(sb, list(dict.fromkeys(primeras.values())))

        for c in colecciones:
            its = por_coleccion.get(c['id'], [])
            c['total'] = len(its)
            c['interesa'] = sum(1 for i in its if i.get('calificacion') == 'interesa')
            c['no_interesa'] = sum(1 for i in its if i.get('calificacion') == 'no_interesa')
            imagenes = (props.get(primeras.get(c['id'], '')) or {}).get('imagenes') or []
            c['portada'] = imagenes[0] if imagenes else None
        return {'colecciones': colecciones, 'total': len(colecciones)}
    except Exception as e:
        return {'colecciones': [], 'total': 0, 'error': str(e)}


@router.post('')
async def create_coleccion(request: Request, body: dict) -> dict[str, Any]:
    """Crear una selección. Body:
    ``{"property_ids": [...], "cliente": "Romina", "agente_email": "..."}``.

    El orden de `property_ids` es el orden inicial que ve el cliente.
    """
    ids = _clean_ids(body.get('property_ids'))
    if not ids:
        raise HTTPException(status_code=400, detail='property_ids requerido')
    sb = _require_sb(request)

    cliente = (body.get('cliente') or '').strip()
    agente_email = (body.get('agente_email') or '').strip() or None

    res = await sb.table(COLECCIONES).insert({
        'cliente': cliente or None,
        'titulo': _titulo_para(cliente),
        'agente_email': agente_email,
    }).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail='No se pudo crear la selección')
    coleccion = dict(res.data[0])

    await sb.table(ITEMS).insert([
        {'coleccion_id': coleccion['id'], 'property_id': pid, 'orden': i}
        for i, pid in enumerate(ids)
    ]).execute()
    return {'coleccion': coleccion}


# ── leer ────────────────────────────────────────────────────────────────────


@router.get('/{coleccion_id}')
async def get_coleccion(request: Request, coleccion_id: str) -> dict[str, Any]:
    """La selección con sus items en orden, cada uno con su propiedad completa.

    Es lo que lee la página pública: un item cuya propiedad ya no existe se
    omite en vez de mostrarse como una card vacía."""
    sb = _require_sb(request)
    coleccion = await _get_coleccion(sb, coleccion_id)

    res = await sb.table(ITEMS).select('*').eq('coleccion_id', coleccion_id).order('orden').execute()
    items = sorted((dict(i) for i in res.data or []), key=lambda i: i.get('orden') or 0)
    props = await _fetch_properties(sb, [i['property_id'] for i in items])

    out = []
    for it in items:
        prop = props.get(it['property_id'])
        if prop is None:
            continue
        it.setdefault('comentario', None)
        it.setdefault('calificacion', None)
        out.append({**it, 'property': prop})
    return {'coleccion': coleccion, 'items': out}


# ── personalizar (agente) ───────────────────────────────────────────────────


@router.patch('/{coleccion_id}')
async def update_coleccion(request: Request, coleccion_id: str, body: dict) -> dict[str, Any]:
    patch = {k: v for k, v in body.items() if k in EDITABLES}
    if not patch:
        raise HTTPException(status_code=400, detail='Nada para actualizar')
    for k in ('cliente', 'titulo', 'subtitulo'):
        if isinstance(patch.get(k), str):
            patch[k] = patch[k].strip() or None
    if 'mostrar_mapa' in patch:
        patch['mostrar_mapa'] = bool(patch['mostrar_mapa'])
    patch['updated_at'] = _now()

    sb = _require_sb(request)
    res = await sb.table(COLECCIONES).update(patch).eq('id', coleccion_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail='La selección no existe')
    return {'coleccion': res.data[0]}


@router.put('/{coleccion_id}/orden')
async def reorder_coleccion(request: Request, coleccion_id: str, body: dict) -> dict[str, Any]:
    """Reescribe el orden. Body: ``{"property_ids": [...]}`` en el orden nuevo.

    Un update por item: una selección son decenas de propiedades como mucho,
    y PostgREST no tiene un update masivo con valores distintos por fila."""
    ids = _clean_ids(body.get('property_ids'))
    if not ids:
        raise HTTPException(status_code=400, detail='property_ids requerido')
    sb = _require_sb(request)
    for i, pid in enumerate(ids):
        await (
            sb.table(ITEMS).update({'orden': i})
            .eq('coleccion_id', coleccion_id).eq('property_id', pid).execute()
        )
    return {'ok': True}


@router.patch('/{coleccion_id}/items/{property_id}')
async def update_item(
    request: Request, coleccion_id: str, property_id: str, body: dict,
) -> dict[str, Any]:
    """Comentario del agente para el cliente sobre UNA propiedad."""
    if 'comentario' not in body:
        raise HTTPException(status_code=400, detail='comentario requerido')
    raw = body.get('comentario')
    comentario = raw.strip() or None if isinstance(raw, str) else None

    sb = _require_sb(request)
    res = await (
        sb.table(ITEMS).update({'comentario': comentario})
        .eq('coleccion_id', coleccion_id).eq('property_id', property_id).execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail='La propiedad no está en esta selección')
    return {'item': res.data[0]}


@router.delete('/{coleccion_id}/items/{property_id}')
async def remove_item(request: Request, coleccion_id: str, property_id: str) -> dict[str, Any]:
    """Saca una propiedad de la selección. La ficha propia NO se borra."""
    sb = _require_sb(request)
    await (
        sb.table(ITEMS).delete()
        .eq('coleccion_id', coleccion_id).eq('property_id', property_id).execute()
    )
    return {'deleted': True}


# ── calificar (cliente) ─────────────────────────────────────────────────────


@router.post('/{coleccion_id}/items/{property_id}/calificacion')
async def calificar_item(
    request: Request, coleccion_id: str, property_id: str, body: dict,
) -> dict[str, Any]:
    """El cliente califica una propiedad. ``null`` deshace la calificación.

    Es el único endpoint que escribe desde la página pública: por eso valida
    el valor contra una lista cerrada y exige que el item exista."""
    calificacion = body.get('calificacion')
    if calificacion is not None and calificacion not in CALIFICACIONES:
        raise HTTPException(status_code=400, detail='calificacion inválida')

    sb = _require_sb(request)
    res = await (
        sb.table(ITEMS)
        .update({
            'calificacion': calificacion,
            'calificada_at': _now() if calificacion else None,
        })
        .eq('coleccion_id', coleccion_id).eq('property_id', property_id).execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail='La propiedad no está en esta selección')
    return {'item': res.data[0]}


# ── enviar / borrar ─────────────────────────────────────────────────────────


@router.post('/{coleccion_id}/enviada')
async def marcar_enviada(request: Request, coleccion_id: str, body: dict) -> dict[str, Any]:
    enviada = body.get('enviada', True) is not False
    sb = _require_sb(request)
    res = await (
        sb.table(COLECCIONES)
        .update({'enviada_at': _now() if enviada else None})
        .eq('id', coleccion_id).execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail='La selección no existe')
    return {'coleccion': res.data[0]}


@router.delete('/{coleccion_id}')
async def delete_coleccion(request: Request, coleccion_id: str) -> dict[str, Any]:
    """Borrado LÓGICO: sella `archivada_at` y la selección desaparece de la
    lista del agente, pero la fila y sus items quedan, así el link `/s/{id}`
    que ya tiene el cliente sigue andando. Idempotente: re-borrar no pisa la
    fecha original."""
    sb = _require_sb(request)
    await (
        sb.table(COLECCIONES)
        .update({'archivada_at': _now()})
        .eq('id', coleccion_id).is_('archivada_at', 'null').execute()
    )
    return {'deleted': True}
