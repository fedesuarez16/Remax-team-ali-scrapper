"""A page that came back stunted deserves a second ask before we give up.

Observed live (`casas en city bell`, portal showing 77 results across 3 pages):

    per_page=[p1:30->28, p2:5->0]  duplicates=5  stop=all_duplicates

Page 2 returned FIVE items and every one had already appeared on page 1 — the
actor re-read page 1 and died early, in a session that also logged four
ABORTED Apify runs and a 403. Our loop read "nothing new" as "listing over"
and dropped pages 2 AND 3 on the floor.

The retry is deliberately narrow, because every attempt is a PAID actor run:

  * SHORT and sterile  → the actor was cut off mid-page. Ask once more.
  * FULL and sterile   → an out-of-range `-pagina-N` bounced back to page 1.
    That is what the end of a listing looks like. Do not pay again.
  * EMPTY              → same, and cheaper to trust.
  * SHORT with new items → the ordinary last page.
"""
from typing import Any

import pytest

from app.models.property import ScrapingFilters
from app.services.apify import ApifyService

# These exercise the Apify actor path, kept as the documented fallback
# (`ZONAPROP_USE_APIFY=true`). Production reads ZonaProp directly.
pytestmark = pytest.mark.usefixtures('apify_zonaprop')


_PAGE = 30


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
        'neighborhood': 'City Bell', 'city': 'La Plata',
        'address': f'Calle {i}', 'propertyType': 'house',
        'price': 450_000, 'currency': 'USD',
    }


def _filters() -> ScrapingFilters:
    return ScrapingFilters(zona='City Bell, La Plata', zona_pedida='City Bell, La Plata',
                           tipos_propiedad=['casa'])


def _serve(service: ApifyService, monkeypatch: pytest.MonkeyPatch,
           script: list[list[dict[str, Any]]]) -> list[str]:
    """Answers the Nth actor call with `script[N]`, then empties."""
    urls: list[str] = []

    async def fake_run(src: str, actor: str, input_data: dict) -> list:
        i = len(urls)
        urls.append(input_data['searchUrl'])
        return script[i] if i < len(script) else []

    monkeypatch.setattr(service, '_run_actor', fake_run)
    return urls


P1 = [_item(i) for i in range(1000, 1030)]
P2 = [_item(i) for i in range(2000, 2030)]
P3 = [_item(i) for i in range(3000, 3012)]


async def test_a_stunted_page_is_asked_again_and_the_listing_continues(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE reported case, with the retry succeeding: 5 stale items, then the
    real page 2, then page 3."""
    urls = _serve(service, monkeypatch, [P1, P1[:5], P2, P3])

    results, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

    assert len(results) == 30 + 30 + 12
    assert urls[1] == urls[2]           # the same page, asked twice
    assert '-pagina-2' in urls[1]
    assert funnel.stop_reason == 'short_page'


async def test_it_gives_up_after_one_retry(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actor is simply broken — one extra run, not an infinite spend."""
    urls = _serve(service, monkeypatch, [P1, P1[:5], P1[:5], P2])

    results, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

    assert len(results) == 30
    assert len(urls) == 3
    assert funnel.stop_reason == 'all_duplicates'


async def test_a_full_page_of_duplicates_is_not_retried(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An out-of-range `-pagina-N` serving page 1 again IS the end."""
    urls = _serve(service, monkeypatch, [P1, P1])

    _, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

    assert len(urls) == 2
    assert funnel.stop_reason == 'all_duplicates'


async def test_an_empty_page_is_not_retried(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls = _serve(service, monkeypatch, [P1, []])

    _, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

    assert len(urls) == 2
    assert funnel.stop_reason == 'empty_page'


async def test_a_short_page_with_new_items_is_the_ordinary_last_one(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls = _serve(service, monkeypatch, [P1, P3])

    results, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

    assert len(results) == 42
    assert len(urls) == 2
    assert funnel.stop_reason == 'short_page'


async def test_the_funnel_shows_both_attempts(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A silent retry would hide that a page had to be asked twice — which is
    the symptom that says the actor is unhealthy."""
    _serve(service, monkeypatch, [P1, P1[:5], P2, []])

    with caplog.at_level('INFO', logger='app.services.apify'):
        await service._scrape_zonaprop_paginated('actor', _filters())

    blob = ' '.join(r.getMessage() for r in caplog.records)
    assert 'p2:5->0' in blob
    assert 'p2#2:30->30' in blob
