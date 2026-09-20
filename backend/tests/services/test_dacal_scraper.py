"""Dacal's website API must stay scoped to its exact location identifiers."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx

from app.models.property import ScrapingFilters
from app.services.dacal import parse_property, resolve_location, scrape_dacal, search_body
from app.services.source_registry import source_by_id, source_for_url

COUNTRIES = [{'id': 1, 'nombre': 'Argentina'}]
PROVINCES = [
    {'id': 147, 'id_pais': 1, 'nombre': 'Buenos Aires'},
    {'id': 155, 'id_pais': 1, 'nombre': 'Cordoba'},
]
LOCALITIES = [
    {'id': 26499, 'id_provincia': 147, 'nombre': 'La Plata'},
    {'id': 30000, 'id_provincia': 155, 'nombre': 'Otra localidad'},
]
NEIGHBORHOODS = [
    {'id': 26514, 'id_localidad': 26499, 'nombre': 'City Bell'},
    {'id': 26520, 'id_localidad': 26499, 'nombre': 'La Plata'},
    {'id': 26499, 'id_localidad': 26499, 'nombre': 'La Plata'},
    {'id': 26532, 'id_localidad': 26499, 'nombre': 'Manuel B Gonnet'},
    {'id': 26503, 'id_localidad': 26499, 'nombre': 'Grand Bell'},
]


def property_item(listing_id: int = 1, *, barrio='City Bell', barrio_id=26514,
                  operation='Venta') -> dict:
    return {
        'id': listing_id, 'idd': 59000 + listing_id,
        'url': f'casa-venta-la-plata-ver-{listing_id}', 'titulo': 'Casa de prueba',
        'descripcion': 'Casa con <b>jardín</b>.', 'nom_tipo': 'Casa', 'mostrar_precio': 1,
        'propiedades': {
            'baños': '2', 'antiguedad': 'A estrenar', 'cocheras': '1',
            'ambientes': '4', 'dormitorios': '3', 'gastos': {'expensas': '50.000'},
        },
        'superficies': {
            'superficie cubierta': '140 mts', 'superficie terreno': '300 mts',
            'Superficie total construido': '180 mts',
        },
        'ubicacion': {
            'direccion': 'Calle 10 123', 'latitud': '-34.8', 'longitud': '-58.0',
            'localidad': {'id': 26499, 'nombre': 'La Plata'},
            'barrio': {'id': barrio_id, 'nombre': barrio},
        },
        'imagenes': [
            {'image': 'https://static.tokkobroker.com/pictures/a.jpg',
             'original': 'https://static.tokkobroker.com/original_pictures/a.jpg'},
            {'image': 'https://static.tokkobroker.com/pictures/b.jpg',
             'original': 'https://static.tokkobroker.com/original_pictures/b.jpg'},
        ],
        'caracteristicas': [{'nombre': 'Parrilla'}, {'nombre': 'Pileta'}],
        'operaciones': [{'id': '1', 'nombre': operation, 'moneda': 'USD', 'importe': '180000'}],
    }


def test_registry_recognizes_only_dacal_own_host():
    assert source_for_url('https://www.dacalbienesraices.com.ar/properties/buy').id == 'dacalbr'
    assert source_for_url('https://dacalbienesraices.com.ar.evil.test/whatever') is None


def test_location_resolution_uses_neighborhood_and_parent_ids():
    assert resolve_location(
        COUNTRIES, PROVINCES, LOCALITIES, NEIGHBORHOODS, 'City Bell, La Plata',
    ) == {'barrio': 26514, 'localidad': 26499, 'provincia': 147, 'pais': 1}
    assert resolve_location(
        COUNTRIES, PROVINCES, LOCALITIES, NEIGHBORHOODS, 'Gonnet',
    )['barrio'] == 26532
    assert resolve_location(
        COUNTRIES, PROVINCES, LOCALITIES, NEIGHBORHOODS, 'La Plata',
    )['barrio'] == 26520
    assert resolve_location(
        COUNTRIES, PROVINCES, LOCALITIES, NEIGHBORHOODS, 'Grand Bell, City Bell, La Plata',
    )['barrio'] == 26503
    assert resolve_location(
        COUNTRIES, PROVINCES, LOCALITIES, NEIGHBORHOODS, 'City Bell, Cordoba',
    ) is None


def test_search_body_sends_native_operation_type_and_location():
    location = {'barrio': 26514, 'localidad': 26499, 'provincia': 147, 'pais': 1}
    assert search_body(ScrapingFilters(
        tipo_operacion='venta', tipos_propiedad=['departamento'], precio_max=200000,
    ), location, 100) == {
        'offset': 100, 'limit': 100, 'ubicacion': [location],
        'operaciones': 1, 'tipo_propiedad': 2,
    }


def test_property_parser_uses_original_images_and_structured_fields():
    prop = parse_property(property_item(), source_by_id('dacalbr'), 'venta')
    assert prop is not None
    assert prop.direccion == 'Calle 10 123, City Bell, La Plata'
    assert (prop.ambientes, prop.banos, prop.cocheras, prop.raw['dormitorios']) == (4, 2, 1, 3)
    assert (prop.precio, prop.expensas, prop.m2_cubiertos, prop.m2_total) == (
        180000, 50000, 140, 180,
    )
    assert prop.antiguedad == 0
    assert prop.imagenes[0].startswith('https://static.tokkobroker.com/original_pictures/')
    assert prop.amenities == ['Parrilla', 'Pileta']


def mock_client(monkeypatch, handler):
    original = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
        transport=httpx.MockTransport(handle), follow_redirects=True,
    ))
    return requests


async def test_scraper_pages_api_and_rejects_a_wrong_location(monkeypatch):
    catalogs = {
        '/api/v1/ubicacion/paises': COUNTRIES,
        '/api/v1/ubicacion/provincias': PROVINCES,
        '/api/v1/ubicacion/localidades': LOCALITIES,
        '/api/v1/ubicacion/barrios': NEIGHBORHOODS,
    }

    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json=catalogs[request.url.path])
        body = json.loads(request.content)
        assert body['ubicacion'][0]['barrio'] == 26514
        if body['offset'] == 0:
            return httpx.Response(200, json={
                'count': 3,
                'propiedades': [property_item(1), property_item(2, barrio='Gonnet', barrio_id=26532)],
            })
        return httpx.Response(200, json={'count': 3, 'propiedades': [property_item(3)]})

    requests = mock_client(monkeypatch, handler)
    progress = AsyncMock()
    props = await scrape_dacal(
        source_by_id('dacalbr'),
        ScrapingFilters(zona='City Bell', tipo_operacion='venta', tipos_propiedad=['casa']),
        progress,
    )
    assert [prop.raw['listing_id'] for prop in props] == ['1', '3']
    posts = [request for request in requests if request.method == 'POST']
    assert [json.loads(request.content)['offset'] for request in posts] == [0, 2]
    assert progress.call_args.args == ('dacalbr', 'done', 2)


async def test_unknown_location_never_runs_an_unscoped_search(monkeypatch):
    catalogs = {
        '/api/v1/ubicacion/paises': COUNTRIES,
        '/api/v1/ubicacion/provincias': PROVINCES,
        '/api/v1/ubicacion/localidades': LOCALITIES,
        '/api/v1/ubicacion/barrios': NEIGHBORHOODS,
    }
    requests = mock_client(monkeypatch, lambda request: httpx.Response(
        200, json=catalogs[request.url.path],
    ))
    assert await scrape_dacal(
        source_by_id('dacalbr'), ScrapingFilters(zona='Inventada'), AsyncMock(),
    ) == []
    assert all(request.method == 'GET' for request in requests)
