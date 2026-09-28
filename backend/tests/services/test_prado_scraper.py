"""Prado's agency scope, legacy encoding, pagination and published card facts."""
from urllib.parse import parse_qs
from unittest.mock import AsyncMock

import httpx
import pytest

from app.models.property import ScrapingFilters
from app.services import prado
from app.services.source_filters import matches_source_filters
from app.services.source_registry import source_by_id

SOURCE = source_by_id('prado')
PROFILE = '<form id="elbuscador"><input name="eid" value="2134"></form>'


def card(listing_id=123, *, location='City Bell, Pdo. de La Plata', agency_id='2134'):
    return f'''<div class="cajaPremium2017">
      <a href="http://www.inmobusqueda.com.ar/ficha-{listing_id}">
        <img class="FotoBox" src="https://fotos55.inmobusqueda.com/{agency_id}/{listing_id}/x200/house.jpg">
      </a><div class="destacadoTipo">Casa en Venta</div>
      <div class="destacadoDireccion">Calle 123</div>
      <div class="destacadoLocalidad">{location}</div>
      <div class="destacadoDescripcion">Casa con jardín.</div>
      <div class="destacadoPrecio">u$d95.000</div>
      <div class="destacadoDuenio">2 dormitorios 120m ² (Sup. Construida)</div>
    </div>'''


def page(cards, *, last=1):
    return f'''<div>60 propiedades</div>{cards}
      <a href="#" onclick="javascript:buscar({last},'?eid=2134');">{last}</a>'''


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


def test_own_profile_card_preserves_bedrooms_and_does_not_invent_rooms():
    props, pages = prado.parse_listing(page(card(), last=3), SOURCE)
    prop = props[0]
    assert pages == 3
    assert prop.fuente == 'prado'
    assert prop.raw['agency_id'] == '2134'
    assert prop.raw['dormitorios'] == 2
    assert prop.ambientes is None
    assert prop.precio == 95000
    assert prop.moneda == 'USD'
    assert prop.m2_total == 120
    assert prop.imagenes == ['https://fotos55.inmobusqueda.com/2134/123/house.jpg']
    assert prop.url_origen == 'https://www.inmobusqueda.com.ar/ficha-123'
    assert not matches_source_filters(prop, ScrapingFilters(ambientes_min=3))
    assert not matches_source_filters(prop, ScrapingFilters(zona='Gonnet'))


def test_unknown_fields_and_consult_price_cannot_match_requested_bounds():
    prop = prado.parse_listing(card().replace('u$d95.000', 'Consulte'), SOURCE)[0][0]
    assert prop.precio is None
    assert not matches_source_filters(prop, ScrapingFilters(precio_max=100000))


def test_depto_card_uses_the_portals_abbreviated_type_label():
    html = card().replace('Casa en Venta', 'Depto en Venta')
    prop = prado.parse_listing(html, SOURCE)[0][0]
    assert prop.tipo_propiedad == 'departamento'
    assert matches_source_filters(prop, ScrapingFilters(
        zona='City Bell', tipos_propiedad=['departamento'], precio_max=100000,
    ))


def test_country_house_keeps_its_type_separate_from_the_operation():
    html = card().replace('Casa en Venta', 'Casa en Country en Venta')
    prop = prado.parse_listing(html, SOURCE)[0][0]
    assert prop.tipo_propiedad == 'casa'
    assert prop.tipo_operacion == 'venta'


def test_other_agency_images_reject_unscoped_response():
    with pytest.raises(ValueError, match='otra inmobiliaria'):
        prado.parse_listing(card(agency_id='999'), SOURCE)


def test_blocked_listing_is_not_empty():
    with pytest.raises(ValueError, match='no se reconoció'):
        prado.parse_listing('No soy bot', SOURCE)
    assert prado.parse_listing('0 propiedades', SOURCE) == ([], 1)


@pytest.mark.parametrize('encoding', ['utf-8', 'latin-1'])
def test_legacy_profile_encoding_ignores_incorrect_charset_declaration(encoding):
    html = '<meta charset="utf-8">' + card(location='José Hernández')
    response = httpx.Response(200, content=html.encode(encoding))
    prop = prado.parse_listing(prado._response_html(response), SOURCE)[0][0]
    assert 'José Hernández' in prop.direccion


async def test_walks_all_pages_even_when_first_page_does_not_match(monkeypatch):
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, text=PROFILE)
        current = request.url.params['pagina']
        return httpx.Response(200, content=page(
            card(location='Gonnet') if current == '1' else card(456), last=2,
        ).encode('latin-1'))

    requests = mock_client(monkeypatch, handler)
    progress = AsyncMock()
    props = await prado.scrape_prado(SOURCE, ScrapingFilters(
        zona='City Bell', tipo_operacion='venta', dormitorios_min=2, precio_max=100000,
    ), progress)
    assert [p.raw['listing_id'] for p in props] == ['456']
    searches = [r for r in requests if r.method == 'POST']
    assert [r.url.params['pagina'] for r in searches] == ['1', '2']
    assert all(parse_qs(r.content.decode())['eid'] == ['2134'] for r in searches)
    assert all(parse_qs(r.content.decode())['operacion'] == ['1'] for r in searches)
    assert progress.call_args.args == ('prado', 'done', 1)


async def test_repeated_unmatched_page_terminates(monkeypatch):
    requests = mock_client(monkeypatch, lambda r: httpx.Response(
        200, text=PROFILE if r.method == 'GET' else page(card(location='Gonnet'), last=5),
    ))
    assert await prado.scrape_prado(SOURCE, ScrapingFilters(zona='City Bell'), AsyncMock()) == []
    assert len(requests) == 3


async def test_wrong_profile_never_starts_search(monkeypatch):
    requests = mock_client(monkeypatch, lambda r: httpx.Response(
        200, text=PROFILE.replace('2134', '999'),
    ))
    with pytest.raises(ValueError, match='verificar el perfil'):
        await prado.scrape_prado(SOURCE, ScrapingFilters(zona='City Bell'), AsyncMock())
    assert len(requests) == 1
