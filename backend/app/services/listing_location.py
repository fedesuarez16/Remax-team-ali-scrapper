"""Coordinates published for a listing, never an agency's office or a search area."""
from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.services.source_registry import SEARCH_SOURCES


def valid_coordinates(lat: Any, lng: Any) -> tuple[float, float] | None:
    try:
        point = float(lat), float(lng)
    except (ValueError, TypeError, OverflowError):
        return None
    # Reject sentinels, swapped GeoJSON axes and non-finite values.
    if all(math.isfinite(n) for n in point) and -56 <= point[0] <= -21 \
            and -74 <= point[1] <= -53:
        return point
    return None


def remax_coordinates(item: dict[str, Any]) -> tuple[float, float] | None:
    location = item.get('location')
    if not isinstance(location, dict) or location.get('type') != 'Point':
        return None
    point = location.get('coordinates')
    if not isinstance(point, list) or len(point) != 2:
        return None
    return valid_coordinates(point[1], point[0])  # GeoJSON is longitude, latitude.


_NUMBER = r'(-?\d+(?:\.\d+)?)'
_TOKKO_CIRCLE = re.compile(r'L\.circle\(\s*\[\s*' + _NUMBER + r'\s*,\s*' + _NUMBER)
_TOKKO_FENWAY = re.compile(
    r'var\s+fenway\s*=\s*new\s+google\.maps\.LatLng\(\s*'
    + _NUMBER + r'\s*,\s*' + _NUMBER,
)


def tokko_coordinates(html: str) -> tuple[float, float] | None:
    soup = BeautifulSoup(html, 'html.parser')
    if not soup.select_one('#ficha_desc, .prop-details-cont'):
        return None
    for script in soup.find_all('script'):
        code = script.get_text()
        # Only the two listing-map templates verified in Urquiza/KW Suma.
        # A generic LatLng/geo scan could pick up an office in the footer.
        if 'openstreetmap_box' in code:
            match = _TOKKO_CIRCLE.search(code)
        elif 'ficha_streetview' in code or 'map-canvas' in code:
            match = _TOKKO_FENWAY.search(code)
        else:
            continue
        if match:
            return valid_coordinates(match[1], match[2])
    return None


async def listing_coordinates(
    row: dict[str, Any], *, client: httpx.AsyncClient,
) -> tuple[float, float] | None:
    """Read a verified public listing endpoint. No arbitrary URLs or redirects.

    Published map locations can themselves be approximate (Urquiza publishes
    a 400 m circle). They still preserve the advertised block/neighbourhood,
    unlike geocoding a numbered street without its cross streets.
    """
    from app.services.geocode import TransientGeocodeError

    url = str(row.get('url_origen') or '')
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.port not in (None, 443) \
                or parsed.username or parsed.password:
            return None
    except ValueError:
        return None
    host = (parsed.hostname or '').removeprefix('www.')
    is_remax = host == 'remax.com.ar' and parsed.path.startswith('/listings/')
    is_tokko = any(
        source.adapter == 'tokko'
        and host == (urlsplit(source.base_url).hostname or '').removeprefix('www.')
        for source in SEARCH_SOURCES
    ) and parsed.path.startswith('/p/')
    if is_remax:
        slug = parsed.path.removeprefix('/listings/').strip('/')
        if not slug or '/' in slug:
            return None
        url = ('https://api-ar.redremax.com/remaxweb-ar/api/listings/findBySlug/'
               + quote(slug, safe=''))
    elif not is_tokko:
        return None
    try:
        response = await client.get(url, timeout=10, follow_redirects=False)
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientGeocodeError(f'listing HTTP {response.status_code}')
        if response.status_code != 200:
            return None
        if is_remax:
            item = response.json().get('data')
            return remax_coordinates(item) if isinstance(item, dict) else None
        return tokko_coordinates(response.text)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise TransientGeocodeError('listing location temporarily unavailable') from exc
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
