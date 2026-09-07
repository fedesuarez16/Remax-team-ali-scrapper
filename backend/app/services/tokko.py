"""Tokko's public website search, reviewed for Mauro Perri and Urquiza.

Uses /Buscar and its p=N HTML fragments, then each /p/ detail page. No API
credentials, browser, or LLM. Selectors are deliberately limited to the two
registered sites; a different Tokko template requires its own review.
"""
from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup  # type: ignore[import-untyped]

from app.models.property import RawProperty, ScrapingFilters, TipoPropiedad
from app.services.source_filters import matches_source_filters
from app.services.source_registry import SearchSource

Progress = Callable[[str, str, int], Awaitable[None]]
_OPERATIONS = {'venta': '1', 'alquiler': '2', 'alquiler_temp': '3'}
_TYPES = {
    'terreno': '1,9', 'departamento': '2', 'casa': '3,4', 'oficina': '5',
    'local': '7', 'ph': '13',
}
_LABEL_TYPES: dict[str, TipoPropiedad] = {
    'terreno': 'terreno', 'campo': 'terreno', 'departamento': 'departamento',
    'casa': 'casa', 'quinta': 'casa', 'oficina': 'oficina', 'local': 'local', 'ph': 'ph',
}


def _plain(value: str) -> str:
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode()
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', value.lower()).split())


def _number(value: str) -> float | None:
    match = re.search(r'\d[\d.,]*', value)
    if not match:
        return None
    number = match.group().rstrip('.,')
    if ',' in number:
        number = number.replace('.', '').replace(',', '.')
    elif '.' in number and all(len(part) == 3 for part in number.split('.')[1:]):
        number = number.replace('.', '')
    try:
        return float(number)
    except ValueError:
        return None


def _text(soup: Any, selector: str) -> str:
    element = soup.select_one(selector)
    return str(element.get_text(' ', strip=True)) if element else ''


def location_catalog(html: str) -> list[dict[str, Any]]:
    match = re.search(r'\b(?:var\s+)?locations_response\s*=\s*', html)
    if not match:
        raise ValueError('El sitio no devolvió el catálogo de ubicaciones esperado.')
    try:
        data, _ = json.JSONDecoder().raw_decode(html[match.end():])
    except ValueError as exc:
        raise ValueError('No se pudo leer el catálogo de ubicaciones del sitio.') from exc
    if not isinstance(data, list):
        raise ValueError('El catálogo de ubicaciones cambió de formato.')
    return [row for row in data if isinstance(row, dict)]


def resolve_location(rows: list[dict[str, Any]], zona: str) -> str | None:
    aliases = {'gonnet': 'manuel b gonnet', 'casco urbano': 'la plata'}
    parts = [aliases.get(_plain(p), _plain(p)) for p in zona.split(',') if _plain(p)]
    parts = list(dict.fromkeys(parts))
    if not parts:
        return None
    # Include parents only when they actually appear in this site's catalogue.
    indexed = {str(row['location_id']): row for row in rows if row.get('location_id')}
    candidates = []
    for key, row in indexed.items():
        if _plain(str(row.get('location_name', ''))) != parts[0]:
            continue
        lineage = [_plain(str(row.get('parent_name', '')))]
        parent = str(row.get('parent_id', ''))
        visited = {key}
        while parent in indexed and parent not in visited:
            visited.add(parent)
            ancestor = indexed[parent]
            lineage.append(_plain(str(ancestor.get('parent_name', ''))))
            parent = str(ancestor.get('parent_id', ''))
        if all(part in lineage for part in parts[1:]):
            candidates.append(row)
    # La Plata is both partido and city. Prefer the city when both exist.
    cities = [r for r in candidates if _plain(str(r.get('parent_name', ''))) == parts[0]]
    candidates = cities or candidates
    return str(candidates[0]['location_id']) if len(candidates) == 1 else None


def search_params(filters: ScrapingFilters, location: str | None) -> dict[str, str]:
    params = {'o': '2,2'}
    if filters.tipo_operacion:
        params['operation'] = _OPERATIONS[filters.tipo_operacion]
    if filters.tipos_propiedad and all(t in _TYPES for t in filters.tipos_propiedad):
        params['ptypes'] = ','.join(_TYPES[t] for t in filters.tipos_propiedad)
    if location:
        params['locations'] = location
    # The current search model has no currency field. Do not invent a currency
    # constraint or send bedrooms to Tokko's separate rooms filter.
    return params


def parse_listing(html: str, source: SearchSource) -> list[RawProperty]:
    soup = BeautifulSoup(html, 'html.parser')
    cards = soup.select('li[prop-id]')
    if not cards:
        visible = soup.get_text(' ', strip=True)
        if 'No hubo resultados para su búsqueda' in visible or html.strip() == '--NoMoreProperties--':
            return []
        raise ValueError(f'{source.name}: no se reconoció el listado de propiedades.')
    results = []
    for card in cards:
        identity = _text(card, '.prop-desc-tipo-ub')
        match = re.match(r'(.+?)\s+en\s+(Venta|Alquiler temporario|Alquiler)\s+en\s+(.+)',
                         identity, re.I)
        link = card.select_one('a[href^="/p/"]')
        if not match or not link:
            raise ValueError(f'{source.name}: cambió el formato de una tarjeta.')
        kind, operation, location = match.groups()
        price_box = card.select_one('.prop-valor-nro')
        price_text = ' '.join(str(t) for t in price_box.find_all(string=True, recursive=False)) \
            if price_box else ''
        currency = 'USD' if re.search(r'USD|U\$S|US\$', price_text, re.I) else 'ARS'
        image = card.select_one('img.dest-img')
        photo = str(image.get('src') or '') if image else ''
        street = _text(card, '.prop-desc-dir')
        prop = RawProperty(
            fuente=source.id, titulo=f'{identity} — {street}',
            direccion=f'{street}, {location}' if street else location,
            tipo_propiedad=_LABEL_TYPES.get(_plain(kind), 'otro'),
            tipo_operacion={'venta': 'venta', 'alquiler': 'alquiler',
                            'alquiler temporario': 'alquiler_temp'}[operation.lower()],
            precio=_number(price_text), moneda=currency,
            url_origen=urljoin(source.base_url, str(link['href'])),
            imagenes=[photo] if photo.startswith('https://') else [],
            raw={'source_id': source.id, 'listing_id': str(card['prop-id'])},
        )
        results.append(prop)
    return results


def parse_detail(html: str, prop: RawProperty) -> RawProperty:
    soup = BeautifulSoup(html, 'html.parser')
    if not soup.select_one('#ficha_desc'):
        raise ValueError('La ficha no contiene los datos de la propiedad esperados.')
    values = {}
    for item in soup.select('#lista_informacion_basica li, #lista_superficies li'):
        label, separator, value = item.get_text(' ', strip=True).partition(':')
        if separator:
            values[_plain(label)] = value.strip()
    update: dict[str, Any] = {}
    for label, field in [('ambientes', 'ambientes'), ('banos', 'banos'), ('cocheras', 'cocheras')]:
        value = _number(values.get(label, ''))
        update[field] = int(value) if value is not None else None
    raw = dict(prop.raw)
    bedrooms = _number(values.get('dormitorios', ''))
    raw['dormitorios'] = int(bedrooms) if bedrooms is not None else None
    update['raw'] = raw
    update['m2_cubiertos'] = _number(values.get('cubierta', ''))
    update['m2_total'] = _number(values.get('total construido', ''))
    if prop.tipo_propiedad == 'terreno':
        update['m2_total'] = _number(values.get('terreno', ''))
    age = values.get('antiguedad', '')
    update['antiguedad'] = 0 if _plain(age) == 'a estrenar' else _number(age)
    # Tokko escapes the description's HTML and unescapes it in the browser.
    description = _text(soup, '#prop-desc')
    update['descripcion'] = BeautifulSoup(description, 'html.parser').get_text(' ', strip=True)
    photos = [str(image.get('src') or '') for image in soup.select('img.zoomImg')]
    update['imagenes'] = list(dict.fromkeys(p for p in photos if p.startswith('https://')))[:20] \
        or prop.imagenes
    update['amenities'] = [item.get_text(' ', strip=True)
                           for item in soup.select('#ficha_servicios li, #ficha_ambientes li')]
    return prop.model_copy(update=update)


async def scrape_tokko(
    source: SearchSource, filters: ScrapingFilters, on_progress: Progress,
) -> list[RawProperty]:
    from app.core.config import settings
    from app.services.apify import _BROWSER_UA, _next_proxy_session, _proxy_with_session

    await on_progress(source.id, 'running', 0)
    results: list[RawProperty] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(
        timeout=settings.WEBSITE_HTTP_TIMEOUT, follow_redirects=True,
        headers={'User-Agent': _BROWSER_UA, 'Accept-Language': 'es-AR,es;q=0.9'},
        proxy=_proxy_with_session(settings.SCRAPER_PROXY_URL, _next_proxy_session(source.id)),
    ) as client:
        base = f'{source.base_url}/Buscar'
        catalog_response = await client.get(base)
        catalog_response.raise_for_status()
        rows = location_catalog(catalog_response.text)
        zona = filters.localidades[0] if filters.localidades else (filters.zona or '')
        location = resolve_location(rows, zona) if zona else None
        if zona and location is None:
            await on_progress(source.id, 'done', 0)
            return []
        params = search_params(filters, location)
        page = 1
        semaphore = asyncio.Semaphore(3)
        detail_failures = 0

        async def detail(prop: RawProperty) -> RawProperty | None:
            nonlocal detail_failures
            try:
                async with semaphore:
                    response = await client.get(str(prop.url_origen))
                    response.raise_for_status()
                if urlsplit(str(response.url)).path != urlsplit(str(prop.url_origen)).path:
                    raise ValueError('La ficha redirigió a otra página.')
                return parse_detail(response.text, prop)
            except (httpx.HTTPError, ValueError):
                detail_failures += 1
                return None

        while settings.TOKKO_MAX_PAGES <= 0 or page <= settings.TOKKO_MAX_PAGES:
            response = await client.get(base, params={**params, 'p': str(page)})
            response.raise_for_status()
            cards = parse_listing(response.text, source)
            new_cards = [p for p in cards if str(p.url_origen) not in seen]
            if not new_cards:
                break
            seen.update(str(p.url_origen) for p in new_cards)
            # Card fields suffice for operation/type/price/zona rejection. Only
            # the detail proves rooms, bedrooms and areas; defer those checks.
            card_filters = filters.model_copy(update={
                'ambientes_min': None, 'ambientes_max': None,
                'dormitorios_min': None, 'dormitorios_max': None,
                'm2_min': None, 'm2_max': None, 'barrio_aliases': [],
            })
            candidates = [p for p in new_cards if matches_source_filters(p, card_filters)]
            details = await asyncio.gather(*(detail(p) for p in candidates))
            results.extend(p for p in details if p and matches_source_filters(p, filters))
            await on_progress(source.id, 'running', len(results))
            page += 1
        if detail_failures:
            await on_progress(source.id, 'error', len(results))
            if not results:
                raise ValueError(f'{source.name}: falló la lectura de {detail_failures} fichas.')
        else:
            await on_progress(source.id, 'done', len(results))
    return results
