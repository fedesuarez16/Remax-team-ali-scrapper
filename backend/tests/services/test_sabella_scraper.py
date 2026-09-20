"""Sabella searches stay inside its native locality/neighborhood parameters."""
from __future__ import annotations

from unittest.mock import AsyncMock

import httpx

from app.models.property import ScrapingFilters
from app.services.sabella import parse_page, resolve_location, scrape_sabella, search_params
from app.services.source_registry import source_by_id, source_for_url


def card(listing_id: int = 1, *, locality: str = 'City Bell') -> str:
    return f'''
    <div class="single-property">
      <div class="property-container"><div class="property-title"><div class="title-left">
        <h4><a>U$S180000</a></h4><span>{locality}</span>
      </div></div>
      <div class="property-image"><a href="casa-ficha-jsp{listing_id}">
        <img src="https://images.example/{listing_id}.jpg"><span class="p-tag">VENTA</span>
      </a></div></div>
      <div class="property-content"><div class="property-title"><h3>Casa de 3 dormitorios</h3></div>
        <p>Casa con jardín.</p><div class="list-item"><span>140 Sup. Cub</span></div>
        <div class="list-item"><span>3 D</span></div><div class="list-item"><span>2</span></div>
      </div>
    </div>'''


def page(*cards: str, last_index: int = 0) -> str:
    links = ''.join(f'<a href="propiedades.php?p={index}&ope=V">{index + 1}</a>'
                    for index in range(last_index + 1))
    return ''.join(cards) + links


def test_registry_and_reviewed_location_parameters():
    assert source_for_url('https://www.sabellapropiedades.com.ar/propiedades.php').id == 'sabella'
    assert resolve_location('City Bell, La Plata') == ('city bell', None)
    assert resolve_location('Gonnet') == ('la plata', 'Manuel B Gonnet')
    assert resolve_location('Villa Elisa') == ('la plata', 'Villa Elisa')
    assert resolve_location('Ensenada') is None


def test_search_params_encode_operation_location_and_type():
    assert search_params(
        ScrapingFilters(tipo_operacion='venta', tipos_propiedad=['ph']),
        ('la plata', 'Casco Urbano'), 2,
    ) == {
        'p': 2, 'ope': 'V', 'loc': 'la plata', 'b': 'Casco Urbano',
        'tipo': 'All', 'in_tpr': 'PH',
    }


def test_parser_reads_card_fields_and_zero_based_pagination():
    properties, pages = parse_page(page(card(), last_index=4), source_by_id('sabella'))
    prop = properties[0]
    assert pages == 5
    assert (prop.precio, prop.tipo_propiedad, prop.tipo_operacion) == (180000, 'casa', 'venta')
    assert (prop.m2_total, prop.m2_cubiertos, prop.banos) == (140, 140, 2)
    assert prop.raw['dormitorios'] == 3
    assert prop.imagenes == ['https://images.example/1.jpg']


async def test_scraper_pages_and_rejects_wrong_locality(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get('p') == '1':
            return httpx.Response(200, text=page(card(3)))
        return httpx.Response(200, text=page(card(1), card(2, locality='Villa Elisa'), last_index=1))

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
        transport=httpx.MockTransport(handler), follow_redirects=True,
    ))
    progress = AsyncMock()
    properties = await scrape_sabella(
        source_by_id('sabella'),
        ScrapingFilters(zona='City Bell', tipo_operacion='venta'),
        progress,
    )
    assert [prop.raw['listing_id'] for prop in properties] == ['jsp1', 'jsp3']
    assert requests[0].url.params['loc'] == 'city bell'
    assert progress.call_args.args == ('sabella', 'done', 2)


async def test_unknown_location_never_requests_an_unscoped_catalog(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(httpx, 'AsyncClient', client)
    assert await scrape_sabella(
        source_by_id('sabella'), ScrapingFilters(zona='Ensenada'), AsyncMock(),
    ) == []
    client.assert_not_called()
