"""ZonaProp pagination driven by US, not the actor: the crawlerbros actor's
browser dies after page 1 (every run returns exactly ~30 items regardless of
maxResults), so `scrape_source` must launch one actor run per listing page
(`...-pagina-N.html`) until the cap is reached, a partial page signals the end,
or a page yields nothing new (redirect/dupe protection).
"""
from typing import Any

import pytest

from app.models.property import ScrapingFilters
from app.services.apify import ApifyService

# These exercise the Apify actor path, kept as the documented fallback
# (`ZONAPROP_USE_APIFY=true`). Production reads ZonaProp directly.
pytestmark = pytest.mark.usefixtures('apify_zonaprop')


_PAGE_SIZE = 30


def _item(i: int, *, city: str = 'La Plata') -> dict[str, Any]:
    return {
        'title': f'Casa {i} en Villa Elisa',
        'url': f'https://www.zonaprop.com.ar/propiedades/clasificado/x-{i}.html',
        'listingId': str(i),
        'neighborhood': 'Villa Elisa', 'city': city,
        'address': f'Calle {i}',
        'listingType': 'sale', 'propertyType': 'house',
        'price': 100000 + i, 'currency': 'USD',
    }


def _page(start: int, count: int) -> list[dict[str, Any]]:
    return [_item(i) for i in range(start, start + count)]


def _filters() -> ScrapingFilters:
    return ScrapingFilters(zona='Villa Elisa, La Plata', localidades=['Villa Elisa, La Plata'])


async def _noop_progress(source: str, status: str, count: int) -> None:
    return None


@pytest.fixture()
def service() -> ApifyService:
    return ApifyService(api_token='dummy-token')


@pytest.fixture()
def cap_60(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, 'ZONAPROP_MAX_RESULTS', 60)


async def test_paginates_until_cap(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch, cap_60: None,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run(src: str, actor: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(input_data)
        return _page(len(calls) * 1000, _PAGE_SIZE)

    monkeypatch.setattr(service, '_run_actor', fake_run)
    results = await service.scrape_source('zonaprop', _filters(), _noop_progress)
    assert len(calls) == 2
    assert calls[0]['searchUrl'].endswith('inmuebles-venta-villa-elisa-la-plata.html')
    assert calls[1]['searchUrl'].endswith('inmuebles-venta-villa-elisa-la-plata-pagina-2.html')
    assert len(results) == 60


async def test_second_page_maxresults_is_remaining_cap(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch, cap_60: None,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run(src: str, actor: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(input_data)
        return _page(len(calls) * 1000, _PAGE_SIZE)

    monkeypatch.setattr(service, '_run_actor', fake_run)
    await service.scrape_source('zonaprop', _filters(), _noop_progress)
    assert calls[0]['maxResults'] == 60
    assert calls[1]['maxResults'] == 30


async def test_partial_page_stops_pagination(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch, cap_60: None,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run(src: str, actor: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(input_data)
        if len(calls) == 1:
            return _page(1000, _PAGE_SIZE)
        return _page(2000, 12)  # short page = last one

    monkeypatch.setattr(service, '_run_actor', fake_run)
    results = await service.scrape_source('zonaprop', _filters(), _noop_progress)
    assert len(calls) == 2
    assert len(results) == 42


async def test_duplicate_page_stops_pagination(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch, cap_60: None,
) -> None:
    # Out-of-range -pagina-N redirects back to page 1: same listingIds again.
    calls: list[dict[str, Any]] = []

    async def fake_run(src: str, actor: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(input_data)
        return _page(1000, _PAGE_SIZE)

    monkeypatch.setattr(service, '_run_actor', fake_run)
    results = await service.scrape_source('zonaprop', _filters(), _noop_progress)
    assert len(calls) == 2
    assert len(results) == 30


async def test_off_zona_page_stops_pagination(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch, cap_60: None,
) -> None:
    # A later page that yields ONLY guard-rejected items means we drifted into
    # a redirect — keep page 1's results but don't keep paginating garbage.
    calls: list[dict[str, Any]] = []

    async def fake_run(src: str, actor: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(input_data)
        if len(calls) == 1:
            return _page(1000, _PAGE_SIZE)
        return [_item(i, city='Villa Elisa') | {'description': 'Colón, Entre Ríos'}
                for i in range(2000, 2000 + _PAGE_SIZE)]

    monkeypatch.setattr(service, '_run_actor', fake_run)
    results = await service.scrape_source('zonaprop', _filters(), _noop_progress)
    assert len(calls) == 2
    assert len(results) == 30


async def test_single_call_when_cap_is_one_page(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, 'ZONAPROP_MAX_RESULTS', 30)
    calls: list[dict[str, Any]] = []

    async def fake_run(src: str, actor: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append(input_data)
        return _page(1000, _PAGE_SIZE)

    monkeypatch.setattr(service, '_run_actor', fake_run)
    results = await service.scrape_source('zonaprop', _filters(), _noop_progress)
    assert len(calls) == 1
    assert len(results) == 30
