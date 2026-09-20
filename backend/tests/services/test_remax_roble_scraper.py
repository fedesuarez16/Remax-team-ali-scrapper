"""RE/MAX Roble must use office UUIDs as well as the portal location filter."""
from __future__ import annotations

from unittest.mock import AsyncMock

import httpx

from app.models.property import ScrapingFilters
from app.services import apify
from app.services.apify import _scrape_remax_api
from app.services.source_registry import source_by_id, source_for_url


def item(listing_id: int, office_id: str, office_name: str = 'REMAX Roble') -> dict:
    return {
        'id': listing_id,
        'operation': {'id': 1, 'value': 'sale'},
        'currency': {'id': 1, 'value': 'USD'},
        'type': {'id': 9, 'value': 'casa'},
        'title': f'Casa {listing_id}',
        'slug': f'casa-city-bell-{listing_id}',
        'price': 150000,
        'displayAddress': f'Calle {listing_id}, City Bell',
        'geoLabel': 'City Bell, La Plata, Buenos Aires',
        'associate': {'officeId': office_id, 'officeName': office_name},
    }


def test_registry_matches_office_landing_page_but_not_portal_urls():
    assert source_for_url('https://www.remax.com.ar/roble').id == 'remaxroble'
    assert source_for_url('https://www.remax.com.ar/roble/equipo').id == 'remaxroble'
    assert source_for_url('https://www.remax.com.ar/listings/casa-123') is None


async def test_scraper_filters_each_reviewed_office_and_rejects_api_leaks(monkeypatch):
    source = source_by_id('remaxroble')
    first, second = source.office_ids
    requests: list[httpx.Request] = []

    async def resolve(_filters, zona):
        assert zona == 'City Bell'
        return 'in::::1066:::'

    monkeypatch.setattr(apify, '_resolve_remax_location', resolve)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        office_id = request.url.params['eq'].removeprefix('officeId:')
        payload = [item(1 if office_id == first else 2, office_id)]
        payload.append(item(99, 'another-office', 'REMAX Otra'))
        return httpx.Response(200, json={
            'data': {'data': payload, 'page': 0, 'totalPages': 1, 'totalItems': 2},
            'code': 200, 'message': '', 'errors': None,
        })

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
        transport=httpx.MockTransport(handler),
    ))
    progress = AsyncMock()
    properties = await _scrape_remax_api(
        ScrapingFilters(zona='City Bell', tipo_operacion='venta'),
        progress,
        source_id=source.id,
        office_ids=source.office_ids,
    )

    assert [prop.fuente for prop in properties] == ['remaxroble', 'remaxroble']
    assert all(prop.direccion.endswith('City Bell, La Plata, Buenos Aires') for prop in properties)
    assert [prop.raw['office_id'] for prop in properties] == [first, second]
    assert [request.url.params['eq'] for request in requests] == [
        f'officeId:{first}', f'officeId:{second}',
    ]
    assert all(request.url.params['locations'] == 'in::::1066:::' for request in requests)
    assert progress.call_args.args == ('remaxroble', 'done', 2)
