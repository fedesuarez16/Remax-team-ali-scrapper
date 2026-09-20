"""Alberto Dacal's Brokian catalogue, scoped by its native location routes."""
from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from collections.abc import Awaitable, Callable, Mapping

import httpx
from bs4 import BeautifulSoup, Tag  # type: ignore[import-untyped]

from app.models.property import Moneda, RawProperty, ScrapingFilters, TipoOperacion, TipoPropiedad
from app.services.source_filters import matches_source_filters
from app.services.source_registry import SearchSource

Progress = Callable[[str, str, int], Awaitable[None]]

_LOCATION_PREFIX = 'Argentina-G.B.A.+Zona+Sur-La+Plata-'
_LOCATION_ROUTES = {
    'abasto': ('Abasto', 'Abasto'),
    'arturo segui': ('Arturo+Segui', 'Arturo Segui'),
    'city bell': ('City+Bell', 'City Bell'),
    'countries b cerrado la plata': (
        'Countries+B.Cerrado+(La+Plata)', 'Countries B.Cerrado (La Plata)',
    ),
    'ignacio correas arana': ('Ignacio+Correas+Arana', 'Ignacio Correas Arana'),
    'la plata': ('La+Plata', 'La Plata'),
    'manuel b gonnet': ('Manuel+B+Gonnet', 'Manuel B Gonnet'),
    'tolosa': ('Tolosa', 'Tolosa'),
    'villa elisa': ('Villa+Elisa', 'Villa Elisa'),
}
_LOCATION_ALIASES = {
    'casco urbano': 'la plata',
    'gonnet': 'manuel b gonnet',
    'countries b cerrado': 'countries b cerrado la plata',
}
_OPERATION_ROUTES: dict[TipoOperacion, str] = {
    'venta': 'venta',
    'alquiler': 'alquileres',
    'alquiler_temp': 'alquileres+temporales',
}
_BUSINESS_OPERATIONS: dict[str, TipoOperacion] = {
    'sell': 'venta', 'leaseout': 'alquiler',
}
_SINGLE_TYPE_ROUTES: dict[TipoPropiedad, str] = {
    'casa': 'casas', 'departamento': 'departamentos', 'terreno': 'terrenos+o+lotes',
}
_CARD_TYPES: dict[str, TipoPropiedad] = {
    'casa': 'casa', 'quinta': 'casa', 'chalet': 'casa', 'duplex': 'casa',
    'departamento': 'departamento', 'monoambiente': 'departamento',
    'departamento de pasillo': 'ph', 'ph': 'ph',
    'local comercial': 'local', 'local': 'local',
    'oficina': 'oficina', 'consultorio': 'oficina',
    'terreno o lote': 'terreno', 'terreno': 'terreno', 'campo': 'terreno',
}
_SCHEMA_TYPES: dict[str, TipoPropiedad] = {
    'house': 'casa', 'apartment': 'departamento',
}


def _plain(value: object) -> str:
    text = unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode()
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', text.lower()).split())


def resolve_location(zona: str) -> tuple[str, str] | None:
    """Return the site's exact route value and the locality it must emit."""
    parts = [_plain(part) for part in zona.split(',') if _plain(part)]
    for part in parts:
        key = _LOCATION_ALIASES.get(part, part)
        if key in _LOCATION_ROUTES:
            return _LOCATION_ROUTES[key]
    return None


def search_url(source: SearchSource, filters: ScrapingFilters, route: str) -> str:
    operation = _OPERATION_ROUTES.get(filters.tipo_operacion or 'venta', 'venta')
    property_type = 'todas'
    if len(filters.tipos_propiedad) == 1:
        property_type = _SINGLE_TYPE_ROUTES.get(filters.tipos_propiedad[0], 'todas')
    return f'{source.base_url}/propiedades/{property_type}/{operation}/{_LOCATION_PREFIX}{route}'


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _positive_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)  # JSON-LD fields are numeric, not localized strings.
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _positive_int(value: object) -> int | None:
    number = _positive_number(value)
    return int(number) if number is not None else None


def _original_image(url: str) -> str:
    """Brokian thumbnails preserve the original id and filename in their URL."""
    return re.sub(r'/conversions/([^/?]+)-thumbnail\.webp(?=\?|$)', r'/\1.jpg', url)


def _json_listing(card: Tag) -> Mapping[str, object] | None:
    script = card.select_one('script[type="application/ld+json"]')
    if not script:
        return None
    try:
        payload = json.loads(script.string or script.get_text())
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, Mapping) and payload.get('@type') == 'RealEstateListing' else None


def parse_card(card: Tag, source: SearchSource) -> RawProperty | None:
    data = _json_listing(card)
    if not data:
        return None
    about = _mapping(data.get('about'))
    address = _mapping(about.get('address'))
    offers = _mapping(data.get('offers'))
    locality = str(address.get('addressLocality') or '').strip()
    street = str(address.get('streetAddress') or '').strip()
    if not locality:
        return None

    business = str(offers.get('businessFunction') or '').rsplit('#', 1)[-1]
    operation = _BUSINESS_OPERATIONS.get(_plain(business))
    if not operation:
        return None
    card_type = _plain(card.get('data-type') or '')
    property_type = _CARD_TYPES.get(card_type) or _SCHEMA_TYPES.get(
        _plain(about.get('@type')),
        'otro',
    )
    currency: Moneda = 'ARS' if _plain(offers.get('priceCurrency')) == 'ars' else 'USD'
    floor_size = _mapping(about.get('floorSize'))
    geo = _mapping(about.get('geo'))
    image = data.get('image')
    images = [_original_image(str(image))] if image else []
    raw_features = about.get('amenityFeature')
    features = raw_features if isinstance(raw_features, list) else []
    description_node = card.select_one('.description')
    description = description_node.get_text(' ', strip=True) if description_node else ''
    listing_id = str(card.get('data-id') or data.get('url') or '').rsplit('--', 1)[-1]
    return RawProperty(
        fuente=source.id,
        titulo=str(data.get('name') or '').strip() or None,
        descripcion=str(data.get('description') or description).strip() or None,
        direccion=', '.join(part for part in (street, locality) if part),
        precio=_positive_number(offers.get('price')),
        moneda=currency,
        tipo_operacion=operation,
        tipo_propiedad=property_type,
        ambientes=_positive_int(about.get('numberOfRooms')),
        banos=_positive_int(about.get('numberOfBathroomsTotal')),
        m2_total=_positive_number(floor_size.get('value')),
        imagenes=images,
        url_origen=str(data.get('url') or offers.get('url') or '').strip() or None,
        amenities=[
            str(feature.get('name'))
            for feature in features
            if isinstance(feature, Mapping) and feature.get('value') and feature.get('name')
        ],
        raw={
            'source_id': source.id,
            'listing_id': listing_id,
            'dormitorios': _positive_int(about.get('numberOfBedrooms')),
            'latitude': geo.get('latitude'),
            'longitude': geo.get('longitude'),
        },
    )


def parse_page(html: str, source: SearchSource) -> tuple[list[RawProperty], int]:
    soup = BeautifulSoup(html, 'html.parser')
    properties = [
        prop for card in soup.select('.property-item')
        if isinstance(card, Tag) and (prop := parse_card(card, source)) is not None
    ]
    page_numbers = [
        int(text) for link in soup.select('.pagination a')
        if (text := link.get_text(strip=True)).isdigit()
    ]
    return properties, max(page_numbers, default=1)


async def scrape_alberto_dacal(
    source: SearchSource, filters: ScrapingFilters, on_progress: Progress,
) -> list[RawProperty]:
    from app.core.config import settings
    from app.services.apify import _BROWSER_UA

    await on_progress(source.id, 'running', 0)
    zona = filters.localidades[0] if filters.localidades else (filters.zona or '')
    resolved = resolve_location(zona)
    if not resolved:
        await on_progress(source.id, 'done', 0)
        return []
    route, expected_locality = resolved
    base_search_url = search_url(source, filters, route)
    headers = {
        'User-Agent': _BROWSER_UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
    async with httpx.AsyncClient(
        timeout=settings.WEBSITE_HTTP_TIMEOUT, follow_redirects=True, headers=headers,
    ) as client:
        first_response = await client.get(base_search_url)
        first_response.raise_for_status()
        first, total_pages = parse_page(first_response.text, source)

        semaphore = asyncio.Semaphore(6)

        async def fetch(page: int) -> list[RawProperty]:
            async with semaphore:
                response = await client.get(base_search_url, params={'page': page})
                response.raise_for_status()
                return parse_page(response.text, source)[0]

        remaining = await asyncio.gather(*(fetch(page) for page in range(2, total_pages + 1)))

    results: list[RawProperty] = []
    seen: set[str] = set()
    for page_number, page in enumerate([first, *remaining], 1):
        for prop in page:
            # The route is precise, but this assertion protects us from a site-side
            # regression or a sponsored card injected from another locality.
            if _plain(prop.direccion.rsplit(',', 1)[-1]) != _plain(expected_locality):
                continue
            if not prop.url_origen or prop.url_origen in seen:
                continue
            seen.add(prop.url_origen)
            if matches_source_filters(prop, filters):
                results.append(prop)
        await on_progress(source.id, 'running', len(results))
        if page_number >= total_pages:
            break
    await on_progress(source.id, 'done', len(results))
    return results
