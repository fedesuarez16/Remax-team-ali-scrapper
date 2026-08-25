"""Funnel instrumentation for the ZonaProp scrape.

The portal shows far more listings than a search returns, and the loss is
spread across four independent gates (dedup, zona guard, missing price, cap)
plus an early `break`. Counting them per page is what turns "trae menos" into
a number you can act on, so `_scrape_zonaprop_paginated` reports a funnel
alongside its results and records WHY it stopped paginating.
"""
from typing import Any

import pytest

from app.models.property import ScrapingFilters
from app.services.apify import ApifyService

_PAGE_SIZE = 30


def _item(i: int, *, city: str = 'La Plata', neighborhood: str = 'Villa Elisa',
          price: int | None = None) -> dict[str, Any]:
    return {
        'title': f'Casa {i} en {neighborhood}',
        'url': f'https://www.zonaprop.com.ar/propiedades/clasificado/x-{i}.html',
        'listingId': str(i),
        'neighborhood': neighborhood, 'city': city,
        'address': f'Calle {i}',
        'listingType': 'sale', 'propertyType': 'house',
        'price': (100000 + i) if price is None else price, 'currency': 'USD',
    }


def _off_zona(i: int) -> dict[str, Any]:
    return _item(i, city='Cordoba', neighborhood='Nueva Cordoba')


def _no_price(i: int) -> dict[str, Any]:
    item = _item(i)
    item['price'] = None
    return item


def _page(start: int, count: int) -> list[dict[str, Any]]:
    return [_item(i) for i in range(start, start + count)]


def _filters() -> ScrapingFilters:
    return ScrapingFilters(zona='Villa Elisa, La Plata', localidades=['Villa Elisa, La Plata'])


@pytest.fixture()
def service() -> ApifyService:
    return ApifyService(api_token='dummy-token')


@pytest.fixture()
def no_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, 'ZONAPROP_MAX_RESULTS', 0)


def _stub_pages(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
    pages: list[list[dict[str, Any]]],
) -> None:
    calls: list[int] = []

    async def fake_run(src: str, actor: str, input_data: dict[str, Any]) -> list[dict[str, Any]]:
        idx = len(calls)
        calls.append(idx)
        return pages[idx] if idx < len(pages) else []

    monkeypatch.setattr(service, '_run_actor', fake_run)


async def test_funnel_counts_every_drop_stage(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch, no_cap: None,
) -> None:
    """One full page of 10 keepers + 10 off-zona + 10 price-less items must be
    reported as exactly that, not as a bare `kept=10`."""
    mixed = (
        [_item(i) for i in range(1000, 1010)]
        + [_off_zona(i) for i in range(2000, 2010)]
        + [_no_price(i) for i in range(3000, 3010)]
    )
    _stub_pages(service, monkeypatch, [mixed, []])

    results, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

    assert len(results) == 10
    assert funnel.raw == 30
    assert funnel.zona_rejected == 10
    assert funnel.no_price == 10
    assert funnel.kept == 10
    assert funnel.duplicates == 0


async def test_funnel_records_a_row_per_page(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch, no_cap: None,
) -> None:
    _stub_pages(service, monkeypatch, [_page(1000, _PAGE_SIZE), _page(2000, _PAGE_SIZE), []])

    _, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

    assert [p.page for p in funnel.pages] == [1, 2, 3]
    assert [p.raw for p in funnel.pages] == [30, 30, 0]
    assert funnel.raw == sum(p.raw for p in funnel.pages)
    assert funnel.kept == sum(p.kept for p in funnel.pages)


async def test_funnel_reports_empty_page_stop(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch, no_cap: None,
) -> None:
    _stub_pages(service, monkeypatch, [_page(1000, _PAGE_SIZE), []])

    _, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

    assert funnel.stop_reason == 'empty_page'


async def test_funnel_reports_short_page_stop(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch, no_cap: None,
) -> None:
    """The `len(raw_items) < 30` break is the prime suspect for truncation —
    it must be visible in the report, not silent."""
    _stub_pages(service, monkeypatch, [_page(1000, 12)])

    _, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

    assert funnel.stop_reason == 'short_page'
    assert funnel.pages[-1].raw == 12


async def test_funnel_reports_all_rejected_stop(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch, no_cap: None,
) -> None:
    off = [_off_zona(i) for i in range(2000, 2000 + _PAGE_SIZE)]
    _stub_pages(service, monkeypatch, [_page(1000, _PAGE_SIZE), off])

    _, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

    assert funnel.stop_reason == 'all_rejected'
    assert funnel.zona_rejected == _PAGE_SIZE


async def test_funnel_reports_all_duplicates_stop(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch, no_cap: None,
) -> None:
    first = _page(1000, _PAGE_SIZE)
    _stub_pages(service, monkeypatch, [first, list(first)])

    _, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

    assert funnel.stop_reason == 'all_duplicates'
    assert funnel.duplicates == _PAGE_SIZE


async def test_funnel_reports_cap_reached_stop(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, 'ZONAPROP_MAX_RESULTS', 30)
    _stub_pages(service, monkeypatch, [_page(1000, _PAGE_SIZE), _page(2000, _PAGE_SIZE)])

    results, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

    assert len(results) == 30
    assert funnel.stop_reason == 'cap_reached'


async def test_funnel_carries_the_search_url(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch, no_cap: None,
) -> None:
    """Without the URL the numbers are unattributable across a fan-out."""
    _stub_pages(service, monkeypatch, [_page(1000, 12)])

    _, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

    assert funnel.search_url.startswith('https://www.zonaprop.com.ar/')


async def test_scrape_source_logs_the_funnel(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch, no_cap: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`scrape_source` keeps returning a plain list — the funnel reaches the
    operator through the log, not through a changed return type."""
    mixed = (
        [_item(i) for i in range(1000, 1010)]
        + [_off_zona(i) for i in range(2000, 2010)]
        + [_no_price(i) for i in range(3000, 3010)]
    )
    _stub_pages(service, monkeypatch, [mixed, []])

    async def _noop_progress(source: str, status: str, count: int) -> None:
        return None

    with caplog.at_level('INFO', logger='app.services.apify'):
        results = await service.scrape_source('zonaprop', _filters(), _noop_progress)

    assert len(results) == 10
    blob = ' '.join(r.getMessage() for r in caplog.records)
    assert 'zonaprop funnel' in blob
    assert 'zona_rejected=10' in blob
    assert 'no_price=10' in blob
    assert 'stop=empty_page' in blob
