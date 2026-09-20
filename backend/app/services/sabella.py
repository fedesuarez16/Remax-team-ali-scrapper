"""Sabella's legacy Xintel catalogue with exact locality and neighborhood filters."""
from __future__ import annotations

import asyncio
import re
import unicodedata
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag  # type: ignore[import-untyped]

from app.models.property import Moneda, RawProperty, ScrapingFilters, TipoPropiedad
from app.services.source_filters import matches_source_filters
from app.services.source_registry import SearchSource

Progress = Callable[[str, str, int], Awaitable[None]]

_LOCATION_PARAMS = {
    'abasto': ('la plata', 'Abasto'),
    'arana': ('la plata', 'Arana'),
    'arturo segui': ('la plata', 'Arturo Segui'),
    'city bell': ('city bell', None),
    'gorina': ('la plata', 'Gorina'),
    'grand bell': ('city bell', 'Grand Bell'),
    'jose hernandez': ('la plata', 'Jose Hernandez'),
    'la plata': ('la plata', 'Casco Urbano'),
    'los hornos': ('la plata', 'Los Hornos'),
    'manuel b gonnet': ('la plata', 'Manuel B Gonnet'),
    'tolosa': ('la plata', 'Tolosa'),
    'villa elisa': ('la plata', 'Villa Elisa'),
}
_ALIASES = {'casco urbano': 'la plata', 'gonnet': 'manuel b gonnet', 'joaquin gorina': 'gorina'}
_TYPE_CODES: dict[TipoPropiedad, tuple[str, str | None]] = {
    'casa': ('C', None), 'departamento': ('D', None), 'ph': ('All', 'PH'),
    'local': ('L', None), 'oficina': ('O', None), 'terreno': ('T', None),
}
_TITLE_TYPES: tuple[tuple[str, TipoPropiedad], ...] = (
    ('departamento', 'departamento'), ('casa', 'casa'), ('duplex', 'casa'),
    ('ph', 'ph'), ('local', 'local'), ('oficina', 'oficina'),
    ('lote', 'terreno'), ('terreno', 'terreno'), ('campo', 'terreno'),
)


def _plain(value: object) -> str:
    text = unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode()
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', text.lower()).split())


def _number(value: object) -> float | None:
    match = re.search(r'\d[\d.,]*', str(value))
    if not match:
        return None
    text = match.group().rstrip('.,')
    if ',' in text:
        text = text.replace('.', '').replace(',', '.')
    elif '.' in text and all(len(part) == 3 for part in text.split('.')[1:]):
        text = text.replace('.', '')
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number > 0 else None


def resolve_location(zona: str) -> tuple[str, str | None] | None:
    for part in (_plain(piece) for piece in zona.split(',')):
        key = _ALIASES.get(part, part)
        if key in _LOCATION_PARAMS:
            return _LOCATION_PARAMS[key]
    return None


def search_params(
    filters: ScrapingFilters, location: tuple[str, str | None], page: int,
) -> dict[str, str | int]:
    operation = {'venta': 'V', 'alquiler': 'A'}.get(filters.tipo_operacion or 'venta')
    if operation is None:
        return {}
    locality, neighborhood = location
    params: dict[str, str | int] = {'p': page, 'ope': operation, 'loc': locality}
    if neighborhood:
        params['b'] = neighborhood
    if len(filters.tipos_propiedad) == 1 and filters.tipos_propiedad[0] in _TYPE_CODES:
        property_type, subtype = _TYPE_CODES[filters.tipos_propiedad[0]]
        params['tipo'] = property_type
        if subtype:
            params['in_tpr'] = subtype
    return params


def _property_type(title: str) -> TipoPropiedad:
    normalized = _plain(title)
    return next((kind for label, kind in _TITLE_TYPES if label in normalized), 'otro')


def parse_card(card: Tag, source: SearchSource) -> RawProperty | None:
    detail_link = card.select_one('.property-image a[href]')
    price_node = card.select_one('.property-container .property-title h4')
    address_block = card.select_one('.property-container .property-title .title-left > span')
    title_node = card.select_one('.property-content .property-title h3')
    operation_node = card.select_one('.p-tag')
    if not detail_link or not address_block or not title_node or not operation_node:
        return None
    operation_text = _plain(operation_node.get_text())
    if operation_text not in {'venta', 'alquiler'}:
        return None
    image = card.select_one('.property-image img[src]')
    details = card.select('.property-content .list-item span')
    title = title_node.get_text(' ', strip=True)
    price_text = price_node.get_text(' ', strip=True) if price_node else ''
    currency: Moneda = 'ARS' if '$' in price_text and 'u$s' not in price_text.lower() else 'USD'
    url = urljoin(f'{source.base_url}/', str(detail_link.get('href') or ''))
    listing_match = re.search(r'-ficha-([a-z0-9]+)', url, re.I)
    return RawProperty(
        fuente=source.id,
        titulo=title or None,
        descripcion=(card.select_one('.property-content > p').get_text(' ', strip=True)
                     if card.select_one('.property-content > p') else None),
        direccion=address_block.get_text(' ', strip=True),
        precio=_number(price_text),
        moneda=currency,
        tipo_operacion='venta' if operation_text == 'venta' else 'alquiler',
        tipo_propiedad=_property_type(title),
        banos=int(value) if len(details) > 2 and (value := _number(details[2].get_text())) else None,
        m2_total=_number(details[0].get_text()) if details else None,
        m2_cubiertos=_number(details[0].get_text())
        if details and 'cub' in _plain(details[0].get_text()) else None,
        imagenes=[str(image.get('src'))] if image and image.get('src') else [],
        url_origen=url,
        raw={
            'source_id': source.id,
            'listing_id': listing_match.group(1) if listing_match else url,
            'dormitorios': int(value)
            if len(details) > 1 and (value := _number(details[1].get_text())) else None,
        },
    )


def parse_page(html: str, source: SearchSource) -> tuple[list[RawProperty], int]:
    soup = BeautifulSoup(html, 'html.parser')
    properties = [
        prop for card in soup.select('.single-property')
        if isinstance(card, Tag) and (prop := parse_card(card, source)) is not None
    ]
    pages = [
        int(match.group(1)) + 1 for link in soup.select('a[href]')
        if (match := re.search(r'[?&]p=(\d+)', str(link.get('href') or '')))
    ]
    return properties, max(pages, default=1)


async def scrape_sabella(
    source: SearchSource, filters: ScrapingFilters, on_progress: Progress,
) -> list[RawProperty]:
    from app.core.config import settings
    from app.services.apify import _BROWSER_UA

    await on_progress(source.id, 'running', 0)
    zona = filters.localidades[0] if filters.localidades else (filters.zona or '')
    location = resolve_location(zona)
    first_params = search_params(filters, location, 0) if location else {}
    if not location or not first_params:
        await on_progress(source.id, 'done', 0)
        return []
    url = f'{source.base_url}/propiedades.php'
    async with httpx.AsyncClient(
        timeout=settings.WEBSITE_HTTP_TIMEOUT, follow_redirects=True,
        headers={'User-Agent': _BROWSER_UA},
    ) as client:
        response = await client.get(url, params=first_params)
        response.raise_for_status()
        first, total_pages = parse_page(response.text, source)
        semaphore = asyncio.Semaphore(6)

        async def fetch(page: int) -> list[RawProperty]:
            async with semaphore:
                page_response = await client.get(url, params=search_params(filters, location, page))
                page_response.raise_for_status()
                return parse_page(page_response.text, source)[0]

        rest = await asyncio.gather(*(fetch(page) for page in range(1, total_pages)))

    results: list[RawProperty] = []
    seen: set[str] = set()
    for page in [first, *rest]:
        for prop in page:
            if not prop.url_origen or prop.url_origen in seen:
                continue
            seen.add(prop.url_origen)
            if matches_source_filters(prop, filters):
                results.append(prop)
        await on_progress(source.id, 'running', len(results))
    await on_progress(source.id, 'done', len(results))
    return results
