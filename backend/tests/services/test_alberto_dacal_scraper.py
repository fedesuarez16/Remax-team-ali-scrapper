"""Alberto Dacal searches must keep the locality encoded in the native route."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
from bs4 import BeautifulSoup

from app.models.property import ScrapingFilters
from app.services.alberto_dacal import parse_card, parse_page, resolve_location, scrape_alberto_dacal, search_url
from app.services.source_registry import source_by_id, source_for_url


def listing_card(
    listing_id: int = 10, *, locality: str = 'City Bell', operation: str = 'Sell',
    card_type: str = 'Casa', bedrooms: int = 3,
) -> str:
    payload = {
        '@context': 'https://schema.org',
        '@type': 'RealEstateListing',
        'name': f'Casa {listing_id}',
        'url': f'https://dacal.com.ar/propiedad/casa-{listing_id}--{listing_id}',
        'image': (
            f'https://d1v2p1s05qqabi.cloudfront.net/{listing_id}/conversions/'
            f'{listing_id}00-thumbnail.webp'
        ),
        'about': {
            '@type': 'House',
            'address': {
                '@type': 'PostalAddress', 'streetAddress': 'Calle 10 123',
                'addressLocality': locality,
            },
            'geo': {'latitude': -34.8, 'longitude': -58.0},
            'floorSize': {'value': 180, 'unitCode': 'MTK'},
            'numberOfRooms': 4,
            'numberOfBedrooms': bedrooms,
            'numberOfBathroomsTotal': 2,
        },
        'offers': {
            'businessFunction': f'http://purl.org/goodrelations/v1#{operation}',
            'price': 180000,
            'priceCurrency': 'USD',
        },
    }
    return (
        f'<div class="card property-item" data-id="{listing_id}" data-type="{card_type}">'
        '<p class="description"><small>Casa con jardín.</small></p>'
        f'<script type="application/ld+json">{json.dumps(payload)}</script></div>'
    )


def listing_page(*cards: str, last_page: int = 1) -> str:
    links = ''.join(
        f'<a class="page-link" href="?page={page}">{page}</a>'
        for page in range(1, last_page + 1)
    )
    return f'<html><body>{"".join(cards)}<ul class="pagination">{links}</ul></body></html>'


def test_registry_recognizes_alberto_dacal_host():
    assert source_for_url('https://www.dacal.com.ar/propiedades/ventas').id == 'albertodacal'
    assert source_for_url('https://dacal.com.ar.evil.test/propiedades/ventas') is None


def test_location_resolution_uses_reviewed_exact_routes():
    assert resolve_location('City Bell, La Plata') == ('City+Bell', 'City Bell')
    assert resolve_location('Gonnet') == ('Manuel+B+Gonnet', 'Manuel B Gonnet')
    assert resolve_location('Casco Urbano') == ('La+Plata', 'La Plata')
    assert resolve_location('Los Hornos') is None


def test_search_url_encodes_operation_property_type_and_location():
    url = search_url(
        source_by_id('albertodacal'),
        ScrapingFilters(tipo_operacion='venta', tipos_propiedad=['casa']),
        'City+Bell',
    )
    assert url == (
        'https://dacal.com.ar/propiedades/casas/venta/'
        'Argentina-G.B.A.+Zona+Sur-La+Plata-City+Bell'
    )


def test_card_parser_uses_structured_fields_and_original_image():
    card = BeautifulSoup(listing_card(), 'html.parser').select_one('.property-item')
    prop = parse_card(card, source_by_id('albertodacal'))
    assert prop is not None
    assert prop.direccion == 'Calle 10 123, City Bell'
    assert (prop.precio, prop.ambientes, prop.banos, prop.m2_total) == (180000, 4, 2, 180)
    assert prop.raw['dormitorios'] == 3
    assert prop.descripcion == 'Casa con jardín.'
    assert prop.imagenes == ['https://d1v2p1s05qqabi.cloudfront.net/10/1000.jpg']


def test_page_parser_reads_only_property_cards_and_pagination():
    properties, total_pages = parse_page(
        listing_page(listing_card(1), listing_card(2), last_page=7),
        source_by_id('albertodacal'),
    )
    assert [prop.raw['listing_id'] for prop in properties] == ['1', '2']
    assert total_pages == 7


async def test_scraper_pages_route_and_rejects_wrong_locality(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = int(request.url.params.get('page', '1'))
        if page == 1:
            return httpx.Response(200, text=listing_page(
                listing_card(1), listing_card(2, locality='Villa Elisa'), last_page=2,
            ))
        return httpx.Response(200, text=listing_page(listing_card(3)))

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
        transport=httpx.MockTransport(handler), follow_redirects=True,
    ))
    progress = AsyncMock()
    props = await scrape_alberto_dacal(
        source_by_id('albertodacal'),
        ScrapingFilters(zona='City Bell', tipo_operacion='venta', tipos_propiedad=['casa']),
        progress,
    )
    assert [prop.raw['listing_id'] for prop in props] == ['1', '3']
    assert all('Argentina-G.B.A.+Zona+Sur-La+Plata-City+Bell' in request.url.path for request in requests)
    assert progress.call_args.args == ('albertodacal', 'done', 2)


async def test_unknown_location_never_runs_an_unscoped_search(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(httpx, 'AsyncClient', client)
    assert await scrape_alberto_dacal(
        source_by_id('albertodacal'), ScrapingFilters(zona='Los Hornos'), AsyncMock(),
    ) == []
    client.assert_not_called()
