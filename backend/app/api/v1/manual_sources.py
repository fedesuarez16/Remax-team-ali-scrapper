from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


@router.get('')
async def list_manual_sources(request: Request) -> dict[str, Any]:
    """List manually-registered sources, most-recent-first."""
    sb = request.app.state.supabase
    if sb is None:
        return {'sources': [], 'total': 0, 'error': 'Supabase no configurado'}

    try:
        res = await (
            sb.table('manual_sources')
            .select('*')
            .order('created_at', desc=True)
            .execute()
        )
        sources = res.data or []
        return {'sources': sources, 'total': len(sources)}
    except Exception as e:
        return {'sources': [], 'total': 0, 'error': str(e)}


@router.post('')
async def create_manual_source(request: Request, body: dict) -> dict[str, Any]:
    """Register a source (agency or portal website) to fold into the
    existing `run_website_scraper` fan-out on every future search — see
    `app.graphs.extraction.nodes.route_after_review`.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'source': None, 'error': 'Supabase no configurado'}

    nombre = (body.get('nombre') or '').strip()
    url = (body.get('url') or '').strip()
    if not nombre:
        return {'source': None, 'error': 'nombre es requerido'}
    if not url.startswith(('http://', 'https://')):
        return {'source': None, 'error': 'url debe empezar con http:// o https://'}

    try:
        res = await sb.table('manual_sources').insert({'nombre': nombre, 'url': url}).execute()
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
