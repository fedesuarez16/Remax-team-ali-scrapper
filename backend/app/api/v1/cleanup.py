"""BOT LIMPIADOR — superficie HTTP.

Manual (`POST /run`) y programado (`GET/PUT /schedule`), más el estado en vivo
y la auditoría de lo que cada corrida borró.

Fire-and-forget en `/run`, mismo patrón que `/properties/geocode/backfill`:
la limpieza puede tardar minutos y el front sigue el avance por `/status`.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request

from app.services.cleaner import (
    DEFAULT_LIMIT,
    cleanup_state,
    read_schedule,
    save_schedule,
)
from app.services.cleaner import run_cleanup as _run_cleanup

router = APIRouter()

# Tope de corridas devueltas por el historial.
_RUNS_PAGE = 20


@router.post('/run')
async def run_now(request: Request, body: dict | None = None) -> dict[str, Any]:
    """Dispara una limpieza manual en segundo plano.

    Body opcional: ``{"limit": 500, "dry_run": false}``. Con ``dry_run`` el bot
    reporta qué borraría sin tocar la base — la forma sana de estrenarlo.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'started': False, 'error': 'Supabase no configurado', 'state': cleanup_state()}

    payload = body or {}
    try:
        limit = int(payload.get('limit') or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT

    asyncio.ensure_future(_run_cleanup(
        sb,
        limit=max(1, limit),
        dry_run=bool(payload.get('dry_run')),
        origen='manual',
    ))
    return {'started': True, 'state': cleanup_state()}


@router.get('/status')
async def status(request: Request) -> dict[str, Any]:
    """Contadores de la corrida en curso (o la última) + cadencia configurada."""
    sb = request.app.state.supabase
    return {'state': cleanup_state(), 'schedule': await read_schedule(sb)}


@router.get('/schedule')
async def get_schedule(request: Request) -> dict[str, Any]:
    sb = request.app.state.supabase
    return {'schedule': await read_schedule(sb)}


@router.put('/schedule')
async def put_schedule(request: Request, body: dict) -> dict[str, Any]:
    """Configura la limpieza automática: cada 7, 30 o X días."""
    sb = request.app.state.supabase
    if sb is None:
        return {'schedule': None, 'error': 'Supabase no configurado'}

    try:
        schedule = await save_schedule(
            sb,
            enabled=bool(body.get('enabled')),
            interval_days=body.get('interval_days'),
        )
    except ValueError as e:
        return {'schedule': None, 'error': str(e)}
    except Exception as e:
        return {'schedule': None, 'error': str(e)}
    return {'schedule': schedule}


@router.get('/runs')
async def list_runs(request: Request, limit: int = _RUNS_PAGE) -> dict[str, Any]:
    """Historial de limpiezas, con la foto de cada propiedad eliminada."""
    sb = request.app.state.supabase
    if sb is None:
        return {'runs': [], 'total': 0}

    try:
        res = await (
            sb.table('cleanup_runs')
            .select('*')
            .order('started_at', desc=True)
            .limit(max(1, limit))
            .execute()
        )
    except Exception as e:
        return {'runs': [], 'total': 0, 'error': str(e)}

    runs = res.data or []
    return {'runs': runs, 'total': len(runs)}
