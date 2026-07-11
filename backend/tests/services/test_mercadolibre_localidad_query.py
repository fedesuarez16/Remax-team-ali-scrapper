"""Test-first for `_scrape_mercadolibre_api`'s free-text `q` param (T-5.5/5.6,
ADR-4: no dedicated ML code path — it inherits the fan-out unit via
`filters.zona`, which fan-out sets to the localidad for polygon branches).

Mocks httpx so no real network call happens; captures the `q` param sent to
the MercadoLibre API.
"""
import httpx
import pytest

from app.models.property import ScrapingFilters
from app.services.apify import _scrape_mercadolibre_api


async def _noop_progress(_src, _status, _count) -> None:
    return None


@pytest.fixture(autouse=True)
def _mock_ml_api(monkeypatch):
    captured: dict = {}

    class _FakeResponse:
        def __init__(self) -> None:
            self.status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {'results': [], 'paging': {'total': 0}}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> '_FakeAsyncClient':
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def get(self, url, params=None, **kwargs) -> _FakeResponse:
            captured['q'] = params.get('q') if params else None
            return _FakeResponse()

    monkeypatch.setattr(httpx, 'AsyncClient', _FakeAsyncClient)
    return captured


async def test_ml_query_uses_zona_which_fanout_sets_to_localidad(_mock_ml_api) -> None:
    # Fan-out sets `filters.zona` to the localidad for polygon branches — ML
    # needs no dedicated code path, it just reads `filters.zona` like today.
    filters = ScrapingFilters(zona='CABA', localidades=['CABA'])
    await _scrape_mercadolibre_api(filters, _noop_progress)
    assert 'CABA' in _mock_ml_api['q']


async def test_ml_query_falls_back_to_zona_when_no_localidad(_mock_ml_api) -> None:
    filters = ScrapingFilters(zona='Palermo')
    await _scrape_mercadolibre_api(filters, _noop_progress)
    assert 'Palermo' in _mock_ml_api['q']
