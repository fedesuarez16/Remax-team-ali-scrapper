"""`not results` cannot detect a ZonaProp nationwide redirect.

An unknown slug does not 404 — the portal answers with a nationwide listing.
The zona guard then throws out almost all of it, but "almost" is the problem:
a nationwide dump that happens to carry two matching listings comes back
non-empty, so the composite-slug retry gated on `not results` stays asleep and
the search settles for two results when the portal has forty.

The funnel already measures the thing that actually distinguishes the two
cases — what fraction of the page the guard rejected. A correct slug keeps
nearly everything (the guard is a redirect detector, not a filter); a
redirect keeps nearly nothing.
"""
from typing import Any

import pytest

from app.models.property import RawProperty, ScrapingFilters
from app.services.apify import ApifyService, ZonaPropFunnel, ZonaPropPage

# These exercise the Apify actor path, kept as the documented fallback
# (`ZONAPROP_USE_APIFY=true`). Production reads ZonaProp directly.
pytestmark = pytest.mark.usefixtures('apify_zonaprop')



def _funnel(raw: int, zona_rejected: int, kept: int) -> ZonaPropFunnel:
    f = ZonaPropFunnel(search_url='https://z/x')
    f.pages.append(ZonaPropPage(page=1, raw=raw, zona_rejected=zona_rejected, kept=kept))
    return f


class TestRedirectSuspected:
    def test_a_mostly_rejected_page_is_a_redirect(self):
        """38 of 40 thrown out: the shape of a nationwide dump."""
        assert _funnel(raw=40, zona_rejected=38, kept=2).redirect_suspected

    def test_a_clean_page_is_not(self):
        assert not _funnel(raw=40, zona_rejected=0, kept=40).redirect_suspected

    def test_a_page_lost_to_missing_prices_is_not(self):
        """Price-less listings are not evidence of a wrong slug."""
        f = ZonaPropFunnel(search_url='https://z/x')
        f.pages.append(ZonaPropPage(page=1, raw=40, no_price=30, kept=10))
        assert not f.redirect_suspected

    def test_an_empty_result_set_is_not_reported_as_a_redirect(self):
        """Nothing came back at all — that is `empty_page`, a different fault,
        and dividing by zero here would crash the scrape."""
        assert not _funnel(raw=0, zona_rejected=0, kept=0).redirect_suspected

    def test_a_funnel_with_no_pages_is_not_a_redirect(self):
        """`stop=actor_error` before the first request even landed."""
        assert not ZonaPropFunnel(search_url='https://z/x').redirect_suspected

    def test_a_later_page_drifting_is_not_a_redirect(self):
        """Page 1 clean, page 2 all rejected: ordinary end-of-listing drift, an
        out-of-range `-pagina-N` bouncing to a nationwide page. The slug was
        right, so re-running the whole search would burn paid pages for
        nothing."""
        f = ZonaPropFunnel(search_url='https://z/x')
        f.pages.append(ZonaPropPage(page=1, raw=30, zona_rejected=0, kept=30))
        f.pages.append(ZonaPropPage(page=2, raw=30, zona_rejected=30, kept=0))
        assert not f.redirect_suspected

    def test_a_bad_slug_is_caught_on_page_one_even_if_later_pages_are_clean(self):
        f = ZonaPropFunnel(search_url='https://z/x')
        f.pages.append(ZonaPropPage(page=1, raw=30, zona_rejected=28, kept=2))
        f.pages.append(ZonaPropPage(page=2, raw=30, zona_rejected=0, kept=30))
        assert f.redirect_suspected


@pytest.fixture()
def service() -> ApifyService:
    return ApifyService(api_token='dummy-token')


async def _noop(source: str, status: str, count: int) -> None:
    return None


def _prop(i: int) -> RawProperty:
    return RawProperty(
        fuente='zonaprop', titulo=f'Depto {i}', direccion=f'Calle 13 nro {i}',
        precio=250000.0, moneda='USD', tipo_operacion='venta',
        tipo_propiedad='departamento',
    )


async def test_retry_fires_on_a_partial_redirect(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported case: the composite slug redirects nationwide and two City
    Bell listings survive the guard. The plain slug must still be tried."""
    seen: list[str] = []

    async def fake(actor_id: str, filters: ScrapingFilters) -> Any:
        zona = filters.zona or ''
        seen.append(zona)
        if zona == 'City Bell':
            return [_prop(i) for i in range(40)], _funnel(40, 0, 40)
        return [_prop(i) for i in range(2)], _funnel(40, 38, 2)

    monkeypatch.setattr(service, '_scrape_zonaprop_paginated', fake)

    results = await service._scrape_source_once(
        'zonaprop', ScrapingFilters(zona='City Bell, La Plata'), _noop,
    )

    assert seen == ['City Bell, La Plata', 'City Bell']
    assert len(results) == 40


async def test_a_clean_first_pass_is_not_retried(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A composite slug the portal DOES know must not cost a second paid run."""
    seen: list[str] = []

    async def fake(actor_id: str, filters: ScrapingFilters) -> Any:
        seen.append(filters.zona or '')
        return [_prop(i) for i in range(40)], _funnel(40, 0, 40)

    monkeypatch.setattr(service, '_scrape_zonaprop_paginated', fake)

    await service._scrape_source_once(
        'zonaprop', ScrapingFilters(zona='City Bell, La Plata'), _noop,
    )

    assert seen == ['City Bell, La Plata']


async def test_the_retry_never_returns_less_than_the_first_pass(
    service: ApifyService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the plain slug turns out to be the worse one, keep what we had —
    the retry is an attempt to do better, never a commitment to its result."""
    async def fake(actor_id: str, filters: ScrapingFilters) -> Any:
        if filters.zona == 'City Bell':
            return [], _funnel(0, 0, 0)
        return [_prop(i) for i in range(2)], _funnel(40, 38, 2)

    monkeypatch.setattr(service, '_scrape_zonaprop_paginated', fake)

    results = await service._scrape_source_once(
        'zonaprop', ScrapingFilters(zona='City Bell, La Plata'), _noop,
    )

    assert len(results) == 2
