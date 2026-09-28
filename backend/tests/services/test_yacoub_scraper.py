"""Synthetic contracts for Yacoub's public search; no live requests."""
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.models.property import ScrapingFilters
from app.services import yacoub
from app.services.source_registry import source_by_id
from app.services.tokko import resolve_location

SOURCE = source_by_id('yacoub')
LOCATIONS = [
    {'i': 26499, 'n': 'La Plata y alrededores', 'p': 'G.B.A. Zona Sur'},
    {'i': 26520, 'n': 'La Plata', 'p': 'La Plata'},
    {'i': 26514, 'n': 'City Bell', 'p': 'La Plata'},
    {'i': 26503, 'n': 'Grand Bell', 'p': 'City Bell'},
]


def listing(listing_id=123, *, location='City Bell', next_page=False, price='USD 95.000'):
    pagination = '<a href="/propiedades/?paginado=2"><button class="next">2</button></a>'
    return f'''<section id="lista"><a class="card-propiedad"
      href="https://yacoub.com.ar/propiedad/calle-123/{listing_id}/">
      <h3 class="yc-prop__tit">Casa en {location}</h3>
      <p class="yc-prop__tipo">Venta</p><p class="yc-prop__dir">Calle 123</p>
      <p class="precio"><small>{price}</small></p>
    </a><div class="container-pagination">
      {pagination if next_page else ''}</div></section>'''


def detail(listing_id=123, *, price=95000, availability='InStock'):
    data = {
        '@type': 'RealEstateListing',
        'url': f'https://yacoub.com.ar/propiedad/calle-123/{listing_id}/',
        'about': {
            'address': {'streetAddress': 'Calle 123', 'addressLocality': 'City Bell'},
            'numberOfRooms': 4, 'numberOfBedrooms': 2, 'numberOfBathroomsTotal': 1,
            'geo': {'latitude': -34.9, 'longitude': -58.0},
        },
        'offers': {'price': price, 'priceCurrency': 'USD',
                   'availability': f'https://schema.org/{availability}'},
    }
    return f'''<script type="application/ld+json">{json.dumps(data)}</script>
      <h1 class="yc-ficha-h1">Casa en Venta en Calle 123, City Bell</h1>
      <section class="section-3"><div class="datos">
        <div class="info">300 M<sup>2</sup> totales</div>
        <div class="info">110 M<sup>2</sup> cubiertos</div>
        <div class="info">2 Cocheras</div></div>
        <div id="ycDesc">Casa con jardín.</div>
        <div class="container-content"><h2>Adicionales</h2><li>Pileta</li></div>
      </section><div id="images_gallery">
        <a data-fancybox="images-gallery" href="https://example.com/own.jpg"></a>
        <a data-fancybox="images-gallery" href="https://example.com/own.jpg"></a>
      </div><a data-fancybox="images-gallery" href="https://example.com/recommended.jpg"></a>'''


def mock_client(monkeypatch, handler):
    original = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path.endswith('ubicaciones.json'):
            return httpx.Response(200, json=LOCATIONS)
        return handler(request)

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
        transport=httpx.MockTransport(handle), follow_redirects=True,
    ))
    return requests


@pytest.mark.parametrize(('zone', 'expected'), [
    ('La Plata', '26520'), ('City Bell, La Plata', '26514'),
    ('Grand Bell, City Bell, La Plata', '26503'), ('City Bell, Córdoba', None),
])
def test_location_catalog_preserves_city_and_ancestry(zone, expected):
    assert resolve_location(yacoub.location_catalog(LOCATIONS), zone) == expected


def test_native_search_separates_operation_type_and_pagination():
    params = yacoub.search_params(ScrapingFilters(
        tipo_operacion='alquiler_temp', tipos_propiedad=['casa', 'ph'],
        precio_min=100, precio_max=2000,
    ), '26514', 2)
    data = json.loads(params['data'])
    assert params['paginado'] == '2'
    assert data['current_localization_id'] == [26514]
    assert data['operation_types'] == [3]
    assert data['property_types'] == [3, 4, 13]
    assert data['price_to'] == 999999999  # Price is checked locally without inventing a currency.
    assert data['currency'] == 'ANY'


def test_detail_reads_own_gallery_rooms_areas_and_coordinates():
    prop = yacoub.parse_detail(detail(), yacoub.parse_listing(listing(), SOURCE)[0][0])
    assert prop.ambientes == 4
    assert prop.raw['dormitorios'] == 2
    assert prop.raw['latitude'] == -34.9
    assert prop.m2_total == 300
    assert prop.m2_cubiertos == 110
    assert prop.cocheras == 2
    assert prop.imagenes == ['https://example.com/own.jpg']
    assert prop.amenities == ['Pileta']
    assert prop.descripcion == 'Casa con jardín.'


def test_unavailable_property_is_excluded():
    prop = yacoub.parse_listing(listing(), SOURCE)[0][0]
    assert yacoub.parse_detail(detail(availability='SoldOut'), prop) is None


@pytest.mark.parametrize('html', ['Verify you are human', '<section id="lista"></section>'])
def test_unknown_or_blocked_page_is_not_zero_results(html):
    with pytest.raises(ValueError):
        yacoub.parse_listing(html, SOURCE)


def test_explicit_empty_result_is_supported():
    assert yacoub.parse_listing(
        '<section id="lista">No se han encontrado propiedades</section>', SOURCE,
    ) == ([], False)


def test_previous_arrow_on_last_page_does_not_request_another_page():
    html = listing(next_page=True).replace('paginado=2', 'paginado=1')
    assert yacoub.parse_listing(html, SOURCE, page=2)[1] is False


async def test_walks_past_rejected_page_and_checks_detail_price(monkeypatch):
    def handler(request):
        if '/propiedad/' in request.url.path:
            return httpx.Response(200, text=detail(456))
        page = request.url.params['paginado']
        return httpx.Response(200, text=listing(location='Gonnet', next_page=True)
                              if page == '1' else listing(456))

    requests = mock_client(monkeypatch, handler)
    props = await yacoub.scrape_yacoub(SOURCE, ScrapingFilters(
        zona='City Bell', tipo_operacion='venta', ambientes_min=3, precio_max=100000,
    ), AsyncMock())
    assert [p.raw['listing_id'] for p in props] == ['456']
    assert len([r for r in requests if '/propiedad/' in r.url.path]) == 1
    assert [r.url.params['paginado'] for r in requests if '/propiedades/' == r.url.path] == ['1', '2']


async def test_repeated_page_terminates_even_when_all_cards_fail_filters(monkeypatch):
    requests = mock_client(monkeypatch, lambda r: httpx.Response(
        200, text=listing(location='Gonnet', next_page=True),
    ))
    assert await yacoub.scrape_yacoub(SOURCE, ScrapingFilters(zona='City Bell'), AsyncMock()) == []
    assert len(requests) == 3


async def test_unknown_location_never_searches_unscoped(monkeypatch):
    requests = mock_client(monkeypatch, lambda r: pytest.fail('Unexpected catalogue search'))
    assert await yacoub.scrape_yacoub(SOURCE, ScrapingFilters(zona='Inventada'), AsyncMock()) == []
    assert len(requests) == 1


async def test_blocked_detail_is_reported_as_error(monkeypatch):
    mock_client(monkeypatch, lambda r: httpx.Response(403) if '/propiedad/' in r.url.path
                else httpx.Response(200, text=listing()))
    progress = AsyncMock()
    with pytest.raises(ValueError, match='falló la lectura'):
        await yacoub.scrape_yacoub(SOURCE, ScrapingFilters(zona='City Bell'), progress)
    assert progress.call_args.args == ('yacoub', 'error', 0)
