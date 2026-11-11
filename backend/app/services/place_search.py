"""Place search for the /map search bar.

Two upstreams, each used for what its usage policy allows:
- Photon (komoot, OSM data) for search-as-you-type suggestions. Nominatim's
  policy forbids autocomplete, Photon is built for it.
- Geometry is fetched once per *selection*, never per keystroke: Nominatim
  `/lookup` for an area's boundary polygon, Overpass for every way of a street
  (OSM splits a street in many ways, so a single-way lookup only gives a piece).

Coordinates leave this module as (lat, lng) — the order Leaflet uses.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Literal

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

PHOTON_URL = 'https://photon.komoot.io/api/'
NOMINATIM_LOOKUP_URL = 'https://nominatim.openstreetmap.org/lookup'
NOMINATIM_SEARCH_URL = 'https://nominatim.openstreetmap.org/search'
# Overpass returns a whole street but public instances 502/time out under
# load, so it gets a short timeout and Nominatim (max 40 ways) backs it up.
OVERPASS_URL = 'https://overpass-api.de/api/interpreter'
OVERPASS_TIMEOUT_SECONDS = 10
# Radius around the picked point: longer than any Gran La Plata avenue half.
STREET_SEARCH_METERS = 8000
# Generic on purpose: no contact data leaves the app. Overpass answers 406
# to agents with extra text after the product token, so keep it bare.
USER_AGENT = 'multi-agent-realstate/0.1'

# Gran La Plata plus margin: (south, west, north, east).
_SOUTH, _WEST, _NORTH, _EAST = -35.15, -58.40, -34.70, -57.75
PHOTON_BBOX = f'{_WEST},{_SOUTH},{_EAST},{_NORTH}'
_CENTER_LAT, _CENTER_LNG = -34.92, -57.95

_AREA_TYPES = {'district', 'locality', 'city', 'county'}
_CACHE_MAX = 512

PlaceKind = Literal['address', 'street', 'area', 'poi']
LatLng = tuple[float, float]

# Shared by search and geometry results; keys are namespaced tuples.
_cache: dict[tuple[Any, ...], Any] = {}


class PlaceSearchError(Exception):
    """An upstream (Photon, Nominatim, Overpass) failed or throttled us."""


class Place(BaseModel):
    id: str
    name: str
    label: str
    kind: PlaceKind
    lat: float
    lng: float
    osm_type: str
    osm_id: int
    city: str | None = None


class PlaceGeometry(BaseModel):
    polygon: list[LatLng] | None = None
    lines: list[list[LatLng]] | None = None


def _cache_put(key: tuple[Any, ...], value: Any) -> None:
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = value


def parse_photon_feature(feature: dict[str, Any]) -> Place | None:
    props = feature.get('properties') or {}
    coords = (feature.get('geometry') or {}).get('coordinates') or []
    if len(coords) < 2:
        return None
    name = (props.get('name') or '').strip()
    street = (props.get('street') or '').strip()
    number = (props.get('housenumber') or '').strip()
    ptype = props.get('type')
    osm_key = props.get('osm_key')

    kind: PlaceKind
    if ptype == 'street' or osm_key == 'highway':
        kind = 'street'
    elif number and street and not name:
        kind, name = 'address', f'{street} {number}'
    elif ptype in _AREA_TYPES or osm_key in ('place', 'boundary'):
        kind = 'area'
    else:
        kind = 'poi'
    if not name:
        return None

    context: list[str] = []
    # A street's district changes segment by segment; naming only the town
    # lets the segments collapse into one suggestion.
    keys = ('locality', 'city') if kind == 'street' else ('district', 'locality', 'city')
    for key in keys:
        value = (props.get(key) or '').strip()
        if value and value != name and value not in context:
            context.append(value)
    osm_type = str(props.get('osm_type') or '')
    osm_id = int(props.get('osm_id') or 0)
    return Place(
        id=f'{osm_type}{osm_id}',
        name=name,
        label=', '.join([name, *context]),
        kind=kind,
        lat=float(coords[1]),
        lng=float(coords[0]),
        osm_type=osm_type,
        osm_id=osm_id,
        city=(props.get('city') or props.get('locality') or '').strip() or None,
    )


async def _get_json(
    client: httpx.AsyncClient, url: str, params: dict[str, Any], *, timeout: float = 20,
) -> Any:
    try:
        resp = await client.get(
            url, params=params, headers={'User-Agent': USER_AGENT}, timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise PlaceSearchError(f'{url}: {exc}') from exc
    if resp.status_code >= 400:
        raise PlaceSearchError(f'{url}: HTTP {resp.status_code}')
    return resp.json()


async def search_places(q: str, *, client: httpx.AsyncClient, limit: int = 8) -> list[Place]:
    query = q.strip().lower()
    key = ('search', query, limit)
    if key in _cache:
        return list(_cache[key])
    data = await _get_json(client, PHOTON_URL, {
        'q': query, 'limit': limit, 'bbox': PHOTON_BBOX,
        'lat': _CENTER_LAT, 'lon': _CENTER_LNG,
    })
    places: list[Place] = []
    seen: set[tuple[str, str]] = set()
    for feature in data.get('features') or []:
        place = parse_photon_feature(feature)
        # Photon returns one feature per street segment sometimes — dedupe by label.
        if place and (place.kind, place.label) not in seen:
            seen.add((place.kind, place.label))
            places.append(place)
    _cache_put(key, places)
    return list(places)


def _ring_area(ring: list[list[float]]) -> float:
    return abs(sum(
        ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1]
        for i in range(len(ring))
    )) / 2


def largest_ring(geojson: dict[str, Any]) -> list[LatLng] | None:
    """Outer ring of the biggest polygon, as (lat, lng). Holes are ignored: a
    barrio filter that drops a park in the middle is not worth the complexity."""
    gtype = geojson.get('type')
    coords = geojson.get('coordinates') or []
    if gtype == 'Polygon':
        rings = [coords[0]] if coords else []
    elif gtype == 'MultiPolygon':
        rings = [poly[0] for poly in coords if poly]
    else:
        return None
    rings = [r for r in rings if len(r) >= 3]
    if not rings:
        return None
    best = max(rings, key=_ring_area)
    return [(float(lat), float(lng)) for lng, lat in best]


def parse_overpass_lines(data: dict[str, Any]) -> list[list[LatLng]]:
    lines: list[list[LatLng]] = []
    for element in data.get('elements') or []:
        geometry = element.get('geometry') or []
        line = [(float(pt['lat']), float(pt['lon'])) for pt in geometry]
        if len(line) >= 2:
            lines.append(line)
    return lines


# Consecutive ways of one street share endpoints; dual carriageways and
# mapping slop leave small gaps. Anything farther is a different street.
_STREET_JOIN_METERS = 60.0


def _meters(a: LatLng, b: LatLng) -> float:
    """Equirectangular distance — exact enough at city scale."""
    kx = 111_320 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[0] - b[0]) * 110_540, (a[1] - b[1]) * kx)


def _segment_meters(p: LatLng, a: LatLng, b: LatLng) -> float:
    kx = 111_320 * math.cos(math.radians(p[0]))
    ax, ay = (a[1] - p[1]) * kx, (a[0] - p[0]) * 110_540
    bx, by = (b[1] - p[1]) * kx, (b[0] - p[0]) * 110_540
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length2))
    return math.hypot(ax + t * dx, ay + t * dy)


def _line_meters(p: LatLng, line: list[LatLng]) -> float:
    return min(_segment_meters(p, line[i], line[i + 1]) for i in range(len(line) - 1))


def _touches(a: list[LatLng], b: list[LatLng]) -> bool:
    return any(
        _meters(end, vertex) <= _STREET_JOIN_METERS
        for first, second in ((a, b), (b, a))
        for end in (first[0], first[-1])
        for vertex in second
    )


def connected_street(lines: list[list[LatLng]], point: LatLng) -> list[list[LatLng]]:
    """The street the user picked: the way nearest to `point` plus every
    same-named way chained to it. Same-named streets of other towns drop out
    ("Calle 7" exists in Berisso, Ensenada and Guernica)."""
    if not lines:
        return []
    start = min(range(len(lines)), key=lambda i: _line_meters(point, lines[i]))
    order, pending = [start], set(range(len(lines))) - {start}
    for current in order:
        for other in sorted(pending):
            if _touches(lines[current], lines[other]):
                pending.discard(other)
                order.append(other)
    return [lines[i] for i in order]


def parse_nominatim_lines(data: Any) -> list[list[LatLng]]:
    lines: list[list[LatLng]] = []
    for row in data if isinstance(data, list) else []:
        geojson = row.get('geojson') or {}
        if geojson.get('type') == 'LineString':
            parts = [geojson.get('coordinates') or []]
        elif geojson.get('type') == 'MultiLineString':
            parts = geojson.get('coordinates') or []
        else:
            continue
        for part in parts:
            line = [(float(lat), float(lng)) for lng, lat in part]
            if len(line) >= 2:
                lines.append(line)
    return lines


async def _street_lines(
    client: httpx.AsyncClient, name: str, city: str | None,
    lat: float | None, lng: float | None,
) -> list[list[LatLng]]:
    area = (
        f'(around:{STREET_SEARCH_METERS},{lat},{lng})'
        if lat is not None and lng is not None
        else f'({_SOUTH},{_WEST},{_NORTH},{_EAST})'
    )
    query = (
        f'[out:json][timeout:{OVERPASS_TIMEOUT_SECONDS}];'
        f'way["highway"]["name"="{_overpass_string(name)}"]{area};out geom;'
    )
    try:
        data = await _get_json(
            client, OVERPASS_URL, {'data': query}, timeout=OVERPASS_TIMEOUT_SECONDS,
        )
        return parse_overpass_lines(data)
    except PlaceSearchError as exc:
        logger.warning('overpass failed, falling back to nominatim: %s', exc)
    params: dict[str, Any] = {
        'street': name, 'format': 'json', 'polygon_geojson': 1, 'dedupe': 0, 'limit': 40,
        'viewbox': f'{_WEST},{_NORTH},{_EAST},{_SOUTH}', 'bounded': 1,
    }
    if city:
        params['city'] = city
    return parse_nominatim_lines(await _get_json(client, NOMINATIM_SEARCH_URL, params))


def _overpass_string(value: str) -> str:
    return value.replace('\\', '\\\\').replace('"', '\\"')


async def place_geometry(
    kind: Literal['area', 'street'],
    *,
    osm_type: str,
    osm_id: int,
    name: str,
    client: httpx.AsyncClient,
    city: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
) -> PlaceGeometry:
    key = ('geometry', kind, osm_type, osm_id, name, city, lat, lng)
    if key in _cache:
        return _cache[key]  # type: ignore[no-any-return]

    if kind == 'street':
        lines = await _street_lines(client, name, city, lat, lng)
        if lat is not None and lng is not None:
            lines = connected_street(lines, (lat, lng))
        geometry = PlaceGeometry(lines=lines or None)
    elif osm_type in ('R', 'W'):
        data = await _get_json(client, NOMINATIM_LOOKUP_URL, {
            'osm_ids': f'{osm_type}{osm_id}', 'format': 'json',
            'polygon_geojson': 1, 'polygon_threshold': 0.0001,
        })
        first = data[0] if isinstance(data, list) and data else {}
        geometry = PlaceGeometry(polygon=largest_ring(first.get('geojson') or {}))
    else:
        # A place mapped as a node (very common for OSM barrios) has no
        # boundary: the caller falls back to a radius around the point.
        geometry = PlaceGeometry()

    _cache_put(key, geometry)
    return geometry
