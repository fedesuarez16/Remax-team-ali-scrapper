"""Houzez agency searches use reviewed city/area slugs and embedded galleries."""
from __future__ import annotations

import html
import json
from unittest.mock import AsyncMock

import httpx

from app.models.property import ScrapingFilters
from app.services.houzez import parse_page, resolve_location, scrape_houzez, search_params
from app.services.source_registry import source_by_id, source_for_url


def card(listing_id: int = 1, *, locality: str = 'City Bell') -> str:
    images = html.escape(json.dumps([
        f'https://images.example/{listing_id}/one_wm.jpeg',
        f'https://images.example/{listing_id}/two_wm.jpeg',
    ]), quote=True)
    return f'''
    <div class="item-listing-wrap" data-hz-id="hz-{listing_id}" data-images="{images}">
      <a class="label-status" href="https://agency.test/estado/en-venta/">En venta</a>
      <h2 class="item-title"><a href="https://agency.test/propiedad/{listing_id}/">Casa</a></h2>
      <li class="item-price">USD 180.000</li>
      <address class="item-address">Calle 10, {locality}, La Plata</address>
      <li class="h-beds"><span class="hz-figure">3</span></li>
      <li class="h-baths"><span class="hz-figure">2</span></li>
      <li class="h-area"><span class="hz-figure">140</span></li>
      <li class="h-type"><span>Casas</span></li>
    </div>'''


def page(*cards: str, last_page: int = 1) -> str:
    return ''.join(cards) + ''.join(
        f'<a href="https://agency.test/propiedades/page/{number}/">{number}</a>'
        for number in range(2, last_page + 1)
    )


def test_registry_and_exact_location_mapping():
    assert source_for_url('https://www.axionpropiedades.com/propiedades/').id == 'axion'
    assert resolve_location('City Bell, La Plata') == ('city-bell', 'City Bell')
    assert resolve_location('Gonnet') == ('manuel-b-gonnet', 'Manuel B. Gonnet')
    assert resolve_location('Ensenada') is None


def test_query_uses_native_city_area_operation_and_types():
    assert search_params(
        ScrapingFilters(tipo_operacion='venta', tipos_propiedad=['casa']), 'city-bell',
    ) == [
        ('location[]', 'la-plata'), ('areas[]', 'city-bell'), ('status[]', 'en-venta'),
        ('type[]', 'casa'), ('type[]', 'chalet'), ('type[]', 'duplex'),
        ('type[]', 'casa-quinta'),
    ]


def test_parser_reads_structured_card_and_full_gallery():
    properties, pages = parse_page(page(card(), last_page=3), source_by_id('axion'))
    prop = properties[0]
    assert pages == 3
    assert prop.direccion == 'Calle 10, City Bell, La Plata'
    assert (prop.precio, prop.tipo_operacion, prop.tipo_propiedad) == (180000, 'venta', 'casa')
    assert (prop.banos, prop.m2_total, prop.raw['dormitorios']) == (2, 140, 3)
    assert prop.imagenes == [
        'https://images.example/1/one_wm.jpeg',
        'https://images.example/1/two_wm.jpeg',
    ]


async def test_scraper_pages_and_rejects_wrong_locality(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if '/page/2/' in request.url.path:
            return httpx.Response(200, text=page(card(3)))
        return httpx.Response(200, text=page(card(1), card(2, locality='Villa Elisa'), last_page=2))

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
        transport=httpx.MockTransport(handler), follow_redirects=True,
    ))
    progress = AsyncMock()
    properties = await scrape_houzez(
        source_by_id('axion'),
        ScrapingFilters(zona='City Bell', tipo_operacion='venta'),
        progress,
    )
    assert [prop.raw['listing_id'] for prop in properties] == ['1', '3']
    assert requests[0].url.params.get_list('areas[]') == ['city-bell']
    assert requests[0].url.params.get_list('location[]') == ['la-plata']
    assert progress.call_args.args == ('axion', 'done', 2)


async def test_unknown_location_never_requests_an_unscoped_catalog(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(httpx, 'AsyncClient', client)
    assert await scrape_houzez(
        source_by_id('axion'), ScrapingFilters(zona='Ensenada'), AsyncMock(),
    ) == []
    client.assert_not_called()
