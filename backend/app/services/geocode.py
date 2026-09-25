from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.listing_location import listing_coordinates, valid_coordinates

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

    Returns ``None`` when no candidate matches the street, house number and
    locality, or the address is only a block/corner/road. Raises
    ``TransientGeocodeError`` on 429/5xx/timeouts so callers can retry later
    without burning the row's ``geocoded_at``.
    """
    # A street, corner or between-streets description is not a house number.
    # Nominatim often silently resolves these to the centre of the whole road.
    street, separator, locality = address.partition(',')
    number = re.search(r'\s(\d+)\s*$', street)
    if not separator or not locality.strip() or not number or re.search(
        r'\bentre\s+(?!r[íi]os\b)|\by\b|/', street, re.IGNORECASE,
    ):
        return None
    expected_road = street[:number.start()].strip()
    if _norm_place(expected_road) in {'calle', 'avenida', 'av', 'av.'}:
        return None
    expected_locality = locality.split(',')[0].strip()
    params: dict[str, Any] = {
        'q': _build_query(address),
        'format': 'jsonv2',
        'limit': 5,
        'addressdetails': 1,
        'countrycodes': 'ar',
        'viewbox': viewbox,
        'bounded': int(viewbox == LP_VIEWBOX),
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
    if not isinstance(data, list):
        return None
    for candidate in data:
        if not isinstance(candidate, dict):
            continue
        details = candidate.get('address')
        if not isinstance(details, dict) or details.get('country_code') != 'ar':
            continue
        if str(details.get('house_number')) != number[1]:
            continue
        road = str(details.get('road') or details.get('pedestrian') or '')
        if _road_key(road) != _road_key(expected_road):
            continue
        places = [str(details.get(key) or '') for key in (
            'city', 'town', 'village', 'suburb', 'neighbourhood', 'quarter',
            'municipality', 'city_district', 'county',
        )]
        if not any(_norm_place(expected_locality) == _norm_place(place) for place in places):
            continue
        point = valid_coordinates(candidate.get('lat'), candidate.get('lon'))
        if point and (viewbox != LP_VIEWBOX or _inside_viewbox(point, viewbox)):
            return point
    return None


def _road_key(value: str) -> str:
    return re.sub(r'^(?:calle|avenida|av\.?)\s+', '', _norm_place(value))


def _inside_viewbox(point: tuple[float, float], viewbox: str) -> bool:
    left, top, right, bottom = map(float, viewbox.split(','))
    lat, lng = point
    return min(left, right) <= lng <= max(left, right) \
        and min(top, bottom) <= lat <= max(top, bottom)


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


# Remove cross streets ONLY when a real house number survives. Without one,
# retain them so the forward geocoder cannot mistake a road centroid for a
# property. Source-published coordinates can still locate those listings.
_GRID_STREET = r'\d+(?:\s*(?:bis\b|[a-z]\b))?'
_ENTRE_RE = re.compile(
    r'\s+(?:e\s*/|entre\s+(?!r[íi]os\b)|e\s+)\s*'
    rf'(?P<first>{_GRID_STREET})\s*(?:y|a)\s*(?P<second>{_GRID_STREET})'
    r'(?:\s+(?:(?:nro|n[°º]?|al)\s*)?(?P<number>\d+)\b)?',
    re.IGNORECASE,
)
# La Plata's grid is often written with the "e/" marker simply left out —
# "11 43 y 44" means calle 11 between 43 and 44. Requires THREE numbers so a
# plain corner ("3 y 42") is not mistaken for it; keeps the locality past the
# comma for the same reason `_ENTRE_RE` does.
_IMPLICIT_ENTRE_RE = re.compile(
    rf'^(?P<street>{_GRID_STREET})\s+(?P<first>{_GRID_STREET})\s+y\s+'
    rf'(?P<second>{_GRID_STREET})'
    r'(?:\s+(?:al\s+)?(?P<number>\d+)\b)?', re.IGNORECASE,
)
# Keep corners identifiable. The forward geocoder must not read the second
# street as a house number; those addresses need source-published coordinates.
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
_ZERO_ALTURA_RE = re.compile(r'\s+0\s*(?=,|$)')
# Some portals ship a breadcrumb ("19 y 45, Argentina | G.B.A. Zona Sur | La
# Plata"); commas are the separator Nominatim understands.
_PIPE_RE = re.compile(r'\s*\|\s*')
_SPACE_BEFORE_COMMA_RE = re.compile(r'\s+,')
_MULTI_SPACE_RE = re.compile(r'\s{2,}')


def _clean_street(direccion: str) -> str:
    """Preserve locality and house number; never reduce a block to a bare road."""
    cleaned = _PIPE_RE.sub(', ', direccion.strip())
    cleaned = _PARTIDO_WRAPPER_RE.sub('', cleaned)
    cleaned = _CNO_RE.sub('Camino ', cleaned)
    cleaned = _TYPE_PREFIX_RE.sub('', cleaned)
    cleaned = _PISO_RE.sub('', cleaned)
    cleaned = _UF_RE.sub('', cleaned)
    cleaned = _SIN_NUMERO_RE.sub('', cleaned)
    def between(match: re.Match[str]) -> str:
        prefix = _ALTURA_MARKER_RE.sub(' ', cleaned[:match.start()])
        prefix = re.sub(r'^calle\s+', '', prefix, flags=re.IGNORECASE)
        if re.search(r'\S\s+[1-9]\d*$', prefix):
            return ' '  # house number was written before e/
        number = match.group('number')
        if number and int(number) > 0:
            return f' {number}'
        return f' entre {match["first"].strip()} y {match["second"].strip()}'

    cleaned = _ENTRE_RE.sub(between, cleaned)
    # After `_ESQ_RE` a corner reads "X y Z", which must not then look like an
    # implicit "<street> <a> y <b>" — so the implicit rule runs first.
    cleaned = _IMPLICIT_ENTRE_RE.sub(lambda m: m['street'].strip() + between(m), cleaned)
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
    # Older RE/MAX rows lost geoLabel on ingestion. Recover only a locality
    # explicitly named in the listing, never the requested search area.
    if ',' not in direccion and not _LP_LOCALITY_RE.search(direccion):
        locality = re.search(
            r'\b(city bell|villa elisa|la plata|gonnet|tolosa|los hornos|ringuelet)\b',
            str(row.get('titulo') or ''), re.IGNORECASE,
        )
        if locality:
            direccion += f', {locality[0]}'
    return _clean_street(direccion)


def _address_propiedades(row: dict[str, Any]) -> str | None:
    direccion = _clean_street(row.get('direccion') or '')
    zona = (row.get('zona') or '').strip()
    parts = [p for p in (direccion, zona) if p]
    return ', '.join(parts) or None


# Only listing locality evidence selects the Gran La Plata bounds; the search
# query must never assign its requested locality to an ambiguous listing.
_LP_LOCALITY_RE = re.compile(
    r'\b(la plata|city bell|gonnet|villa elisa|tolosa|los hornos|ensenada|berisso|'
    r'romero|hernandez|abasto|ringuelet|olmos|arturo segui|melchor romero)\b',
    re.IGNORECASE,
)


def _viewbox_for_properties(row: dict[str, Any]) -> str:
    direccion = _address_properties(row) or ''
    if re.search(r'\b(entre r[íi]os|santa fe|c[oó]rdoba|mendoza|uruguay)\b',
                 direccion, re.IGNORECASE):
        return BA_VIEWBOX
    # A street called "La Plata" or "Romero" is not evidence of locality.
    _, separator, context = direccion.partition(',')
    if separator:
        direccion = context
    return LP_VIEWBOX if _LP_LOCALITY_RE.search(direccion) else BA_VIEWBOX


def _viewbox_for_propiedades(_row: dict[str, Any]) -> str:
    return LP_VIEWBOX  # entire cartera is Gran La Plata


# (table, select, order-by, order-desc, address builder, per-row viewbox) — both
# tables share one run so the Nominatim rate budget is spent sequentially, never
# in parallel. `properties` orders newest-first so a fresh search's rows get
# priority over any older backlog still stuck at `geocoded_at IS NULL`.
_TABLES: list[tuple[
    str, str, str, bool, Callable[[dict[str, Any]], str | None], Callable[[dict[str, Any]], str],
]] = [
    ('properties', 'id,direccion,titulo,url_origen', 'created_at', True,
     _address_properties, _viewbox_for_properties),
    ('propiedades', 'id,direccion,zona', 'id', False, _address_propiedades, _viewbox_for_propiedades),
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


async def run_backfill(
    sb: Any, *, limit: int = 200, force: bool = False,
    job_id: str | None = None, recheck: bool = False, dry_run: bool = False,
) -> dict[str, Any]:
    """Geocode rows missing coordinates (properties + propiedades/cartera), one
    Nominatim request per second.

    Selector is ``geocoded_at IS NULL`` by default (rows never attempted) — NOT
    ``lat IS NULL`` — so previously-failed addresses are not retried on every run
    and don't burn the rate budget forever. Pass ``force=True`` to explicitly
    retry rows that failed before (``lat IS NULL``). ``job_id`` scopes a pass
    to one search (including reused properties). ``recheck=True`` also includes
    previously located rows; ``dry_run=True`` reports before/after coordinates
    without writing them. Recheck requires an explicit search scope.

    Throttling (429/5xx/timeouts) is transient: the row keeps ``geocoded_at``
    NULL, we back off once, and a second throttle aborts the run so the next
    run picks up where this one stopped.

    Guarded by a module-level lock so only one backfill runs at a time per
    process; a concurrent call while one is already running is a no-op that
    just reports the in-progress state.
    """
    if recheck and not job_id:
        raise ValueError('recheck requires a job_id')
    if sb is None:
        return {'skipped': True, **backfill_state()}
    if _lock.locked():
        return {'skipped': True, **backfill_state()}

    async with _lock:
        _state.update({
            'running': True, 'processed': 0, 'geocoded': 0, 'failed': 0, 'aborted': None,
            'started_at': datetime.now(timezone.utc).isoformat(), 'finished_at': None,
            'job_id': job_id, 'dry_run': dry_run, 'changes': [],
        })
        try:
            async with httpx.AsyncClient() as client:
                source_cache: dict[str, tuple[float, float] | None] = {}
                for table, select, order, order_desc, build_address, viewbox_for in _TABLES:
                    if job_id and table != 'properties':
                        continue
                    aborted = await _backfill_table(
                        sb, client, table=table, select=select, order=order, order_desc=order_desc,
                        build_address=build_address, viewbox_for=viewbox_for,
                        limit=limit, force=force, job_id=job_id, recheck=recheck,
                        dry_run=dry_run, source_cache=source_cache,
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
    order_desc: bool,
    build_address: Callable[[dict[str, Any]], str | None],
    viewbox_for: Callable[[dict[str, Any]], str],
    limit: int,
    force: bool,
    job_id: str | None = None,
    recheck: bool = False,
    dry_run: bool = False,
    source_cache: dict[str, tuple[float, float] | None] | None = None,
) -> str | None:
    """Geocode one table's pending rows; returns an abort reason on sustained throttling."""
    if job_id:
        columns = f'{select},lat,lng,geocoded_at'
        linked = await sb.table('search_property_results').select(
            f'properties({columns})'
        ).eq('job_id', job_id).order('property_id').limit(limit).execute()
        direct = await sb.table(table).select(columns).eq(
            'scraping_job_id', job_id,
        ).order('id').limit(limit).execute()
        candidates = [r['properties'] for r in (linked.data or []) if r.get('properties')]
        candidates.extend(direct.data or [])
        unique = {r['id']: r for r in candidates}
        rows = [r for r in unique.values() if recheck or (
            r.get('lat') is None if force else r.get('geocoded_at') is None
        )][:limit]
    else:
        query = sb.table(table).select(select).order(order, desc=order_desc).limit(limit)
        query = query.is_('lat', 'null') if force else query.is_('geocoded_at', 'null')
        res = await query.execute()
        rows = res.data or []

    for row in rows:
        address = build_address(row)
        viewbox = viewbox_for(row)
        coords: tuple[float, float] | None = None
        if address or row.get('url_origen'):
            try:
                coords = await resolve_property_coordinates(
                    row, address=address, client=client, viewbox=viewbox, cache=source_cache,
                )
            except TransientGeocodeError:
                await asyncio.sleep(THROTTLE_BACKOFF_SECONDS)
                try:
                    coords = await resolve_property_coordinates(
                        row, address=address, client=client, viewbox=viewbox, cache=source_cache,
                    )
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
        if job_id:
            _state['changes'].append({
                'id': row['id'], 'before': [row.get('lat'), row.get('lng')],
                'after': [update['lat'], update['lng']],
            })
        if not dry_run:
            try:
                query = sb.table(table).update(update).eq('id', row['id'])
                # Do not overwrite a location if its address changed while fetching.
                if row.get('direccion') is not None:
                    query = query.eq('direccion', row['direccion'])
                await query.execute()
            except Exception as exc:
                logger.warning('backfill: failed to persist %s row %s: %s', table, row.get('id'), exc)
                return f'{table}: failed to persist row {row.get("id")}'
        _state['processed'] += 1
        await asyncio.sleep(RATE_LIMIT_SECONDS)
    return None


async def resolve_property_coordinates(
    row: dict[str, Any], *, address: str | None, client: httpx.AsyncClient,
    viewbox: str, cache: dict[str, tuple[float, float] | None] | None = None,
) -> tuple[float, float] | None:
    url = str(row.get('url_origen') or '')
    if cache is not None and url in cache:
        point = cache[url]
    else:
        point = await listing_coordinates(row, client=client)
        if cache is not None and url:
            cache[url] = point
    if point and (viewbox != LP_VIEWBOX or _inside_viewbox(point, viewbox)):
        return point
    return await geocode(address, client=client, viewbox=viewbox) if address else None
