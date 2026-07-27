"""RE/MAX has a public, unauthenticated JSON API (api-ar.redremax.com) that its
own Angular frontend consumes — confirmed with real requests. No documented
location filter exists, so `_scrape_remax_api` pages through results and
applies the same text-match zona guard ZonaProp already uses
(`_item_matches_zona`), matching against `geoLabel`/`displayAddress`.

IMPORTANT: the real response is DOUBLE-nested —
`{"data": {"data": [...items...], "page":0, "totalPages":N, "totalItems":M},
"code":200, "message":"...", "errors":null}` — confirmed against a real
request. A prior version of this fixture modeled it flat (`{"data": [...],
"totalPages": N}`), which hid a real bug: `_scrape_remax_api` iterated the
INNER paging dict's keys as if they were items, throwing
`'str' object has no attribute 'get'` in production the moment real listings
came back. This fixture mirrors the true shape.
"""
import httpx
import pytest

from app.models.property import ScrapingFilters
from app.services import apify
from app.services.apify import _scrape_remax_api


@pytest.fixture(autouse=True)
def _no_location_resolver(monkeypatch):
    """Keep these tests off the location-resolver path (it has its own suite
    in test_remax_location_resolver.py): unresolved location → nationwide
    paging + text zona guard, the fallback behavior pinned here."""
    async def _none(zona: str) -> None:
        return None
    monkeypatch.setattr(apify, '_remax_resolve_location', _none)


def _item(id_: int, *, geo_label: str = 'Palermo, Capital Federal') -> dict:
    return {
        'id': id_,
        'operation': {'id': 1, 'value': 'sale'},
        'currency': {'id': 1, 'value': 'USD'},
        'type': {'id': 2, 'value': 'departamento_estandar'},
        'title': f'Depto {id_}',
        'slug': f'depto-{id_}',
        'totalRooms': 3,
        'bathrooms': 1,
        'bedrooms': 2,
        'price': 100_000 + id_,
        'displayAddress': f'Calle Falsa {id_}',
        'geoLabel': geo_label,
        'dimensionTotalBuilt': 55.0,
        'dimensionCovered': 50.0,
    }


async def _noop_progress(_src, _status, _count) -> None:
    return None


@pytest.fixture(autouse=True)
def _mock_remax_api(monkeypatch):
    captured: dict = {'requests': []}

    class _FakeResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> '_FakeAsyncClient':
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def get(self, url, params=None, **kwargs) -> _FakeResponse:
            captured['requests'].append(params)
            page = params.get('page', 0)
            if page > 0:
                return _FakeResponse({
                    'data': {'data': [], 'page': page, 'totalPages': 1, 'totalItems': 2},
                    'code': 200, 'message': '', 'errors': None,
                })
            return _FakeResponse({
                'data': {
                    'data': [_item(1, geo_label='Palermo, Capital Federal'),
                             _item(2, geo_label='Belgrano, Capital Federal')],
                    'page': 0, 'totalPages': 1, 'totalItems': 2,
                },
                'code': 200, 'message': '', 'errors': None,
            })

    monkeypatch.setattr(httpx, 'AsyncClient', _FakeAsyncClient)
    return captured


async def test_sends_operation_and_type_ids(_mock_remax_api) -> None:
    filters = ScrapingFilters(zona='Palermo', tipo_operacion='venta', tipos_propiedad=['departamento'])
    await _scrape_remax_api(filters, _noop_progress)
    req = _mock_remax_api['requests'][0]
    assert req['in'] == ['operationId:1', 'typeId:1,2,3,4,5,6,7,8']


async def test_alquiler_maps_to_operation_id_2(_mock_remax_api) -> None:
    filters = ScrapingFilters(zona='Palermo', tipo_operacion='alquiler')
    await _scrape_remax_api(filters, _noop_progress)
    req = _mock_remax_api['requests'][0]
    assert req['in'][0] == 'operationId:2'


async def test_filters_results_by_zona_text_match(_mock_remax_api) -> None:
    filters = ScrapingFilters(zona='Palermo', tipo_operacion='venta')
    results = await _scrape_remax_api(filters, _noop_progress)
    assert len(results) == 1
    assert results[0].direccion == 'Calle Falsa 1'


async def test_unresolved_location_sends_no_locations_param(_mock_remax_api) -> None:
    filters = ScrapingFilters(zona='Palermo', tipo_operacion='venta')
    await _scrape_remax_api(filters, _noop_progress)
    assert 'locations' not in _mock_remax_api['requests'][0]


async def test_resolved_location_is_sent_as_server_side_filter(
    _mock_remax_api, monkeypatch,
) -> None:
    # Server-side filtering is what makes zona searches actually return
    # results: without it the newest-100 nationwide sample almost never
    # contains a specific zona (the original "always 0 results" bug).
    async def fake_resolve(zona: str) -> str:
        assert zona == 'Gonnet'
        return 'in::::1067:::'

    monkeypatch.setattr(apify, '_remax_resolve_location', fake_resolve)
    filters = ScrapingFilters(zona='Gonnet', tipo_operacion='venta')
    await _scrape_remax_api(filters, _noop_progress)
    assert _mock_remax_api['requests'][0]['locations'] == 'in::::1067:::'
