from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request

from app.core.database import chunk_for_in_filter

router = APIRouter()

# Sidebar shows at most this many recent searches; POST trims older rows
# beyond this cap. Kept in sync with the unique index / RLS-less table
# created in supabase/migrations/20260718010000_search_history.sql.
CAP = 20


async def _attach_apify_costs(sb: Any, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Decorate each entry with what its search cost on Apify.

    Best-effort by design: cost is a nice-to-have, the history listing is not.
    A missing migration, an unreadable job or a chat-originated entry (no
    `job_id`) leaves the entry untouched — the UI reads that as "sin dato",
    which is NOT the same as the meaningful `0.0` a cache-served search stores.
    """
    job_ids = [h['job_id'] for h in history if h.get('job_id')]
    if not job_ids:
        return history

    # Chunked: `in_` values ride in the URL, which PostgREST rejects past ~39 KB
    # (~1000 uuids) — a long history page would silently lose every cost.
    rows: list[dict[str, Any]] = []
    try:
        for chunk in chunk_for_in_filter(job_ids):
            res = await (
                sb.table('scraping_jobs')
                .select('id,apify_cost_usd,apify_cost_breakdown')
                .in_('id', chunk)
                .execute()
            )
            rows.extend(res.data or [])
    except Exception:
        return history

    jobs = {row['id']: row for row in rows}
    for entry in history:
        job = jobs.get(entry.get('job_id') or '')
        if job is None:
            continue
        entry['apify_cost_usd'] = job.get('apify_cost_usd')
        entry['apify_cost_breakdown'] = job.get('apify_cost_breakdown')
    return history


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
        history = await _attach_apify_costs(sb, history)
        return {'history': history, 'total': len(history)}
    except Exception as e:
        return {'history': [], 'total': 0, 'error': str(e)}


@router.post('')
async def create_or_update_search_history(request: Request, body: dict) -> dict[str, Any]:
    """Upsert a search-history entry via SELECT existing -> UPDATE in place.

    Case-insensitive match on `query`. NOTE (ADR-1, deliberate deviation from
    the older delete-then-insert wording): once `label`/`folder_id` columns
    exist, delete-then-insert would silently wipe a user's custom label and
    folder assignment every time they re-run the same search. So instead we
    SELECT the row matching lower(query); if found, UPDATE it in place
    (merging zona/job_id when provided, folder_id only if the key is present
    in the body, and bumping created_at so the row resurfaces at the top of
    the most-recent-first listing) while PRESERVING `label` and any
    `folder_id` not explicitly overridden. If no row exists, INSERT a fresh
    one (label null) and trim beyond CAP. `label` is never set by POST — it
    is only ever set/changed via PATCH. Never raises — failures are
    swallowed into {error: str(e)}.
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
        existing_res = await sb.table('search_history').select('*').ilike('query', query).execute()
        existing_rows = existing_res.data or []

        if existing_rows:
            existing = existing_rows[0]
            update_payload: dict[str, Any] = {
                'query': query,
                'zona': zona,
                'job_id': job_id,
                'created_at': datetime.now(timezone.utc).isoformat(),
            }
            if 'folder_id' in body:
                update_payload['folder_id'] = body.get('folder_id')
            res = await (
                sb.table('search_history')
                .update(update_payload)
                .eq('id', existing['id'])
                .execute()
            )
            entry = res.data[0] if res.data else None
            return {'entry': entry}

        payload: dict[str, Any] = {'query': query, 'zona': zona, 'job_id': job_id}
        if 'folder_id' in body:
            payload['folder_id'] = body.get('folder_id')
        res = await sb.table('search_history').insert(payload).execute()
        entry = res.data[0] if res.data else None
        await _trim_cap(sb)
        return {'entry': entry}
    except Exception as e:
        return {'entry': None, 'error': str(e)}


@router.post('/folders')
async def create_search_history_folder(request: Request, body: dict) -> dict[str, Any]:
    """Create a folder to group search-history entries.

    Declared BEFORE the `/{entry_id}` routes (ADR-3) so the static
    `/folders` segment can never be shadowed by a path-param route.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'folder': None, 'error': 'Supabase no configurado'}

    name = (body.get('name') or '').strip()
    if not name:
        return {'folder': None, 'error': 'name es requerido'}

    try:
        res = await sb.table('search_history_folders').insert({'name': name}).execute()
        folder = res.data[0] if res.data else None
        return {'folder': folder}
    except Exception as e:
        return {'folder': None, 'error': str(e)}


@router.get('/folders')
async def list_search_history_folders(request: Request) -> dict[str, Any]:
    """List all folders, most-recent-first.

    Declared BEFORE the `/{entry_id}` routes (ADR-3).
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'folders': [], 'total': 0, 'error': 'Supabase no configurado'}

    try:
        res = await (
            sb.table('search_history_folders')
            .select('*')
            .order('created_at', desc=True)
            .execute()
        )
        folders = res.data or []
        return {'folders': folders, 'total': len(folders)}
    except Exception as e:
        return {'folders': [], 'total': 0, 'error': str(e)}


@router.delete('/folders/{folder_id}')
async def delete_search_history_folder(request: Request, folder_id: str) -> dict[str, Any]:
    """Borrar una carpeta. Sus búsquedas NO se borran: quedan sin carpeta.

    La FK `search_history.folder_id` es `on delete set null` (ver
    20260727000000_search_history_folders_and_labels.sql), así que Postgres
    suelta las entradas en la misma transacción del delete. Por eso acá NO
    tocamos `search_history`: hacerlo a mano duplicaría la garantía y abriría
    el caso feo de dejar entradas desasignadas si el delete de la carpeta
    fallara después.

    Declarado ANTES de `/{entry_id}` (ADR-3) para que el segmento estático
    `folders` no pueda quedar tapado por una ruta con path-param.

    Idempotente: borrar un id inexistente no es un error.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'deleted': False, 'error': 'Supabase no configurado'}

    try:
        await sb.table('search_history_folders').delete().eq('id', folder_id).execute()
        return {'deleted': True}
    except Exception as e:
        return {'deleted': False, 'error': str(e)}


@router.patch('/{entry_id}')
async def update_search_history_entry(request: Request, entry_id: str, body: dict) -> dict[str, Any]:
    """Update `label` and/or `folder_id` on a single entry.

    Uses presence checks (`'label' in body` / `'folder_id' in body`, ADR-5)
    rather than falsy checks, so a client can explicitly clear a label or
    unassign a folder by sending `null` while an absent key leaves that
    column untouched.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'entry': None, 'error': 'Supabase no configurado'}

    update_payload: dict[str, Any] = {}
    if 'label' in body:
        update_payload['label'] = body.get('label')
    if 'folder_id' in body:
        update_payload['folder_id'] = body.get('folder_id')

    try:
        res = await (
            sb.table('search_history')
            .update(update_payload)
            .eq('id', entry_id)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return {'entry': None, 'error': 'entry no encontrada'}
        return {'entry': rows[0]}
    except Exception as e:
        return {'entry': None, 'error': str(e)}


@router.delete('/{entry_id}')
async def delete_search_history_entry(request: Request, entry_id: str) -> dict[str, Any]:
    """Delete a single history entry by id. Idempotent — deleting a
    non-existent id is not an error (Postgres delete is naturally
    idempotent), so this always returns {deleted: true} absent a real
    backend/DB failure.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'deleted': False, 'error': 'Supabase no configurado'}

    try:
        await sb.table('search_history').delete().eq('id', entry_id).execute()
        return {'deleted': True}
    except Exception as e:
        return {'deleted': False, 'error': str(e)}


async def _trim_cap(sb: Any, cap: int = CAP) -> None:
    """Delete the oldest rows beyond `cap`, keeping the `cap` most recent."""
    res = await sb.table('search_history').select('id').order('created_at', desc=True).execute()
    rows = res.data or []
    if len(rows) > cap:
        stale_ids = [r['id'] for r in rows[cap:]]
        # Chunked: `in_` values ride in the URL, which PostgREST rejects past
        # ~39 KB (~1000 uuids). A trim that raises leaves the cap unenforced
        # forever; batching keeps it working however far past the cap we are.
        for chunk in chunk_for_in_filter(stale_ids):
            await sb.table('search_history').delete().in_('id', chunk).execute()
