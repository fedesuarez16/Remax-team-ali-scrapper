"""Dacal Bienes Raíces' public JSON API, as used by its own website."""
from __future__ import annotations

import asyncio
import re
import unicodedata
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import httpx
from bs4 import BeautifulSoup  # type: ignore[import-untyped]

from app.models.property import Moneda, RawProperty, ScrapingFilters, TipoOperacion, TipoPropiedad
from app.services.source_filters import matches_source_filters
from app.services.source_registry import SearchSource

Progress = Callable[[str, str, int], Awaitable[None]]
_API = 'https://api.dacalbienesraices.com.ar/api/v1'
_PAGE_SIZE = 100
_OPERATIONS = {'venta': 1, 'alquiler': 2, 'alquiler_temp': 3}
_OPERATION_LABELS: dict[str, TipoOperacion] = {
    'venta': 'venta', 'alquiler': 'alquiler', 'alquiler temporario': 'alquiler_temp',
}
_TYPE_IDS: dict[TipoPropiedad, int] = {
    'departamento': 2, 'local': 7, 'oficina': 5,
}
_TYPE_LABELS: dict[str, TipoPropiedad] = {
    'departamento': 'departamento', 'casa': 'casa', 'quinta': 'casa',
    'local': 'local', 'oficina': 'oficina', 'terreno': 'terreno', 'campo': 'terreno',
}


def _plain(value: object) -> str:
    text = unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode()
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', text.lower()).split())


def _number(value: object) -> float | None:
    match = re.search(r'-?\d[\d.,]*', str(value))
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


def _positive_number(value: object) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _positive_int(value: object) -> int | None:
    number = _positive_number(value)
    return int(number) if number is not None else None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mapping_list(value: object) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def resolve_location(
    countries: Sequence[Mapping[str, Any]], provinces: Sequence[Mapping[str, Any]],
    localities: Sequence[Mapping[str, Any]], neighborhoods: Sequence[Mapping[str, Any]],
    zona: str,
) -> dict[str, int] | None:
    aliases = {'gonnet': 'manuel b gonnet', 'casco urbano': 'la plata'}
    parts = [aliases.get(_plain(part), _plain(part)) for part in zona.split(',') if _plain(part)]
    if not parts:
        return None
    country_by_id = {row.get('id'): row for row in countries}
    province_by_id = {row.get('id'): row for row in provinces}
    locality_by_id = {row.get('id'): row for row in localities}
    exact: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    compatible: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for neighborhood in neighborhoods:
        if _plain(neighborhood.get('nombre', '')) != parts[0]:
            continue
        locality = locality_by_id.get(neighborhood.get('id_localidad'))
        if not locality:
            continue
        province = province_by_id.get(locality.get('id_provincia'), {})
        country = country_by_id.get(province.get('id_pais'), {})
        lineage = {
            _plain(locality.get('nombre', '')), _plain(province.get('nombre', '')),
            _plain(country.get('nombre', '')),
        }
        # Some API neighborhoods (for example Grand Bell) do not expose their
        # intermediate City Bell parent. A globally unique exact neighborhood
        # id is still precise; ancestry only disambiguates repeated names.
        pair = (neighborhood, locality)
        if all(part in lineage for part in parts[1:]):
            exact.append(pair)
        elif parts[1:] and any(part in lineage for part in parts[1:]):
            compatible.append(pair)
    candidates = exact or compatible
    if len(candidates) > 1:
        # La Plata appears both as the partido pseudo-neighborhood (26499) and
        # the actual city (26520). Prefer the proper child, never the broad id.
        children = [pair for pair in candidates if pair[0].get('id') != pair[0].get('id_localidad')]
        candidates = children or candidates
    if len(candidates) != 1:
        return None
    neighborhood, locality = candidates[0]
    matched_province = province_by_id.get(locality.get('id_provincia'))
    matched_country = country_by_id.get(matched_province.get('id_pais')) if matched_province else None
    if not matched_province or not matched_country:
        return None
    return {
        'barrio': int(neighborhood['id']), 'localidad': int(locality['id']),
        'provincia': int(matched_province['id']), 'pais': int(matched_country['id']),
    }


def search_body(filters: ScrapingFilters, location: dict[str, int], offset: int) -> dict[str, Any]:
    body: dict[str, Any] = {'offset': offset, 'limit': _PAGE_SIZE, 'ubicacion': [location]}
    if filters.tipo_operacion:
        body['operaciones'] = _OPERATIONS[filters.tipo_operacion]
    if len(filters.tipos_propiedad) == 1 and filters.tipos_propiedad[0] in _TYPE_IDS:
        body['tipo_propiedad'] = _TYPE_IDS[filters.tipos_propiedad[0]]
    return body


def parse_property(data: Mapping[str, Any], source: SearchSource,
                   requested_operation: TipoOperacion | None = None) -> RawProperty | None:
    location = _mapping(data.get('ubicacion'))
    neighborhood = _mapping(location.get('barrio'))
    locality = _mapping(location.get('localidad'))
    place_names = [str(neighborhood.get('nombre') or ''), str(locality.get('nombre') or '')]
    place = ', '.join(dict.fromkeys(name for name in place_names if name))
    street = str(location.get('direccion') or '').strip()
    if not place:
        return None

    operations = _mapping_list(data.get('operaciones'))
    selected: Mapping[str, Any] | None = None
    for operation in operations:
        normalized = _OPERATION_LABELS.get(_plain(operation.get('nombre', '')))
        if normalized == requested_operation:
            selected = operation
            break
        if selected is None and normalized:
            selected = operation
    if not selected:
        return None
    operation_type = _OPERATION_LABELS.get(_plain(selected.get('nombre', '')))
    if not operation_type:
        return None

    details = _mapping(data.get('propiedades'))
    surfaces = _mapping(data.get('superficies'))
    type_name = _plain(data.get('nom_tipo', ''))
    property_type = _TYPE_LABELS.get(type_name, 'otro')
    terrain = _positive_number(surfaces.get('superficie terreno'))
    built = _positive_number(surfaces.get('Superficie total construido'))
    covered = _positive_number(surfaces.get('superficie cubierta'))
    age_text = details.get('antiguedad', '')
    age = 0 if _plain(age_text) == 'a estrenar' else _positive_number(age_text)
    expenses = _mapping(details.get('gastos'))
    images = _mapping_list(data.get('imagenes'))
    photos = [
        str(image.get('original') or image.get('image') or '')
        for image in images if not image.get('is_blueprint')
    ]
    features = _mapping_list(data.get('caracteristicas'))
    amenities = [str(item.get('nombre')) for item in features
                 if item.get('nombre')]
    description = BeautifulSoup(str(data.get('descripcion') or ''), 'html.parser').get_text(
        ' ', strip=True,
    )
    shown_price = bool(data.get('mostrar_precio'))
    currency: Moneda = 'ARS' if _plain(selected.get('moneda', '')) == 'ars' else 'USD'
    bedrooms = _positive_int(details.get('dormitorios'))
    slug = str(data.get('url') or '').strip('/')
    detail_id = data.get('idd')
    url = f'{source.base_url}/properties/{slug}-{detail_id}' if slug and detail_id else None
    return RawProperty(
        fuente=source.id, titulo=str(data.get('titulo') or '') or None,
        descripcion=description or None,
        direccion=f'{street}, {place}' if street else place,
        precio=_positive_number(selected.get('importe')) if shown_price else None,
        moneda=currency, tipo_operacion=operation_type, tipo_propiedad=property_type,
        ambientes=_positive_int(details.get('ambientes')),
        banos=_positive_int(details.get('baños')), cocheras=_positive_int(details.get('cocheras')),
        expensas=_positive_number(expenses.get('expensas')),
        m2_total=terrain if property_type == 'terreno' else (built or terrain),
        m2_cubiertos=covered, antiguedad=int(age) if age is not None else None,
        amenities=amenities, imagenes=list(dict.fromkeys(photo for photo in photos if photo)),
        url_origen=url,
        raw={
            'source_id': source.id, 'listing_id': str(data.get('id') or ''),
            'dormitorios': bedrooms, 'latitude': location.get('latitud'),
            'longitude': location.get('longitud'),
        },
    )


async def scrape_dacal(
    source: SearchSource, filters: ScrapingFilters, on_progress: Progress,
) -> list[RawProperty]:
    from app.core.config import settings
    from app.services.apify import _BROWSER_UA

    await on_progress(source.id, 'running', 0)
    async with httpx.AsyncClient(
        timeout=settings.WEBSITE_HTTP_TIMEOUT, follow_redirects=True,
        headers={'User-Agent': _BROWSER_UA, 'Accept': 'application/json'},
    ) as client:
        endpoints = ('paises', 'provincias', 'localidades', 'barrios')
        responses = await asyncio.gather(*(
            client.get(f'{_API}/ubicacion/{endpoint}') for endpoint in endpoints
        ))
        for response in responses:
            response.raise_for_status()
        catalogs = [response.json() for response in responses]
        if not all(isinstance(catalog, list) for catalog in catalogs):
            raise ValueError(f'{source.name}: la API cambió el formato de ubicaciones.')
        countries, provinces, localities, neighborhoods = (
            _mapping_list(catalog) for catalog in catalogs
        )
        zona = filters.localidades[0] if filters.localidades else (filters.zona or '')
        location = resolve_location(
            countries, provinces, localities, neighborhoods, zona,
        ) if zona else None
        if not location:
            await on_progress(source.id, 'done', 0)
            return []

        results: list[RawProperty] = []
        seen: set[str] = set()
        offset = 0
        total: int | None = None
        while total is None or offset < total:
            response = await client.post(
                f'{_API}/properties/search', json=search_body(filters, location, offset),
                headers={'Origin': source.base_url},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, Mapping) or not isinstance(payload.get('propiedades'), list):
                raise ValueError(f'{source.name}: la API cambió el formato de resultados.')
            total = int(payload.get('count') or 0)
            page = payload['propiedades']
            if not page:
                break
            for item in page:
                if not isinstance(item, Mapping):
                    continue
                prop = parse_property(item, source, filters.tipo_operacion)
                if not prop or not prop.url_origen or prop.url_origen in seen:
                    continue
                seen.add(prop.url_origen)
                if matches_source_filters(prop, filters):
                    results.append(prop)
            await on_progress(source.id, 'running', len(results))
            offset += len(page)
        await on_progress(source.id, 'done', len(results))
        return results
