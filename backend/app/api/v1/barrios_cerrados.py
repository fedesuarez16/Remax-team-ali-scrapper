"""Hand-loaded catalogue of gated communities, and the per-portal probe.

Two things live here, and they are two halves of one answer to "buscar en un
barrio cerrado como si fuera una zona":

  * the CATALOGUE — nombre + localidad + alias + polígono. The localidad is the
    load-bearing field: `zona_candidates` degrades a search through the comma
    string it is handed, and a gated community never arrives with one, so
    without it a portal that misses the barrio returns nothing at all.
  * the PROBE — asks every portal what IT calls this place and writes the
    answers down for a human to confirm. See `app.services.barrio_probe`.

Error convention follows `saved_zones`/`search_history`: a 200 with an `error`
key, never a raised HTTPException, so the UI renders one failure shape.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app.services.apify import PORTAL_SOURCES
from app.services.barrio_cerrado import barrio_aliases, barrio_zona, strip_kind_prefix
from app.services.barrio_probe import (
    ARGENPROP_AUTOCOMPLETE_CAP,
    discover_argenprop_barrios,
    probe_barrio,
)
from app.services.polygon import MIN_POLYGON_POINTS
from app.services.zona import normalize_address

router = APIRouter()

_KINDS = ('barrio_cerrado', 'club_de_campo', 'country', 'barrio_privado')

_BARRIOS = 'barrios_cerrados'
_REFS = 'barrio_cerrado_portal_refs'


def _clean_polygon(raw: Any) -> list[list[float]] | None:
    """Same validation as `saved_zones._clean_polygon` — the map renders both
    through the same Leaflet `Polygon`, so garbage has to die at write time."""
    if not isinstance(raw, list) or len(raw) < MIN_POLYGON_POINTS:
        return None
    cleaned: list[list[float]] = []
    for vertex in raw:
        if not isinstance(vertex, (list, tuple)) or len(vertex) != 2:
            return None
        lat, lng = vertex
        if isinstance(lat, bool) or isinstance(lng, bool):
            return None
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            return None
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
            return None
        cleaned.append([float(lat), float(lng)])
    return cleaned


def _decorate(row: dict, refs: list[dict] | None = None) -> dict:
    """Add the DERIVED fields the UI needs to trust the row.

    `aliases_efectivos` is what the guard will actually filter on and
    `zona` is the string the candidate chain will walk. Both are computed, not
    stored — recomputing on read means a rename can never leave a stale filter
    behind, and showing them is what turns the barrio filter from a black box
    into something an operator can audit before relying on it.
    """
    nombre = str(row.get('nombre') or '')
    localidad = str(row.get('localidad') or '')
    return {
        **row,
        'zona': barrio_zona(nombre, localidad),
        'aliases_efectivos': list(barrio_aliases(nombre, row.get('aliases') or [])),
        'portal_refs': refs if refs is not None else [],
    }


async def _refs_for(sb: Any, barrio_id: str) -> list[dict]:
    res = await sb.table(_REFS).select('*').eq('barrio_id', barrio_id).execute()
    return res.data or []


# ── Bulk import from Argenprop ────────────────────────────────────────────────
#
# Declared BEFORE the `/{barrio_id}` routes: FastAPI matches in declaration
# order, and a parameterised route declared first would swallow `/import` as a
# barrio whose id is the literal string "import".


def _identity_key(nombre: str) -> str:
    """The key two spellings of one barrio collapse onto.

    "Club de Campo Los Ceibos", "los ceibos" and "Barrio Cerrado Grand Bell"
    vs "Grand Bell" are the same places. Comparing raw strings would re-import
    barrios the operator already has, so the kind comes off and the rest is
    accent-folded — the same normal form the guard matches on.
    """
    return normalize_address(strip_kind_prefix(nombre))


@router.get('/import/preview')
async def import_preview(
    request: Request,
    partido: str = Query(..., min_length=1),
    localidad: str | None = None,
    # ~20 extra autocomplete calls that widen coverage past the API's 30-row
    # ceiling. Off by default because most partidos fit under it; worth it for
    # Pilar or Tigre, where the base query returns only the alphabetical head.
    deep: bool = False,
) -> dict[str, Any]:
    """Enumerate a partido's gated communities WITHOUT writing anything.

    Half of a deliberate two-step. Argenprop can list the barrios but not place
    them (it labels Grand Bell "Partido de La Plata"; the barrio is in City
    Bell), and its autocomplete caps at 30 rows with no error — so the operator
    has to see the list, the localidad it defaulted to, and whether it was
    truncated, before any of it becomes catalogue.
    """
    found = await discover_argenprop_barrios(partido, localidad, deep=deep)

    existentes: set[str] = set()
    sb = request.app.state.supabase
    if sb is not None:
        try:
            res = await sb.table(_BARRIOS).select('*').execute()
            existentes = {_identity_key(str(r.get('nombre') or '')) for r in (res.data or [])}
        except Exception:
            pass  # a dedupe we could not compute is a warning, not a failure

    barrios = [
        {**c.as_dict(), 'ya_existe': _identity_key(c.nombre) in existentes}
        for c in found.barrios
    ]
    out: dict[str, Any] = {
        'barrios': barrios,
        'total_api': found.total_api,
        'truncated': found.truncated,
        'error': found.error,
    }
    if found.truncated:
        # `truncated` says the BASE query hit the ceiling, which is all we can
        # know — it is not a claim that anything is actually missing. Measured:
        # La Plata reports truncated and the deep sweep finds nothing new (19
        # either way), while Pilar goes from 29 to 120. So the message has to
        # tell the operator what to DO about it, and that differs by whether
        # the sweep already ran.
        out['warning'] = (
            f'Argenprop cortó en su tope de {ARGENPROP_AUTOCOMPLETE_CAP} resultados. '
            + (
                f'Ya corrí el barrido profundo, así que esta lista de {partido} es lo '
                'más completo que da el portal — si sabés de alguno que falta, cargalo a mano.'
                if deep else
                f'La lista de {partido} puede estar INCOMPLETA: probá de nuevo con '
                '`deep=true` (medido: Pilar pasa de 29 a 120 barrios).'
            )
        )
    return out


@router.post('/import')
async def import_barrios(request: Request, body: dict) -> dict[str, Any]:
    """Load the reviewed subset, seeding Argenprop's ref for each one.

    Partial by design: one broken row does not abort the other twenty. The
    operator fixes what failed instead of re-reviewing what did not.

    The seeded ref is NOT confirmed. Enumerating a barrio is not the same as a
    human saying "yes, that is my barrio" — and with homonyms as common as they
    are (Argenprop serves five different "Los Ceibos"), that distinction is the
    only thing between a curated catalogue and a pile of plausible guesses.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'creados': 0, 'salteados': 0, 'barrios': [], 'errores': [],
                'error': 'Supabase no configurado'}

    entradas = body.get('barrios') or []
    if not isinstance(entradas, list):
        return {'creados': 0, 'salteados': 0, 'barrios': [], 'errores': [],
                'error': 'barrios debe ser una lista'}

    try:
        res = await sb.table(_BARRIOS).select('*').execute()
        existentes = {_identity_key(str(r.get('nombre') or '')) for r in (res.data or [])}
    except Exception as e:
        return {'creados': 0, 'salteados': 0, 'barrios': [], 'errores': [], 'error': str(e)}

    creados: list[dict] = []
    errores: list[str] = []
    salteados = 0

    for entrada in entradas:
        nombre = str((entrada or {}).get('nombre') or '').strip()
        localidad = str((entrada or {}).get('localidad') or '').strip()
        if not nombre:
            errores.append('Una entrada sin nombre fue ignorada')
            continue
        if not localidad:
            errores.append(f'{nombre}: falta localidad, no se importó')
            continue

        key = _identity_key(nombre)
        if key in existentes:
            salteados += 1
            continue

        kind = str((entrada or {}).get('kind') or 'barrio_cerrado')
        if kind not in _KINDS:
            kind = 'barrio_cerrado'

        try:
            inserted = await sb.table(_BARRIOS).insert({
                'nombre': nombre, 'localidad': localidad, 'kind': kind,
                'aliases': [], 'activo': True,
            }).execute()
        except Exception as e:
            errores.append(f'{nombre}: {e}')
            continue

        row = (inserted.data or [None])[0]
        if row is None:
            errores.append(f'{nombre}: la base no devolvió la fila creada')
            continue
        # Only mark it taken once the write actually landed — otherwise a
        # failed insert would make a retry in the same batch look like a dupe.
        existentes.add(key)

        if ref := str((entrada or {}).get('argenprop_ref') or '').strip():
            try:
                await sb.table(_REFS).insert({
                    'barrio_id': row.get('id'), 'portal': 'argenprop',
                    'strategy': 'native', 'ref': ref,
                    'label': (entrada or {}).get('label'),
                    'result_count': None, 'confirmed': False,
                    'note': 'Sembrado por el import masivo de Argenprop. Sin confirmar.',
                }).execute()
            except Exception as e:
                # The barrio itself is in — losing its ref only costs a probe.
                errores.append(f'{nombre}: se creó, pero no se pudo guardar la ref ({e})')

        creados.append(_decorate(row))

    return {
        'creados': len(creados), 'salteados': salteados,
        'barrios': creados, 'errores': errores,
    }


@router.get('')
async def list_barrios(request: Request) -> dict[str, Any]:
    """The catalogue, each row carrying its per-portal refs.

    The refs come INLINE rather than behind a second call per barrio: "cómo
    figura en cada portal" is the whole reason the screen exists, and a
    listing that hides it behind N requests is a listing nobody reads.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'barrios': [], 'total': 0, 'error': 'Supabase no configurado'}
    try:
        res = await sb.table(_BARRIOS).select('*').order('nombre').execute()
        rows = res.data or []
        refs = (await sb.table(_REFS).select('*').execute()).data or []
        by_barrio: dict[str, list[dict]] = {}
        for ref in refs:
            by_barrio.setdefault(str(ref.get('barrio_id')), []).append(ref)
        barrios = [_decorate(r, by_barrio.get(str(r.get('id')), [])) for r in rows]
        return {'barrios': barrios, 'total': len(barrios)}
    except Exception as e:
        return {'barrios': [], 'total': 0, 'error': str(e)}


@router.post('')
async def create_barrio(request: Request, body: dict) -> dict[str, Any]:
    """Load a gated community by hand.

    `localidad` is required and that is not bureaucracy: it is the ONLY field
    nobody can derive, and without it the barrio is unsearchable on every
    portal that does not resolve it natively.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'barrio': None, 'error': 'Supabase no configurado'}

    nombre = (body.get('nombre') or '').strip()
    if not nombre:
        return {'barrio': None, 'error': 'nombre es requerido'}

    localidad = (body.get('localidad') or '').strip()
    if not localidad:
        return {
            'barrio': None,
            'error': (
                'localidad es requerida: es la que le da a la búsqueda por dónde '
                'degradar cuando un portal no encuentra el barrio'
            ),
        }

    kind = (body.get('kind') or 'barrio_cerrado').strip()
    if kind not in _KINDS:
        return {'barrio': None, 'error': f'kind inválido (esperaba uno de {_KINDS})'}

    payload: dict[str, Any] = {
        'nombre': nombre,
        'localidad': localidad,
        'kind': kind,
        'aliases': [str(a).strip() for a in (body.get('aliases') or []) if str(a).strip()],
        'activo': bool(body.get('activo', True)),
    }

    if body.get('polygon') is not None:
        polygon = _clean_polygon(body.get('polygon'))
        if polygon is None:
            return {
                'barrio': None,
                'error': f'polygon inválido (mínimo {MIN_POLYGON_POINTS} vértices [lat, lng])',
            }
        payload['polygon'] = polygon

    try:
        res = await sb.table(_BARRIOS).insert(payload).execute()
        row = res.data[0] if res.data else None
        return {'barrio': _decorate(row) if row else None}
    except Exception as e:
        return {'barrio': None, 'error': str(e)}


@router.patch('/{barrio_id}')
async def update_barrio(request: Request, barrio_id: str, body: dict) -> dict[str, Any]:
    """Presence-checked field update — same convention as the other routers, so
    a client can flip `activo` without resending the polygon."""
    sb = request.app.state.supabase
    if sb is None:
        return {'barrio': None, 'error': 'Supabase no configurado'}

    payload: dict[str, Any] = {}
    if 'nombre' in body:
        nombre = (body.get('nombre') or '').strip()
        if not nombre:
            return {'barrio': None, 'error': 'nombre no puede estar vacío'}
        payload['nombre'] = nombre
    if 'localidad' in body:
        localidad = (body.get('localidad') or '').strip()
        if not localidad:
            return {'barrio': None, 'error': 'localidad no puede estar vacía'}
        payload['localidad'] = localidad
    if 'kind' in body:
        if body.get('kind') not in _KINDS:
            return {'barrio': None, 'error': f'kind inválido (esperaba uno de {_KINDS})'}
        payload['kind'] = body['kind']
    if 'aliases' in body:
        payload['aliases'] = [
            str(a).strip() for a in (body.get('aliases') or []) if str(a).strip()
        ]
    if 'activo' in body:
        payload['activo'] = bool(body['activo'])
    if 'polygon' in body:
        if body.get('polygon') is None:
            payload['polygon'] = None
        else:
            polygon = _clean_polygon(body.get('polygon'))
            if polygon is None:
                return {
                    'barrio': None,
                    'error': f'polygon inválido (mínimo {MIN_POLYGON_POINTS} vértices)',
                }
            payload['polygon'] = polygon

    if not payload:
        return {'barrio': None, 'error': 'nada para actualizar'}

    try:
        res = await sb.table(_BARRIOS).update(payload).eq('id', barrio_id).execute()
        rows = res.data or []
        if not rows:
            return {'barrio': None, 'error': 'barrio no encontrado'}
        return {'barrio': _decorate(rows[0], await _refs_for(sb, barrio_id))}
    except Exception as e:
        return {'barrio': None, 'error': str(e)}


@router.delete('/{barrio_id}')
async def delete_barrio(request: Request, barrio_id: str) -> dict[str, Any]:
    """Idempotent — deleting an unknown id is not an error. Refs go with it via
    `on delete cascade`; the explicit delete here keeps the fake-Supabase path
    and the real one behaving alike."""
    sb = request.app.state.supabase
    if sb is None:
        return {'deleted': False, 'error': 'Supabase no configurado'}
    try:
        await sb.table(_REFS).delete().eq('barrio_id', barrio_id).execute()
        await sb.table(_BARRIOS).delete().eq('id', barrio_id).execute()
        return {'deleted': True}
    except Exception as e:
        return {'deleted': False, 'error': str(e)}


@router.post('/{barrio_id}/probe')
async def probe(request: Request, barrio_id: str) -> dict[str, Any]:
    """Ask every portal how it names this barrio, and persist the answers.

    A re-probe KEEPS `confirmed` only when the ref came back byte-identical.
    A human's "yes, that is my barrio" was about a specific handle; if the
    portal now answers something else, carrying the flag over would stop the
    UI flagging it for review while the scraper filters on a ref that moved.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'portal_refs': [], 'error': 'Supabase no configurado'}

    try:
        found = await sb.table(_BARRIOS).select('*').eq('id', barrio_id).execute()
        rows = found.data or []
        if not rows:
            return {'portal_refs': [], 'error': 'barrio no encontrado'}
        barrio = rows[0]

        previos = {str(r.get('portal')): r for r in await _refs_for(sb, barrio_id)}
        refs = await probe_barrio(
            str(barrio.get('nombre') or ''), str(barrio.get('localidad') or ''))

        nuevos: list[dict] = []
        for ref in refs:
            row = ref.as_row(barrio_id)
            anterior = previos.get(ref.portal)
            if anterior is not None:
                # A hand-corrected ref is a human decision about this portal.
                # The probe reports what the portal says; it does not overrule
                # the operator, so a confirmed row keeps its own ref.
                if anterior.get('confirmed') and anterior.get('ref') == row['ref']:
                    row['confirmed'] = True
            nuevos.append(row)

        await sb.table(_REFS).delete().eq('barrio_id', barrio_id).execute()
        inserted = await sb.table(_REFS).insert(nuevos).execute()
        return {'portal_refs': inserted.data or nuevos}
    except Exception as e:
        return {'portal_refs': [], 'error': str(e)}


@router.patch('/{barrio_id}/refs/{portal}')
async def update_ref(
    request: Request, barrio_id: str, portal: str, body: dict,
) -> dict[str, Any]:
    """Confirm — or hand-correct — one portal's ref.

    The escape hatch that makes the whole catalogue usable. Homonyms are the
    norm (Argenprop serves a "Los Ceibos" in Tigre, La Plata, Córdoba,
    Corrientes and González Catán), so an operator who knows the right handle
    has to be able to write it, and the probe must not overrule them later.
    """
    sb = request.app.state.supabase
    if sb is None:
        return {'ref': None, 'error': 'Supabase no configurado'}
    if portal not in PORTAL_SOURCES:
        return {'ref': None, 'error': f'portal desconocido: {portal}'}

    payload: dict[str, Any] = {}
    if 'ref' in body:
        payload['ref'] = (body.get('ref') or '').strip() or None
    if 'strategy' in body:
        if body.get('strategy') not in ('native', 'localidad'):
            return {'ref': None, 'error': "strategy debe ser 'native' o 'localidad'"}
        payload['strategy'] = body['strategy']
    if 'confirmed' in body:
        payload['confirmed'] = bool(body['confirmed'])
    if 'note' in body:
        payload['note'] = body.get('note')

    if not payload:
        return {'ref': None, 'error': 'nada para actualizar'}

    try:
        res = await (
            sb.table(_REFS).update(payload)
            .eq('barrio_id', barrio_id).eq('portal', portal).execute()
        )
        rows = res.data or []
        if not rows:
            return {'ref': None, 'error': 'ref no encontrada — corré el probe primero'}
        return {'ref': rows[0]}
    except Exception as e:
        return {'ref': None, 'error': str(e)}
