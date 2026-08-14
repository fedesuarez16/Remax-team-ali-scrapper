"""RE/MAX paging depth is a business knob, not a portal constraint: its public
API happily serves far more than the 5 pages `_scrape_remax_api` used to
hard-code (5 × 20 = 100 items, which is why every zona search topped out at
exactly 100). The cap now lives in `settings.REMAX_MAX_PAGES`, mirroring
`ARGENPROP_MAX_PAGES`, with `0` meaning "no cap — follow `totalPages`"
(same semantics as `ZONAPROP_MAX_RESULTS`).

Unlike Argenprop there is no robots.txt page ceiling to hard-cap against, so
the only stop conditions are the setting, an empty page, and `totalPages`.
"""
import httpx
import pytest

from app.core.config import settings
from app.models.property import ScrapingFilters
from app.services import apify
from app.services.apify import _scrape_remax_api

_TOTAL_PAGES = 12


def _item(id_: int) -> dict:
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
        'geoLabel': 'Palermo, Capital Federal',
        'dimensionTotalBuilt': 55.0,
        'dimensionCovered': 50.0,
    }


async def _noop_progress(_src: str, _status: str, _count: int) -> None:
    return None


@pytest.fixture(autouse=True)
def _no_location_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _none(zona: str) -> None:
        return None
    monkeypatch.setattr(apify, '_remax_resolve_location', _none)


@pytest.fixture(autouse=True)
def _mock_remax_api(monkeypatch: pytest.MonkeyPatch) -> dict:
    """A deep result set: `_TOTAL_PAGES` full pages, so the ONLY thing that can
    stop paging early is the configured cap."""
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
            page = int(params.get('page', 0))
            size = int(params.get('pageSize', 20))
            items = [] if page >= _TOTAL_PAGES else [
                _item(page * size + i) for i in range(size)
            ]
            return _FakeResponse({
                'data': {
                    'data': items, 'page': page,
                    'totalPages': _TOTAL_PAGES, 'totalItems': _TOTAL_PAGES * size,
                },
                'code': 200, 'message': '', 'errors': None,
            })

    monkeypatch.setattr(httpx, 'AsyncClient', _FakeAsyncClient)
    return captured


def _filters() -> ScrapingFilters:
    return ScrapingFilters(zona='Palermo', tipo_operacion='venta')


async def test_page_cap_comes_from_settings(
    _mock_remax_api: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, 'REMAX_MAX_PAGES', 8)
    results = await _scrape_remax_api(_filters(), _noop_progress)
    assert len(_mock_remax_api['requests']) == 8
    assert len(results) == 8 * settings.REMAX_PAGE_SIZE


async def test_zero_means_no_cap_and_follows_total_pages(
    _mock_remax_api: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, 'REMAX_MAX_PAGES', 0)
    await _scrape_remax_api(_filters(), _noop_progress)
    assert len(_mock_remax_api['requests']) == _TOTAL_PAGES


async def test_page_size_comes_from_settings(
    _mock_remax_api: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, 'REMAX_MAX_PAGES', 1)
    monkeypatch.setattr(settings, 'REMAX_PAGE_SIZE', 50)
    results = await _scrape_remax_api(_filters(), _noop_progress)
    assert _mock_remax_api['requests'][0]['pageSize'] == 50
    assert len(results) == 50


async def test_default_ships_uncapped() -> None:
    # The 5 × 20 = 100 default WAS the bug: every zona search topped out at
    # exactly 100 items and it read like a portal limit. See
    # `test_uncapped_pagination.py` for the cross-portal contract.
    assert settings.REMAX_MAX_PAGES == 0
    assert settings.REMAX_PAGE_SIZE == 200
