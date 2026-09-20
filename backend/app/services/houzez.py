"""Houzez catalogue scraper for reviewed agency websites such as Axion."""
from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from collections.abc import Awaitable, Callable

import httpx
from bs4 import BeautifulSoup, Tag  # type: ignore[import-untyped]

from app.models.property import Moneda, RawProperty, ScrapingFilters, TipoOperacion, TipoPropiedad
from app.services.source_filters import matches_source_filters
from app.services.source_registry import SearchSource

Progress = Callable[[str, str, int], Awaitable[None]]

_LOCATIONS = {
    'abasto': ('abasto', 'Abasto'),
    'arana': ('arana', 'Arana'),
    'city bell': ('city-bell', 'City Bell'),
    'grand bell': ('grand-bell', 'Grand Bell'),
    'haras del sur': ('haras-del-sur', 'Haras del Sur'),
    'joaquin gorina': ('joaquin-gorina', 'Joaquín Gorina'),
    'jose hernandez': ('jose-hernandez', 'José Hernández'),
    'jose melchor romero': ('jose-melchor-romero', 'José Melchor Romero'),
    'la plata': ('la-plata', 'La Plata'),
    'lisandro olmos': ('lisandro-olmos', 'Lisandro Olmos'),
    'lomas de city bell': ('lomas-de-city-bell', 'Lomas de City Bell'),
    'los hornos': ('los-hornos', 'Los Hornos'),
    'manuel b gonnet': ('manuel-b-gonnet', 'Manuel B. Gonnet'),
    'ringuelet': ('ringuelet', 'Ringuelet'),
    'tolosa': ('tolosa', 'Tolosa'),
    'villa elisa': ('villa-elisa', 'Villa Elisa'),
    'villa elvira': ('villa-elvira', 'Villa Elvira'),
    'villa parque sicardi': ('villa-parque-sicardi', 'Villa Parque Sicardi'),
}
_ALIASES = {'casco urbano': 'la plata', 'gonnet': 'manuel b gonnet', 'gorina': 'joaquin gorina'}
_OPERATIONS: dict[TipoOperacion, str] = {
    'venta': 'en-venta', 'alquiler': 'en-alquiler',
    'alquiler_temp': 'en-alquiler-temporario',
}
_STATUS_OPERATIONS = {value: key for key, value in _OPERATIONS.items()}
_TYPE_SLUGS: dict[TipoPropiedad, tuple[str, ...]] = {
    'casa': ('casa', 'chalet', 'duplex', 'casa-quinta'),
    'departamento': ('departamento',),
    'ph': ('ph',),
    'local': ('local-comercial',),
    'oficina': ('oficina',),
    'terreno': ('terreno-lote', 'lote', 'campo'),
}
_TYPE_LABELS: dict[str, TipoPropiedad] = {
    'casas': 'casa', 'chalets': 'casa', 'duplexs': 'casa', 'casaquinta': 'casa',
    'departamentos': 'departamento', 'phs': 'ph', 'locales comerciales': 'local',
    'oficinas': 'oficina', 'terreno lote': 'terreno', 'lotes': 'terreno',
    'campos': 'terreno',
}


def _plain(value: object) -> str:
    text = unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode()
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', text.lower()).split())


def _number(value: object) -> float | None:
    match = re.search(r'\d[\d.,]*', str(value))
    if not match:
        return None
    number = match.group().rstrip('.,')
    if ',' in number:
        number = number.replace('.', '').replace(',', '.')
    elif '.' in number and all(len(part) == 3 for part in number.split('.')[1:]):
        number = number.replace('.', '')
    try:
        parsed = float(number)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _integer(node: Tag | None) -> int | None:
    number = _number(node.get_text(' ', strip=True)) if node else None
    return int(number) if number is not None else None


def resolve_location(zona: str) -> tuple[str, str] | None:
    for part in (_plain(piece) for piece in zona.split(',')):
        key = _ALIASES.get(part, part)
        if key in _LOCATIONS:
            return _LOCATIONS[key]
    return None


def search_params(filters: ScrapingFilters, area_slug: str) -> list[tuple[str, str]]:
    params = [
        ('location[]', 'la-plata'),
        ('areas[]', area_slug),
        ('status[]', _OPERATIONS.get(filters.tipo_operacion or 'venta', 'en-venta')),
    ]
    if len(filters.tipos_propiedad) == 1:
        params.extend(('type[]', slug) for slug in _TYPE_SLUGS.get(filters.tipos_propiedad[0], ()))
    return params


def parse_card(card: Tag, source: SearchSource) -> RawProperty | None:
    title_link = card.select_one('.item-title a')
    address_node = card.select_one('.item-address')
    status_node = card.select_one('.label-status')
    price_node = card.select_one('.item-price')
    type_node = card.select_one('.h-type')
    if not title_link or not address_node or not status_node:
        return None
    operation_slug = (status_node.get('href') or '').rstrip('/').rsplit('/', 1)[-1]
    operation = _STATUS_OPERATIONS.get(operation_slug)
    if not operation:
        return None
    price_text = price_node.get_text(' ', strip=True) if price_node else ''
    currency: Moneda = 'ARS' if price_text.startswith('$') else 'USD'
    try:
        images_payload = json.loads(str(card.get('data-images') or '[]'))
    except (TypeError, ValueError, json.JSONDecodeError):
        images_payload = []
    images = list(dict.fromkeys(
        str(image) for image in images_payload
        if isinstance(image, str) and image.startswith(('http://', 'https://'))
    )) if isinstance(images_payload, list) else []
    kind = _plain(type_node.get_text(' ', strip=True) if type_node else '')
    bedrooms = _integer(card.select_one('.h-beds .hz-figure'))
    return RawProperty(
        fuente=source.id,
        titulo=title_link.get_text(' ', strip=True) or None,
        direccion=address_node.get_text(' ', strip=True),
        precio=_number(price_text),
        moneda=currency,
        tipo_operacion=operation,
        tipo_propiedad=_TYPE_LABELS.get(kind, 'otro'),
        banos=_integer(card.select_one('.h-baths .hz-figure')),
        m2_total=_number(card.select_one('.h-area .hz-figure').get_text())
        if card.select_one('.h-area .hz-figure') else None,
        imagenes=images,
        url_origen=str(title_link.get('href') or '').strip() or None,
        raw={
            'source_id': source.id,
            'listing_id': str(card.get('data-hz-id') or '').removeprefix('hz-'),
            'dormitorios': bedrooms,
        },
    )


def parse_page(html: str, source: SearchSource) -> tuple[list[RawProperty], int]:
    soup = BeautifulSoup(html, 'html.parser')
    properties = [
        prop for card in soup.select('.item-listing-wrap')
        if isinstance(card, Tag) and (prop := parse_card(card, source)) is not None
    ]
    pages = [
        int(match.group(1)) for link in soup.select('a[href]')
        if (match := re.search(r'/page/(\d+)/', str(link.get('href') or '')))
    ]
    return properties, max(pages, default=1)


async def scrape_houzez(
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
    area_slug, _expected_location = resolved
    params = search_params(filters, area_slug)
    root = f'{source.base_url}/propiedades/'
    async with httpx.AsyncClient(
        timeout=settings.WEBSITE_HTTP_TIMEOUT, follow_redirects=True,
        headers={'User-Agent': _BROWSER_UA},
    ) as client:
        first_response = await client.get(root, params=params)
        first_response.raise_for_status()
        first, total_pages = parse_page(first_response.text, source)
        semaphore = asyncio.Semaphore(6)

        async def fetch(page: int) -> list[RawProperty]:
            async with semaphore:
                response = await client.get(f'{root}page/{page}/', params=params)
                response.raise_for_status()
                return parse_page(response.text, source)[0]

        rest = await asyncio.gather(*(fetch(page) for page in range(2, total_pages + 1)))

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
