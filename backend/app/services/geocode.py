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


# "502 e/ 17 y 18" / "31 entre 425 y 426" — Nominatim can't parse the
# between-streets part, and it poisons the match; keep only the street itself.
# Negative lookahead excludes "Entre Ríos" (the province), which is NOT the
# between-streets marker even though it starts with the same word.
_ENTRE_RE = re.compile(r'\s+(?:e/|entre)\s+(?!r[íi]os\b).*$', re.IGNORECASE)
# "48 esq 6" → "48 y 6": Nominatim resolves corners in "X y Z" form, not "esq".
_ESQ_RE = re.compile(r'\s+esq\.?\s+', re.IGNORECASE)


def _clean_street(direccion: str) -> str:
    """Strip the between-streets/corner notation grid addresses use, which
    Nominatim can't parse and which poisons the match otherwise."""
    cleaned = _ENTRE_RE.sub('', direccion.strip())
    cleaned = _ESQ_RE.sub(' y ', cleaned)
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
