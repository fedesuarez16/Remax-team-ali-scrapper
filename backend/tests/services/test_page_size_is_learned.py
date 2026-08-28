"""Don't assume how many listings ZonaProp puts on a page — measure it.

`_ZP_PAGE_SIZE = 30` was a guess baked into the stop rule: any page returning
fewer than 30 items was read as "the last one". If the portal actually serves
20 per page, page 1 comes back short of the guess, pagination stops on the
FIRST page, and a five-page listing yields one page — the reported "32 results
where the portal shows ~300".

The first page tells us the real page size for free. Anything shorter than
THAT is genuinely the end.
"""
from typing import Any

import pytest

from app.models.property import ScrapingFilters
from app.services.apify import ApifyService

# These exercise the Apify actor path, kept as the documented fallback
# (`ZONAPROP_USE_APIFY=true`). Production reads ZonaProp directly.
pytestmark = pytest.mark.usefixtures('apify_zonaprop')



@pytest.fixture()
def service() -> ApifyService:
    return ApifyService(api_token='dummy-token')


@pytest.fixture(autouse=True)
def no_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, 'ZONAPROP_MAX_RESULTS', 0)


def _item(i: int) -> dict[str, Any]:
    return {
        'title': f'Casa {i}', 'url': f'https://z/{i}', 'listingId': str(i),
        'neighborhood': 'La Plata', 'city': 'La Plata',
        'address': f'Calle {i}', 'propertyType': 'house',
        'price': 250000, 'currency': 'USD',
    }


def _filters() -> ScrapingFilters:
    return ScrapingFilters(zona='La Plata, La Plata', tipos_propiedad=['casa'])


def _serve(service: ApifyService, monkeypatch: pytest.MonkeyPatch,
           sizes: list[int]) -> list[str]:
    """Serves pages of the given sizes, then empties. Returns requested URLs."""
    urls: list[str] = []
    counter = iter(range(10_000, 99_999))

    async def fake_run(src: str, actor: str, input_data: dict) -> list:
        urls.append(input_data['searchUrl'])
        idx = len(urls) - 1
        n = sizes[idx] if idx < len(sizes) else 0
        return [_item(next(counter)) for _ in range(n)]

    monkeypatch.setattr(service, '_run_actor', fake_run)
    return urls


async def test_a_twenty_item_portal_still_paginates(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE bug: five pages of 20 used to stop after the first because 20 < 30."""
    urls = _serve(service, monkeypatch, [20, 20, 20, 20, 20])

    results, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

    assert len(results) == 100
    assert len(urls) == 6  # five real pages, then the empty one that ends it


async def test_a_short_page_relative_to_the_learned_size_still_stops(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls = _serve(service, monkeypatch, [20, 20, 7])

    results, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

    assert len(results) == 47
    assert funnel.stop_reason == 'short_page'
    assert len(urls) == 3  # no wasted run after a genuinely short page


async def test_the_thirty_item_case_is_unchanged(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls = _serve(service, monkeypatch, [30, 30, 12])

    results, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

    assert len(results) == 72
    assert funnel.stop_reason == 'short_page'
    assert len(urls) == 3


async def test_a_single_short_page_is_still_the_whole_listing(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Page 1 is all there is. It defines the page size, so we cannot call it
    short — one extra request confirms the end rather than guessing."""
    urls = _serve(service, monkeypatch, [12])

    results, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

    assert len(results) == 12
    assert funnel.stop_reason == 'empty_page'
    assert len(urls) == 2


async def test_the_cap_still_binds_with_a_smaller_page_size(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 20-item page must not let the 200-item cap turn into 140 — the page
    ceiling has to be recomputed from the size we measured."""
    from app.core.config import settings
    monkeypatch.setattr(settings, 'ZONAPROP_MAX_RESULTS', 200)
    _serve(service, monkeypatch, [20] * 15)

    results, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

    assert len(results) == 200
    assert funnel.stop_reason == 'cap_reached'
