"""Prado's public InmoBúsqueda inventory, always scoped to agency 2134.

Its website is under construction and links this profile through its Linktree.
Only published card facts are used; missing rooms/areas cannot prove a match.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup  # type: ignore[import-untyped]

from app.models.property import RawProperty, ScrapingFilters
from app.services.source_filters import matches_source_filters
from app.services.source_registry import SearchSource
from app.services.tokko import Progress, _LABEL_OPERATIONS, _number, _plain, _text

_PROFILE = 'https://www.inmobusqueda.com/pradopropiedades'
_SEARCH = 'https://www.inmobusqueda.com/perfil/perfil.resultados.php'
_AGENCY_ID = '2134'
_TYPES = {
    'casa': 'casa', 'casa en country': 'casa', 'casa quinta': 'casa', 'duplex': 'casa',
    'triplex': 'casa', 'departamento': 'departamento', 'depto': 'departamento',
    'dpto': 'departamento', 'piso': 'departamento',
    'ph': 'ph', 'local': 'local', 'oficina': 'oficina', 'lote': 'terreno',
    'terreno': 'terreno', 'campo': 'terreno', 'fracciones': 'terreno',
}


def search_params(filters: ScrapingFilters) -> dict[str, str]:
    # Broad type families include several native codes (house, country house,
    # duplex...). Keep them together, then check the exact type locally.
    native_types = {'ph': '23', 'local': '5', 'oficina': '10'}
    kind = filters.tipos_propiedad[0] if len(filters.tipos_propiedad) == 1 else ''
    return {
        'eid': _AGENCY_ID, 'tipo': native_types.get(kind, '0'),
        'operacion': {'venta': '1', 'alquiler': '0', 'alquiler_temp': '2'}.get(
            filters.tipo_operacion or '', '99',
        ),
        'precio': '0', 'preciohasta': '0', 'moneda': '0',
        'dormitorios': '99', 'dormitorios2': '99',
        'antiguedad': '200', 'antiguedad2': '200', 'estado': '99', 'estado2': '99',
        'garage': '7', 'orden': '1',
    }


def parse_listing(html: str, source: SearchSource) -> tuple[list[RawProperty], int]:
    from app.services.apify import _full_size_image_url

    soup = BeautifulSoup(html, 'html.parser')
    cards = soup.select('.cajaPremium2017')
    if not cards:
        if re.search(r'\b0\s+propiedades\b|no\s+(?:hay|se encontraron)\s+propiedades',
                     soup.get_text(' ', strip=True), re.I):
            return [], 1
        raise ValueError('Prado: no se reconoció el listado de InmoBúsqueda.')
    results = []
    for card in cards:
        identity = re.fullmatch(r'(.+) en (.+)', _text(card, '.destacadoTipo'))
        link = card.select_one('a[href*="/ficha-"]')
        if not identity or not link:
            raise ValueError('Prado: cambió el formato de las tarjetas.')
        kind, operation = identity.groups()
        op = _LABEL_OPERATIONS.get(_plain(operation))
        if op is None:
            # A combined sale/rental card does not identify which price is shown.
            continue
        parsed = urlsplit(str(link['href']))
        listing = re.fullmatch(r'/ficha-(\d+)', parsed.path)
        if parsed.hostname != 'www.inmobusqueda.com.ar' or not listing:
            raise ValueError('Prado: la tarjeta no tiene una ficha válida.')
        street = _text(card, '.destacadoDireccion')
        locality = _text(card, '.destacadoLocalidad')
        if not locality:
            raise ValueError('Prado: la tarjeta no informa la ubicación.')
        price_text = _text(card, '.destacadoPrecio')
        facts = _text(card, '.destacadoDuenio')
        bedrooms = re.search(r'(\d+)\s*dormitorios?', facts, re.I)
        rooms = re.search(r'(\d+)\s*ambientes?', facts, re.I)
        area = re.search(r'([\d.,]+)\s*m\s*[²2]\s*\(Sup\. Construida\)', facts, re.I)
        photo = card.select_one('img.FotoBox[src]')
        image = str(photo['src']) if photo else ''
        # Profile results belong to the pinned agency. The image path gives
        # an independent check for an unexpected unscoped response.
        if image and 'sinfotos' not in image and f'/{_AGENCY_ID}/' not in urlsplit(image).path:
            raise ValueError('Prado: el listado contiene otra inmobiliaria.')
        results.append(RawProperty(
            fuente=source.id, titulo=f'{kind} en {operation} — {street}',
            direccion=f'{street}, {locality}', descripcion=_text(card, '.destacadoDescripcion'),
            tipo_propiedad=_TYPES.get(_plain(kind), 'otro'),  # type: ignore[arg-type]
            tipo_operacion=op, precio=_number(price_text),
            moneda='ARS' if price_text.strip().startswith('$') else 'USD',
            ambientes=int(rooms.group(1)) if rooms else None,
            m2_total=_number(area.group(1)) if area else None,
            imagenes=[_full_size_image_url(image)] if image and 'sinfotos' not in image else [],
            url_origen=f'https://www.inmobusqueda.com.ar/ficha-{listing.group(1)}',
            raw={'source_id': source.id, 'agency_id': _AGENCY_ID, 'listing_id': listing.group(1),
                 'dormitorios': int(bedrooms.group(1)) if bedrooms else None},
        ))
    pages = [int(m.group(1)) for a in soup.select('a[href]')
             if (m := re.search(r'buscar\((\d+),', str(a.get('onclick') or a['href'])))]
    return results, max(pages, default=1)


def _response_html(response: httpx.Response) -> str:
    # The profile declares UTF-8 even when its actual bytes are ISO-8859-1.
    try:
        return response.content.decode('utf-8')
    except UnicodeDecodeError:
        return response.content.decode('latin-1')


async def scrape_prado(
    source: SearchSource, filters: ScrapingFilters, on_progress: Progress,
) -> list[RawProperty]:
    from app.core.config import settings
    from app.services.apify import (
        _BROWSER_UA, _check_inmobusqueda_access, _next_proxy_session, _proxy_with_session,
    )

    await on_progress(source.id, 'running', 0)
    results: list[RawProperty] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(
        timeout=settings.WEBSITE_HTTP_TIMEOUT, follow_redirects=True,
        headers={'User-Agent': _BROWSER_UA, 'Referer': _PROFILE},
        proxy=_proxy_with_session(settings.SCRAPER_PROXY_URL, _next_proxy_session(source.id)),
    ) as client:
        response = await client.get(_PROFILE)
        response.raise_for_status()
        _check_inmobusqueda_access(response)
        profile = BeautifulSoup(_response_html(response), 'html.parser')
        identity: Any = profile.select_one('#elbuscador input[name="eid"]')
        if not identity or identity.get('value') != _AGENCY_ID:
            raise ValueError('Prado: no se pudo verificar el perfil de la inmobiliaria.')
        page = 1
        while True:
            response = await client.post(_SEARCH, params={'e': '0', 'pagina': str(page)},
                                         data=search_params(filters))
            response.raise_for_status()
            _check_inmobusqueda_access(response)
            cards, last_page = parse_listing(_response_html(response), source)
            fresh = [p for p in cards if str(p.url_origen) not in seen]
            if cards and not fresh:
                break
            seen.update(str(p.url_origen) for p in fresh)
            results.extend(p for p in fresh if matches_source_filters(p, filters))
            await on_progress(source.id, 'running', len(results))
            if page >= last_page:
                break
            page += 1
    await on_progress(source.id, 'done', len(results))
    return results
