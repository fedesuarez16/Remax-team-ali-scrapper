from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()

# Sidebar shows at most this many recent searches; POST trims older rows
# beyond this cap. Kept in sync with the unique index / RLS-less table
# created in supabase/migrations/20260718010000_search_history.sql.
CAP = 20


@router.get('')
async def list_search_history(request: Request) -> dict[str, Any]:
    """List saved search-history entries, most-recent-first, capped at CAP."""
    sb = request.app.state.supabase
    if sb is None:
        return {'history': [], 'total': 0, 'error': 'Supabase no configurado'}

    try:
        res = await (
            sb.table('search_history')
            .select('*')
            .order('created_at', desc=True)
            .limit(CAP)
            .execute()
        )
        history = res.data or []
        return {'history': history, 'total': len(history)}
    except Exception as e:
        return {'history': [], 'total': 0, 'error': str(e)}


@router.post('')
async def create_or_update_search_history(request: Request, body: dict) -> dict[str, Any]:
    """Upsert a search-history entry via delete-then-insert.

    Case-insensitive match on `query`: any existing row is deleted, then a
    fresh row is inserted with the new payload — mirrors the client's old
    localStorage filter->prepend->slice behavior and keeps the unique index
    on lower(query) conflict-free. Rows beyond CAP (oldest by created_at)
    are then trimmed. Never raises — failures are swallowed into
    {error: str(e)}.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'entry': None, 'error': 'Supabase no configurado'}

    query = (body.get('query') or '').strip()
    if not query:
        return {'entry': None}

    zona = body.get('zona')
    job_id = body.get('job_id')

    try:
        await sb.table('search_history').delete().ilike('query', query).execute()
        payload: dict[str, Any] = {'query': query, 'zona': zona, 'job_id': job_id}
        res = await sb.table('search_history').insert(payload).execute()
        entry = res.data[0] if res.data else None
        await _trim_cap(sb)
        return {'entry': entry}
    except Exception as e:
        return {'entry': None, 'error': str(e)}


async def _trim_cap(sb: Any, cap: int = CAP) -> None:
    """Delete the oldest rows beyond `cap`, keeping the `cap` most recent."""
    res = await sb.table('search_history').select('id').order('created_at', desc=True).execute()
    rows = res.data or []
    if len(rows) > cap:
        stale_ids = [r['id'] for r in rows[cap:]]
        await sb.table('search_history').delete().in_('id', stale_ids).execute()
