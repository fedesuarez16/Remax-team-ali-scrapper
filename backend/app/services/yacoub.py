"""Yacoub's public location catalogue, filtered HTML search and property details."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup  # type: ignore[import-untyped]

from app.models.property import RawProperty, ScrapingFilters
from app.services.source_filters import matches_source_filters
from app.services.source_registry import SearchSource
from app.services.tokko import (
    Progress, _LABEL_OPERATIONS, _LABEL_TYPES, _OPERATIONS, _TYPES,
    _number, _plain, _positive_number, _text, resolve_location,
)


def location_catalog(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, list) or not data:
        raise ValueError('Yacoub: no se reconoció el catálogo de ubicaciones.')
    rows = []
    for entry in data:
        if not isinstance(entry, dict) or not all(key in entry for key in ('i', 'n', 'p')):
            raise ValueError('Yacoub: cambió el formato de las ubicaciones.')
        rows.append({
            'location_id': entry['i'],
            'location_name': str(entry['n']).removesuffix(' y alrededores'),
            'parent_name': entry['p'],
        })
    for row in rows:
        parents = [r for r in rows if r['location_name'] == row['parent_name']
                   and r['location_id'] != row['location_id']
                   and r['parent_name'] != row['parent_name']]
        if len(parents) == 1:
            row['parent_id'] = parents[0]['location_id']
    return rows


def search_params(filters: ScrapingFilters, location: str | None, page: int) -> dict[str, str]:
    data: dict[str, Any] = {
        'current_localization_id': [int(location)] if location else 0,
        'current_localization_type': 'state' if location and int(location) < 1000 else 'division',
        # With currency=ANY the site's price pagination is inconsistent.
        # Preserve the search model's numeric price semantics in the final filter.
        'price_from': 0, 'price_to': 999999999,
        'operation_types': [int(_OPERATIONS[filters.tipo_operacion])]
        if filters.tipo_operacion else [1, 2, 3],
        'property_types': sorted({int(code) for kind in filters.tipos_propiedad
                                  for code in _TYPES.get(kind, '').split(',') if code})
        if filters.tipos_propiedad and all(t in _TYPES for t in filters.tipos_propiedad)
        else list(range(1, 26)),
        'currency': 'ANY', 'filters': [],
    }
    return {'data': json.dumps(data), 'limit': '20', 'order_by': 'id', 'order': 'DESC',
            'paginado': str(page)}


def parse_listing(
    html: str, source: SearchSource, page: int = 1,
) -> tuple[list[RawProperty], bool]:
    soup = BeautifulSoup(html, 'html.parser')
    listing = soup.select_one('#lista')
    if listing is None:
        raise ValueError('Yacoub: no se reconoció el listado de propiedades.')
    results = []
    for card in listing.select('a.card-propiedad[href]'):
        title = _text(card, '.yc-prop__tit')
        identity = re.fullmatch(r'(.+?) en (.+)', title)
        operation = _LABEL_OPERATIONS.get(_plain(_text(card, '.yc-prop__tipo')))
        url = urljoin(source.base_url, str(card['href']))
        if not identity or not operation or not re.fullmatch(r'/propiedad/[^/]+/\d+/',
                                                            urlsplit(url).path):
            raise ValueError('Yacoub: cambió el formato de las tarjetas.')
        if urlsplit(url).hostname != urlsplit(source.base_url).hostname:
            raise ValueError('Yacoub: la ficha apunta a otro sitio.')
        kind, locality = identity.groups()
        street = _text(card, '.yc-prop__dir')
        price = _text(card, 'p.precio')
        image = card.select_one('.cont-img img[src]')
        results.append(RawProperty(
            fuente=source.id, titulo=title, direccion=f'{street}, {locality}',
            tipo_propiedad=_LABEL_TYPES.get(_plain(kind), 'otro'), tipo_operacion=operation,
            precio=_positive_number(price), moneda='USD' if 'USD' in price else 'ARS',
            url_origen=url, imagenes=[str(image['src'])] if image else [],
            raw={'source_id': source.id, 'listing_id': urlsplit(url).path.strip('/').split('/')[-1]},
        ))
    # Both the previous and next arrows have class="next". Their target page
    # is the only reliable distinction, especially on the final page.
    targets = [parse_qs(urlsplit(str(a['href'])).query).get('paginado', [''])[0]
               for a in listing.select('.container-pagination a[href]')]
    has_next = any(p.isdigit() and int(p) > page for p in targets)
    if not results and not re.search(r'(?:0\s+propiedades|no se (?:han )?encontr|sin resultados)',
                                    listing.get_text(' ', strip=True), re.I):
        raise ValueError('Yacoub: el listado vacío no tiene el indicador esperado.')
    return results, has_next


def parse_detail(html: str, prop: RawProperty) -> RawProperty | None:
    soup = BeautifulSoup(html, 'html.parser')
    data = None
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            item = json.loads(script.get_text())
        except ValueError:
            continue
        if isinstance(item, dict) and item.get('@type') == 'RealEstateListing':
            data = item
            break
    if not data or not soup.select_one('.yc-ficha-h1'):
        raise ValueError('Yacoub: no se reconoció la ficha de la propiedad.')
    if urlsplit(str(data.get('url', ''))).path.rstrip('/') != urlsplit(
        str(prop.url_origen),
    ).path.rstrip('/'):
        raise ValueError('Yacoub: la ficha corresponde a otra propiedad.')
    offer = data.get('offers') or {}
    if offer.get('availability', '').rsplit('/', 1)[-1] in ('SoldOut', 'OutOfStock', 'Discontinued'):
        return None
    about = data.get('about') or {}
    address = about.get('address') or {}
    facts = [_plain(e.get_text(' ', strip=True)) for e in soup.select('.section-3 .datos .info')]

    def fact(label: str) -> float | None:
        return next((_number(t) for t in facts if t.endswith(label)), None)

    total = fact('totales')
    covered = fact('cubiertos')
    parking = fact('cocheras')
    raw = {**prop.raw, 'dormitorios': about.get('numberOfBedrooms')}
    geo = about.get('geo') or {}
    if geo.get('latitude') is not None and geo.get('longitude') is not None:
        raw.update(latitude=geo['latitude'], longitude=geo['longitude'])
    photos = [str(a['href']) for a in soup.select('#images_gallery a[data-fancybox][href]')]
    photos = photos or data.get('image') or prop.imagenes
    amenities: list[str] = []
    for section in soup.select('.section-3 .container-content'):
        if _plain(_text(section, 'h2')) in ('servicios', 'ambientes', 'adicionales'):
            amenities.extend(li.get_text(' ', strip=True) for li in section.select('li'))
    age = next((str(v.get('value', '')) for v in about.get('additionalProperty', [])
                if _plain(str(v.get('name', ''))) == 'antiguedad'), '')
    price = _positive_number(str(offer.get('price', '')))
    return prop.model_copy(update={
        'titulo': _text(soup, '.yc-ficha-h1'),
        'direccion': ', '.join(str(address[k]) for k in ('streetAddress', 'addressLocality')
                               if address.get(k)) or prop.direccion,
        'descripcion': _text(soup, '#ycDesc') or data.get('description'),
        'precio': price, 'moneda': offer.get('priceCurrency') or prop.moneda,
        'ambientes': about.get('numberOfRooms'), 'banos': about.get('numberOfBathroomsTotal'),
        'cocheras': int(parking) if parking is not None else None,
        'm2_total': total, 'm2_cubiertos': covered,
        'antiguedad': 0 if _plain(age) == 'a estrenar' else _number(age),
        'imagenes': list(dict.fromkeys(p for p in photos if p.startswith('https://'))),
        'amenities': amenities, 'raw': raw,
    })


async def scrape_yacoub(
    source: SearchSource, filters: ScrapingFilters, on_progress: Progress,
) -> list[RawProperty]:
    from app.core.config import settings
    from app.services.apify import _BROWSER_UA, _next_proxy_session, _proxy_with_session

    await on_progress(source.id, 'running', 0)
    results: list[RawProperty] = []
    async with httpx.AsyncClient(
        timeout=settings.WEBSITE_HTTP_TIMEOUT, follow_redirects=True,
        headers={'User-Agent': _BROWSER_UA},
        proxy=_proxy_with_session(settings.SCRAPER_PROXY_URL, _next_proxy_session(source.id)),
    ) as client:
        response = await client.get(f'{source.base_url}/wp-content/uploads/yacoub-publico/ubicaciones.json')
        response.raise_for_status()
        rows = location_catalog(response.json())
        zona = filters.localidades[0] if filters.localidades else (filters.zona or '')
        location = resolve_location(rows, zona) if zona else None
        if zona and location is None:
            await on_progress(source.id, 'done', 0)
            return []
        seen: set[str] = set()
        page = 1
        failures = 0
        semaphore = asyncio.Semaphore(3)
        card_filters = filters.model_copy(update={
            'ambientes_min': None, 'ambientes_max': None,
            'dormitorios_min': None, 'dormitorios_max': None,
            'm2_min': None, 'm2_max': None, 'barrio_aliases': [],
        })

        async def detail(prop: RawProperty) -> RawProperty | None:
            nonlocal failures
            try:
                async with semaphore:
                    response = await client.get(str(prop.url_origen))
                    response.raise_for_status()
                return parse_detail(response.text, prop)
            except (httpx.HTTPError, ValueError):
                failures += 1
                return None

        while True:
            response = await client.get(f'{source.base_url}/propiedades/',
                                        params=search_params(filters, location, page))
            response.raise_for_status()
            cards, has_next = parse_listing(response.text, source, page)
            fresh = [p for p in cards if str(p.url_origen) not in seen]
            if not fresh:
                break
            seen.update(str(p.url_origen) for p in fresh)
            candidates = [p for p in fresh if matches_source_filters(p, card_filters)]
            details = await asyncio.gather(*(detail(p) for p in candidates))
            results.extend(p for p in details if p and matches_source_filters(p, filters))
            await on_progress(source.id, 'running', len(results))
            if not has_next:
                break
            page += 1
        await on_progress(source.id, 'error' if failures else 'done', len(results))
        if failures and not results:
            raise ValueError(f'Yacoub: falló la lectura de {failures} fichas.')
    return results
