from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


@router.get('')
async def list_properties(
    request: Request,
    limit: int = 200,
    fuente: str | None = None,
    tipo_operacion: str | None = None,
) -> dict[str, Any]:
    """List properties from Supabase, most recent first."""
    sb = request.app.state.supabase
    if sb is None:
        return {'properties': [], 'total': 0, 'message': 'Supabase no configurado'}

    try:
        q = sb.table('properties').select('*').order('created_at', desc=True).limit(limit)
        if fuente:
            q = q.eq('fuente', fuente)
        if tipo_operacion:
            q = q.eq('tipo_operacion', tipo_operacion)
        result = await q.execute()
        return {'properties': result.data, 'total': len(result.data)}
    except Exception as e:
        return {'properties': [], 'total': 0, 'error': str(e)}
