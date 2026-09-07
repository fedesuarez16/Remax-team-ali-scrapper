"""Reviewed public website contracts, with synthetic HTML and no network."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import settings
from app.models.property import RawProperty, ScrapingFilters
from app.services import apify
from app.services.source_filters import matches_source_filters
from app.services.source_registry import source_by_id, source_for_url
from app.services.tokko import (
    location_catalog, parse_detail, parse_listing, resolve_location, scrape_tokko, search_params,
)

LOCATIONS = [
    {'location_id': 26499, 'location_name': 'La Plata', 'parent_id': 149,
     'parent_name': 'G.B.A. Zona Sur'},
    {'location_id': 26520, 'location_name': 'La Plata', 'parent_id': 26499,
     'parent_name': 'La Plata'},
    {'location_id': 26514, 'location_name': 'City Bell', 'parent_id': 26499,
     'parent_name': 'La Plata'},
    {'location_id': 26503, 'location_name': 'Grand Bell', 'parent_id': 26514,
     'parent_name': 'City Bell'},
    {'location_id': 26532, 'location_name': 'Manuel B Gonnet', 'parent_id': 26499,
     'parent_name': 'La Plata'},
]
CATALOG = f'<script>var locations_response = {json.dumps(LOCATIONS)}\nload_locations();</script>'
DETAIL = '''
<div id="ficha_desc">
 <ul id="lista_informacion_basica">
  <li>Ambientes : 5</li><li>Dormitorios : 2</li><li>Baños : 2</li><li>Cocheras : 1</li>
  <li>Antigüedad : A Estrenar</li>
 </ul>
 <ul id="lista_superficies"><li>Terreno: 430 m²</li><li>Cubierta: 126 m²</li>
  <li>Total Construido: 161 m²</li></ul>
 <div id="prop-desc">Casa &lt;br&gt;con jardín.</div>
</div>
<img class="zoomImg" src="https://static.tokkobroker.com/pictures/test-house-a.jpg">
<img class="zoomImg" src="https://static.tokkobroker.com/pictures/test-house-b.jpg">
<img class="zoomImg" src="https://static.tokkobroker.com/pictures/test-house-a.jpg">
<section id="seccion-similares"><img src="https://example.com/another-house.jpg"></section>
'''


def card(listing_id=100, *, operation='Venta', kind='Casa', location='City Bell, La Plata',
         price='USD95.000') -> str:
    return f'''<li prop-id="{listing_id}">
      <a href="/p/{listing_id}-Casa-en-City-Bell">
        <div class="prop-desc-tipo-ub">{kind} en {operation} en {location}</div>
        <div class="prop-desc-dir">Calle de prueba 123</div>
        <div class="prop-data2"><div>2</div><img src="dormitorios.png"></div>
        <img class="dest-img" src="https://static.tokkobroker.com/pictures/test-cover.jpg">
      </a><div class="prop-valor-nro">{price}<div class="codref">TEST{listing_id}</div></div>
    </li>'''


@pytest.mark.parametrize(('url', 'expected'), [
    ('https://www.mauroperribienesraices.com.ar/', 'mauroperri'),
    ('http://urquiza.com.ar/Venta', 'urquiza'),
    ('https://www.inmobusqueda.com.ar/', 'inmobusqueda'),
    ('https://www.inmobusqueda.com.ar/inmobiliaria-123', None),
    ('https://urquiza.com.ar.evil.example/', None),
    ('https://urquiza.com.ar@evil.example/', None),
    ('https://evil.example/?next=urquiza.com.ar', None),
    ('https://urquiza.com.ar:8080/', None),
])
def test_registry_matches_reviewed_hosts_only(url, expected):
    source = source_for_url(url)
    assert (source.id if source else None) == expected


@pytest.mark.parametrize(('zona', 'expected'), [
    ('City Bell, La Plata', '26514'), ('La Plata', '26520'),
    ('Casco urbano, La Plata', '26520'), ('Gonnet', '26532'),
    ('Grand Bell, City Bell, La Plata', '26503'), ('City Bell, Córdoba', None),
    ('Lomas de City Bell', None), ('Lugar inexistente', None),
])
def test_location_resolution_uses_catalogue_and_ancestry(zona, expected):
    assert resolve_location(location_catalog(CATALOG), zona) == expected


def test_ambiguous_location_requires_a_parent():
    rows = LOCATIONS + [{'location_id': 9, 'location_name': 'City Bell',
                         'parent_id': 8, 'parent_name': 'Otra provincia'}]
    assert resolve_location(rows, 'City Bell') is None
    assert resolve_location(rows, 'City Bell, La Plata') == '26514'


def test_detail_owns_its_gallery_and_distinguishes_rooms_and_bedrooms():
    prop = parse_listing(card(), source_by_id('urquiza'))[0]
    assert prop.precio == 95000
    assert prop.ambientes is None
    prop = parse_detail(DETAIL, prop)
    assert (prop.ambientes, prop.raw['dormitorios'], prop.banos) == (5, 2, 2)
    assert (prop.m2_total, prop.m2_cubiertos, prop.antiguedad) == (161, 126, 0)
    assert len(prop.imagenes) == 2
    assert all('test-house' in url for url in prop.imagenes)
    assert '<br>' not in prop.descripcion


def test_consultar_never_parses_the_listing_code_as_the_price():
    prop = parse_listing(card(price='Consultar'), source_by_id('mauroperri'))[0]
    assert prop.precio is None


def test_dual_operation_card_keeps_the_displayed_sale_price_with_sale():
    prop = parse_listing(card(operation='Venta / Alquiler'), source_by_id('mauroperri'))[0]
    assert prop.tipo_operacion == 'venta'
    assert prop.precio == 95000


def test_native_search_sends_operation_types_and_location():
    params = search_params(ScrapingFilters(
        tipo_operacion='alquiler_temp', tipos_propiedad=['casa', 'ph'], ambientes_min=3,
    ), '26514')
    assert params == {'operation': '3', 'ptypes': '3,4,13', 'locations': '26514', 'o': '2,2'}


@pytest.mark.parametrize('changes', [
    {'tipo_operacion': 'alquiler'}, {'tipos_propiedad': ['departamento']},
    {'precio_max': 90000}, {'ambientes_max': 4}, {'dormitorios_min': 3},
    {'m2_min': 200}, {'zona_pedida': 'Gonnet'}, {'barrio_aliases': ['Grand Bell']},
])
def test_final_filter_rejects_non_matching_properties(changes):
    prop = parse_detail(DETAIL, parse_listing(card(), source_by_id('urquiza'))[0])
    assert not matches_source_filters(prop, ScrapingFilters(**changes))


def test_missing_requested_fields_cannot_prove_a_match():
    prop = parse_listing(card(price='Consultar'), source_by_id('urquiza'))[0]
    assert not matches_source_filters(prop, ScrapingFilters(precio_max=100000))
    assert not matches_source_filters(prop, ScrapingFilters(ambientes_min=2))
    assert matches_source_filters(prop, ScrapingFilters(tipo_operacion='venta'))


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


@pytest.mark.parametrize('source_id', ['mauroperri', 'urquiza'])
async def test_scraper_walks_beyond_an_unmatched_page_and_fetches_only_own_details(
    monkeypatch, source_id,
):
    monkeypatch.setattr(settings, 'TOKKO_MAX_PAGES', 0)

    def handler(request):
        if request.url.path.startswith('/p/'):
            return httpx.Response(200, text=DETAIL)
        page = request.url.params.get('p')
        body = {None: CATALOG, '1': card(operation='Alquiler'),
                '2': card(101), '3': '--NoMoreProperties--'}[page]
        return httpx.Response(200, text=body)

    requests = mock_client(monkeypatch, handler)
    props = await scrape_tokko(source_by_id(source_id), ScrapingFilters(
        zona='City Bell, La Plata', tipo_operacion='venta', tipos_propiedad=['casa'],
        precio_max=100000, ambientes_min=5, dormitorios_max=2,
    ), AsyncMock())
    assert len(props) == 1
    assert props[0].fuente == source_id
    assert props[0].raw['listing_id'] == '101'
    details = [r for r in requests if r.url.path.startswith('/p/')]
    assert len(details) == 1
    searches = [r for r in requests if 'p' in r.url.params]
    assert [r.url.params['p'] for r in searches] == ['1', '2', '3']
    assert all(r.url.params['locations'] == '26514' for r in searches)


async def test_repeated_last_page_stops_even_when_nothing_matches(monkeypatch):
    requests = mock_client(monkeypatch, lambda r: httpx.Response(
        200, text=CATALOG if 'p' not in r.url.params else card(operation='Alquiler'),
    ))
    assert await scrape_tokko(source_by_id('urquiza'), ScrapingFilters(
        zona='City Bell', tipo_operacion='venta',
    ), AsyncMock()) == []
    assert len(requests) == 3


async def test_unknown_location_never_turns_into_a_nationwide_search(monkeypatch):
    requests = mock_client(monkeypatch, lambda r: httpx.Response(200, text=CATALOG))
    assert await scrape_tokko(source_by_id('urquiza'), ScrapingFilters(
        zona='Inventada',
    ), AsyncMock()) == []
    assert len(requests) == 1


async def test_detail_block_is_an_error_instead_of_zero_matches(monkeypatch):
    def handler(request):
        if request.url.path.startswith('/p/'):
            return httpx.Response(403)
        page = request.url.params.get('p')
        return httpx.Response(200, text={None: CATALOG, '1': card(),
                                       '2': '--NoMoreProperties--'}[page])

    mock_client(monkeypatch, handler)
    progress = AsyncMock()
    with pytest.raises(ValueError, match='falló la lectura'):
        await scrape_tokko(source_by_id('urquiza'), ScrapingFilters(zona='City Bell'), progress)
    assert progress.call_args.args[1] == 'error'


def test_challenge_page_is_not_a_valid_empty_listing():
    with pytest.raises(ValueError, match='no se reconoció'):
        parse_listing('<html>Verify you are human</html>', source_by_id('urquiza'))


async def test_apify_entrypoint_dispatches_the_registered_source(monkeypatch):
    from app.services import tokko
    scrape = AsyncMock(return_value=[RawProperty(
        fuente='urquiza', direccion='City Bell', url_origen='https://www.urquiza.com.ar/p/100',
    )])
    monkeypatch.setattr(tokko, 'scrape_tokko', scrape)
    service = apify.ApifyService.__new__(apify.ApifyService)
    props = await service.scrape_source('urquiza', ScrapingFilters(zona='City Bell'), AsyncMock())
    assert props[0].fuente == 'urquiza'
    assert scrape.call_args.args[1].zona_pedida == 'City Bell'
