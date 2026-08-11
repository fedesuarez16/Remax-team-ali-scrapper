from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()

# The singleton row's fixed id. There is exactly one set of ficha texts for the
# whole team, so the table is a settings row, not a collection.
SETTINGS_ID = 1

# Built-in texts — the values these had as module constants in
# `frontend/lib/ficha.ts` before they became editable. They are the fallback for
# every read path: the public `/p/[id]` page renders this footer on every visit,
# so an unconfigured Supabase or a dead query must degrade to a correct footer,
# never to an empty one. The legal disclaimer especially: it is normative text
# that has to appear on every published ficha.
DEFAULT_TEXTOS: dict[str, str] = {
    'texto_seleccion': (
        'Esta selección de propiedades reúne las oportunidades relevadas en el mercado que '
        'mejor se ajustan a tus criterios de búsqueda. Si alguna opción resulta de tu interés, '
        'comunícate para coordinar una visita o solicitar más información.'
    ),
    'disclaimer_legal': (
        '⚖️ En cumplimiento de las normas legales aplicables, informamos que los Agentes NO '
        'ejercen el Corretaje Inmobiliario. Todas las operaciones inmobiliarias son concluidas '
        'por los Corredores Matriculados responsables en cada oficina.'
    ),
    'firma': 'Andrés Alí | Diagonal II',
    'colegiatura': 'C.D.C.P.D.J.L.P. 7428',
    'pie_publicacion': (
        'Publicación generada por RE/MAX Diagonal II. La información puede estar sujeta a '
        'modificaciones sin previo aviso.'
    ),
}

# Everything else on the row (id, updated_at) is system-owned and dropped.
_EDITABLE_FIELDS = frozenset(DEFAULT_TEXTOS)


def _merge_with_defaults(row: dict[str, Any] | None) -> dict[str, str]:
    """Project a stored row onto the editable text fields, filling each absent
    or null one from the defaults.

    Per-field rather than all-or-nothing on purpose: a row written before a new
    text was introduced would otherwise render that field as `null` in a
    published footer.
    """
    row = row or {}
    return {
        field: (row.get(field) or default)
        for field, default in DEFAULT_TEXTOS.items()
    }


@router.get('')
async def get_ficha_settings(request: Request) -> dict[str, Any]:
    """Read the team-wide ficha texts. Always returns a usable set.

    Failures are reported alongside the defaults instead of raising: the caller
    is usually the public listing page, and a broken footer is worse than a
    stale one.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'settings': dict(DEFAULT_TEXTOS), 'error': 'Supabase no configurado'}

    try:
        res = await sb.table('ficha_settings').select('*').limit(1).execute()
    except Exception as e:
        return {'settings': dict(DEFAULT_TEXTOS), 'error': str(e)}

    rows = res.data or []
    return {'settings': _merge_with_defaults(rows[0] if rows else None)}


@router.patch('')
async def update_ficha_settings(request: Request, body: dict) -> dict[str, Any]:
    """Edit one or more team-wide ficha texts.

    Presence-checked, so a form can submit a single field without clobbering
    the others. Blanks are refused rather than stored — an empty disclaimer
    would strip the legally required notice from every published ficha at once.

    Unlike GET, a failed write reports the error with no settings: the user has
    to know their edit did not persist.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'settings': None, 'error': 'Supabase no configurado'}

    payload: dict[str, str] = {}
    for field in _EDITABLE_FIELDS:
        if field not in body:
            continue
        value = body[field]
        if not isinstance(value, str) or not value.strip():
            return {'settings': None, 'error': f'{field} debe ser un texto no vacío'}
        payload[field] = value.strip()

    if not payload:
        return {'settings': None, 'error': 'nada para actualizar'}

    # Read-then-merge rather than a bare upsert of the defaults: the untouched
    # columns must keep whatever the team already customised. Upserting
    # `{**DEFAULT_TEXTOS, **payload}` would revert every field the user did not
    # submit — a typo fix on the signature silently restoring the stock
    # disclaimer. The read also covers the fresh-install case, where there is no
    # row yet and the defaults are exactly the right base.
    try:
        current = await sb.table('ficha_settings').select('*').limit(1).execute()
        stored = (current.data or [None])[0]
        row = {'id': SETTINGS_ID, **_merge_with_defaults(stored), **payload}
        res = await sb.table('ficha_settings').upsert(row).execute()
    except Exception as e:
        return {'settings': None, 'error': str(e)}

    rows = res.data or []
    return {'settings': _merge_with_defaults(rows[0] if rows else row)}
