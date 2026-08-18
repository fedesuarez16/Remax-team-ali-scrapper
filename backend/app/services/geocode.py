from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'
NOMINATIM_REVERSE_URL = 'https://nominatim.openstreetmap.org/reverse'
# Nominatim /reverse address keys, most-specific barrio → coarser fallbacks.
_REVERSE_ADDR_KEYS = (
    'suburb', 'neighbourhood', 'quarter', 'city_district', 'town', 'city', 'municipality',
)
# Localidad (partido/ciudad) address keys — coarser than barrio, used as the
# portal search unit (ADR-2: single /reverse call feeds both extractions).
_LOCALIDAD_ADDR_KEYS = ('city', 'town', 'municipality', 'city_district')
# Partido/departamento address keys — appended to the localidad ("Villa Elisa,
# La Plata") so portal slugs resolve the RIGHT homonym (there is a Villa Elisa
# in Entre Ríos that ZonaProp prefers for the bare slug).
_PARTIDO_ADDR_KEYS = ('county', 'state_district')
_PARTIDO_PREFIX_RE = re.compile(r'^(partido|departamento)\s+(de\s+)?', re.IGNORECASE)


def _norm_place(value: str) -> str:
    import unicodedata
    return unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode().strip().lower()
USER_AGENT = 'multi-agent-realstate/0.1 (property-map geocoder; contact: coflipweb@gmail.com)'
# left,top,right,bottom (lon,lat) — soft bias toward Buenos Aires
BA_VIEWBOX = '-58.531,-34.526,-58.335,-34.705'
# Gran La Plata (La Plata, City Bell, Gonnet, Villa Elisa, Hudson) — the cartera
# uses numbered grid streets that exist all over GBA, so without this bias
# Nominatim picks the wrong district (e.g. Los Hornos rows landing in Morón).
LP_VIEWBOX = '-58.35,-34.75,-57.80,-35.10'
RATE_LIMIT_SECONDS = 1.1
# Single retry after this pause when Nominatim throttles; a second 429 aborts the run.
THROTTLE_BACKOFF_SECONDS = 30


class TransientGeocodeError(Exception):
    """Nominatim throttled us (429) or failed server-side — the address may still be
    resolvable later, so the row must NOT be marked as attempted."""


# Addresses that already carry their own province/country ("...Entre Ríos,
# Argentina") must NOT get ", Buenos Aires, Argentina" appended — the two
# contradict each other and Nominatim returns nothing for the combined string.
_HAS_LOCATION_CONTEXT_RE = re.compile(
    r'\b(argentina|buenos aires|caba|entre r[íi]os|santa fe|c[oó]rdoba|mendoza|uruguay)\b',
    re.IGNORECASE,
)


def _build_query(address: str) -> str:
    if _HAS_LOCATION_CONTEXT_RE.search(address):
        return address
    return f'{address}, Buenos Aires, Argentina'


async def geocode(
    address: str, *, client: httpx.AsyncClient, viewbox: str = BA_VIEWBOX,
) -> tuple[float, float] | None:
    """Resolve a free-text address to (lat, lng) via Nominatim.

    Returns ``None`` only when the address genuinely can't be resolved (empty
    results, malformed response, non-throttle 4xx). Raises
    ``TransientGeocodeError`` on 429/5xx/timeouts so callers can retry later
    without burning the row's ``geocoded_at``.
    """
    params: dict[str, Any] = {
        'q': _build_query(address),
        'format': 'jsonv2',
        'limit': 1,
        'countrycodes': 'ar',
        'viewbox': viewbox,
        'bounded': 0,
    }
    try:
        resp = await client.get(
            NOMINATIM_URL, params=params, headers={'User-Agent': USER_AGENT}, timeout=10,
        )
    except httpx.HTTPError as exc:
        raise TransientGeocodeError(str(exc)) from exc
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientGeocodeError(f'HTTP {resp.status_code}')
    try:
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning('geocode failed for %r: %s', address, exc)
        return None
    if not data:
        return None
    try:
        return float(data[0]['lat']), float(data[0]['lon'])
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning('geocode: malformed response for %r: %s', address, exc)
        return None


async def reverse_geocode_pair(
    lat: float, lng: float, *, client: httpx.AsyncClient, zoom: int = 14,
) -> tuple[str | None, str | None]:
    """Resolve coordinates to (barrio, localidad) via ONE Nominatim `/reverse`
    call (ADR-2 — zero extra HTTP calls vs the barrio-only lookup).

    Mirrors `geocode()`'s error handling: `TransientGeocodeError` on
    429/5xx/timeout, ``(None, None)`` (never an exception) when the response
    has no usable address component. Barrio uses `_REVERSE_ADDR_KEYS`
    (favors ``suburb`` over coarser names); localidad uses the coarser
    `_LOCALIDAD_ADDR_KEYS` (city/town/municipality/city_district) so both are
    extracted from the same address payload.
    """
    params: dict[str, Any] = {
        'lat': lat, 'lon': lng, 'format': 'jsonv2', 'zoom': zoom,
        'addressdetails': 1, 'accept-language': 'es',
    }
    try:
        resp = await client.get(
            NOMINATIM_REVERSE_URL, params=params, headers={'User-Agent': USER_AGENT}, timeout=10,
        )
    except httpx.HTTPError as exc:
        raise TransientGeocodeError(str(exc)) from exc
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientGeocodeError(f'HTTP {resp.status_code}')
    try:
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning('reverse_geocode failed for (%s, %s): %s', lat, lng, exc)
        return None, None
    addr = data.get('address') or {}
    barrio: str | None = None
    for key in _REVERSE_ADDR_KEYS:
        value = addr.get(key)
        if value:
            barrio = value
            break
    if barrio is None:
        barrio = data.get('name') or None
    localidad: str | None = None
    for key in _LOCALIDAD_ADDR_KEYS:
        value = addr.get(key)
        if value:
            localidad = value
            break
    if localidad:
        for key in _PARTIDO_ADDR_KEYS:
            raw = addr.get(key)
            if not raw:
                continue
            partido = _PARTIDO_PREFIX_RE.sub('', raw).strip()
            if partido and _norm_place(partido) != _norm_place(localidad):
                localidad = f'{localidad}, {partido}'
            break
    return barrio, localidad


async def reverse_geocode(
    lat: float, lng: float, *, client: httpx.AsyncClient, zoom: int = 14,
) -> str | None:
    """Resolve coordinates to a place/neighborhood name via Nominatim `/reverse`.

    Thin wrapper around `reverse_geocode_pair` returning only the barrio —
    kept for callers that don't need the localidad.
    """
    barrio, _localidad = await reverse_geocode_pair(lat, lng, client=client, zoom=zoom)
    return barrio


# Portals write the between-streets marker four different ways — "502 e/ 17 y
# 18", "19 E/41 y 42", "29 E / 418 y 419" and the bare "509 E 14 y 15" — and
# Nominatim can parse none of them. The marker and the cross streets have to go,
# but only up to the NEXT COMMA: everything after it is the locality ("48 e/ 7 y
# 8, La Plata"), and dropping that sends La Plata's numbered grid streets to the
# Buenos Aires viewbox, where the same numbers exist and resolve to the wrong
# district. The bare-"e" branch demands a following "<num> y <num>" so ordinary
# street tokens are never mistaken for the marker, and the "entre" branch
# excludes "Entre Ríos" (the province, not a marker).
_ENTRE_RE = re.compile(
    r'\s+(?:e\s*/|entre\s+(?!r[íi]os\b)|e(?=\s+\d+\s*(?:y|a)\s+\d))[^,]*',
    re.IGNORECASE,
)
# La Plata's grid is often written with the "e/" marker simply left out —
# "11 43 y 44" means calle 11 between 43 and 44. Requires THREE numbers so a
# plain corner ("3 y 42") is not mistaken for it; keeps the locality past the
# comma for the same reason `_ENTRE_RE` does.
_IMPLICIT_ENTRE_RE = re.compile(
    r'^(\d+\s*(?:bis|[a-z])?)\s+\d+\s+y\s+\d+[^,]*', re.IGNORECASE,
)
# "48 esq 6" → "48 y 6": Nominatim resolves corners in "X y Z" form, not "esq".
# Both the abbreviation and the full word appear in the data.
_ESQ_RE = re.compile(r'\s+esq(?:uina|\.)?\s+', re.IGNORECASE)
# "S/N" = sin número. It is a sentinel, not part of the address.
_SIN_NUMERO_RE = re.compile(r'\s*\bs/n\b\.?', re.IGNORECASE)
# Street-number markers ("N°380", "nro 1301", "al 2500") — keep the digits,
# drop the marker, which Nominatim reads as a street-name token.
_ALTURA_MARKER_RE = re.compile(r'\s*\b(?:n|nro|n°|nº|al)\s*[°º]?\s*(?=\d)', re.IGNORECASE)
# "UF 1" — unidad funcional, an internal unit id with no map meaning.
_UF_RE = re.compile(r'\s*\buf\s*\d+\b', re.IGNORECASE)
# InmoBusqueda closes every address with "Pdo. de <partido>". Nominatim has no
# idea what "Pdo." is and reads it as a street-name token, which poisons the
# whole query — that source failed on 93.5% of its rows. Unwrapping the
# abbreviation leaves the bare partido name, which Nominatim resolves fine.
_PARTIDO_WRAPPER_RE = re.compile(r'\b(?:pdo\.?|partido)\s+de\s+', re.IGNORECASE)
# "Cno." = Camino, another abbreviation Nominatim does not expand on its own.
_CNO_RE = re.compile(r'\bcno\.?\s+', re.IGNORECASE)
# Inmobusqueda prefixes the listing type onto the address ("Oficina en 48 ...").
_TYPE_PREFIX_RE = re.compile(
    r'^(?:departamento|depto\.?|dpto\.?|casa|ph|oficina|local|terreno|lote|cochera|'
    r'galp[oó]n|quinta|campo|d[uú]plex|monoambiente)\s+en\s+',
    re.IGNORECASE,
)
# Argenprop appends the floor; Nominatim has no concept of it and the trailing
# token only poisons the match.
_PISO_RE = re.compile(r'\s*,?\s*piso\s+\S+\s*$', re.IGNORECASE)
# RE/MAX writes a bare trailing 0 when the street number is unknown.
_ZERO_ALTURA_RE = re.compile(r'\s+0\s*$')
# Some portals ship a breadcrumb ("19 y 45, Argentina | G.B.A. Zona Sur | La
# Plata"); commas are the separator Nominatim understands.
_PIPE_RE = re.compile(r'\s*\|\s*')
_SPACE_BEFORE_COMMA_RE = re.compile(r'\s+,')
_MULTI_SPACE_RE = re.compile(r'\s{2,}')


def _clean_street(direccion: str) -> str:
    """Normalise the notations the portals use into something Nominatim can
    resolve, preserving the locality that disambiguates numbered grid streets.

    Order matters: the floor suffix is dropped before the between-streets rule
    runs, so "473 bis e/15 a y 17 , Piso 0" loses the floor first and the
    cross-streets rule then has a clean tail to eat.
    """
    cleaned = _PIPE_RE.sub(', ', direccion.strip())
    cleaned = _PARTIDO_WRAPPER_RE.sub('', cleaned)
    cleaned = _CNO_RE.sub('Camino ', cleaned)
    cleaned = _TYPE_PREFIX_RE.sub('', cleaned)
    cleaned = _PISO_RE.sub('', cleaned)
    cleaned = _UF_RE.sub('', cleaned)
    cleaned = _SIN_NUMERO_RE.sub('', cleaned)
    cleaned = _ENTRE_RE.sub('', cleaned)
    # After `_ESQ_RE` a corner reads "X y Z", which must not then look like an
    # implicit "<street> <a> y <b>" — so the implicit rule runs first.
    cleaned = _IMPLICIT_ENTRE_RE.sub(r'\1', cleaned)
    cleaned = _ESQ_RE.sub(' y ', cleaned)
    cleaned = _ALTURA_MARKER_RE.sub(' ', cleaned)
    cleaned = _ZERO_ALTURA_RE.sub('', cleaned)
    cleaned = _SPACE_BEFORE_COMMA_RE.sub(',', cleaned)
    cleaned = _MULTI_SPACE_RE.sub(' ', cleaned).strip().strip(',').strip()
    if re.match(r'^\d', cleaned):
        cleaned = f'Calle {cleaned}'
    return cleaned


def _address_properties(row: dict[str, Any]) -> str | None:
    direccion = (row.get('direccion') or '').strip()
    if not direccion:
        return None
    return _clean_street(direccion)


def _address_propiedades(row: dict[str, Any]) -> str | None:
    direccion = _clean_street(row.get('direccion') or '')
    zona = (row.get('zona') or '').strip()
    parts = [p for p in (direccion, zona) if p]
    return ', '.join(parts) or None


# `properties` has no separate zona column, so a Gran La Plata locality
# mentioned right in `direccion` is the only signal available — without this,
# La Plata's numbered grid streets (which also exist in CABA/GBA) get biased
# toward BA_VIEWBOX and land in the wrong district (see LP_VIEWBOX above).
_LP_LOCALITY_RE = re.compile(
    r'\b(la plata|city bell|gonnet|villa elisa|tolosa|los hornos|ensenada|berisso|'
    r'romero|hernandez|abasto|ringuelet|olmos|arturo segui|melchor romero)\b',
    re.IGNORECASE,
)


def _viewbox_for_properties(row: dict[str, Any]) -> str:
    direccion = row.get('direccion') or ''
    return LP_VIEWBOX if _LP_LOCALITY_RE.search(direccion) else BA_VIEWBOX


def _viewbox_for_propiedades(_row: dict[str, Any]) -> str:
    return LP_VIEWBOX  # entire cartera is Gran La Plata


# (table, select, order-by, address builder, per-row viewbox) — both tables
# share one run so the Nominatim rate budget is spent sequentially, never in
# parallel.
_TABLES: list[tuple[
    str, str, str, Callable[[dict[str, Any]], str | None], Callable[[dict[str, Any]], str],
]] = [
    ('properties', 'id,direccion', 'created_at', _address_properties, _viewbox_for_properties),
    ('propiedades', 'id,direccion,zona', 'id', _address_propiedades, _viewbox_for_propiedades),
]

_lock = asyncio.Lock()
_state: dict[str, Any] = {
    'running': False,
    'processed': 0,
    'geocoded': 0,
    'failed': 0,
    'aborted': None,
    'started_at': None,
    'finished_at': None,
}


def backfill_state() -> dict[str, Any]:
    """Return a copy of the current/last backfill job state."""
    return dict(_state)


async def run_backfill(sb: Any, *, limit: int = 200, force: bool = False) -> dict[str, Any]:
    """Geocode rows missing coordinates (properties + propiedades/cartera), one
    Nominatim request per second.

    Selector is ``geocoded_at IS NULL`` by default (rows never attempted) — NOT
    ``lat IS NULL`` — so previously-failed addresses are not retried on every run
    and don't burn the rate budget forever. Pass ``force=True`` to explicitly
    retry rows that failed before (``lat IS NULL``).

    Throttling (429/5xx/timeouts) is transient: the row keeps ``geocoded_at``
    NULL, we back off once, and a second throttle aborts the run so the next
    run picks up where this one stopped.

    Guarded by a module-level lock so only one backfill runs at a time per
    process; a concurrent call while one is already running is a no-op that
    just reports the in-progress state.
    """
    if sb is None:
        return {'skipped': True, **backfill_state()}
    if _lock.locked():
        return {'skipped': True, **backfill_state()}

    async with _lock:
        _state.update({
            'running': True, 'processed': 0, 'geocoded': 0, 'failed': 0, 'aborted': None,
            'started_at': datetime.now(timezone.utc).isoformat(), 'finished_at': None,
        })
        try:
            async with httpx.AsyncClient() as client:
                for table, select, order, build_address, viewbox_for in _TABLES:
                    aborted = await _backfill_table(
                        sb, client, table=table, select=select, order=order,
                        build_address=build_address, viewbox_for=viewbox_for,
                        limit=limit, force=force,
                    )
                    if aborted:
                        _state['aborted'] = aborted
                        logger.warning('backfill aborted: %s', aborted)
                        break
        finally:
            _state['running'] = False
            _state['finished_at'] = datetime.now(timezone.utc).isoformat()

        return backfill_state()


async def _backfill_table(
    sb: Any,
    client: httpx.AsyncClient,
    *,
    table: str,
    select: str,
    order: str,
    build_address: Callable[[dict[str, Any]], str | None],
    viewbox_for: Callable[[dict[str, Any]], str],
    limit: int,
    force: bool,
) -> str | None:
    """Geocode one table's pending rows; returns an abort reason on sustained throttling."""
    query = sb.table(table).select(select).order(order).limit(limit)
    query = query.is_('lat', 'null') if force else query.is_('geocoded_at', 'null')
    res = await query.execute()
    rows: list[dict[str, Any]] = res.data or []

    for row in rows:
        address = build_address(row)
        viewbox = viewbox_for(row)
        coords: tuple[float, float] | None = None
        if address:
            try:
                coords = await geocode(address, client=client, viewbox=viewbox)
            except TransientGeocodeError:
                await asyncio.sleep(THROTTLE_BACKOFF_SECONDS)
                try:
                    coords = await geocode(address, client=client, viewbox=viewbox)
                except TransientGeocodeError as exc:
                    # Still throttled after backing off — stop here; this row and the
                    # remaining ones keep geocoded_at NULL for the next run.
                    return f'{table}: {exc}'
        update: dict[str, Any] = {'geocoded_at': datetime.now(timezone.utc).isoformat()}
        if coords:
            update['lat'], update['lng'] = coords
            _state['geocoded'] += 1
        else:
            update['lat'], update['lng'] = None, None
            _state['failed'] += 1
        try:
            await sb.table(table).update(update).eq('id', row['id']).execute()
        except Exception as exc:
            logger.warning('backfill: failed to persist %s row %s: %s', table, row.get('id'), exc)
        _state['processed'] += 1
        await asyncio.sleep(RATE_LIMIT_SECONDS)
    return None
