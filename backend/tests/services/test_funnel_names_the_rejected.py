"""When the guard rejects most of a page, the log must say WHAT it rejected.

`zona_rejected=83` answers "how many" and leaves the only useful question
open: were those 83 listings from somewhere else (the portal served the wrong
page), or were they the right place under a label the guard does not
recognise? Those demand opposite fixes — retry the slug, versus loosen the
guard — and the number alone cannot tell them apart.

The portal's own locality label for the rejected items is what separates them,
so the funnel carries a sample of the distinct values it threw away.
"""
from typing import Any

import pytest

from app.models.property import ScrapingFilters
from app.services.apify import ApifyService, ZonaPropFunnel, ZonaPropPage

# These exercise the Apify actor path, kept as the documented fallback
# (`ZONAPROP_USE_APIFY=true`). Production reads ZonaProp directly.
pytestmark = pytest.mark.usefixtures('apify_zonaprop')



def _item(i: int, hood: str, city: str = 'La Plata') -> dict[str, Any]:
    return {
        'title': f'Casa {i}', 'url': f'https://z/{i}', 'listingId': str(i),
        'neighborhood': hood, 'city': city, 'address': f'Calle {i}',
        'propertyType': 'house', 'price': 320000, 'currency': 'USD',
    }


def _filters() -> ScrapingFilters:
    return ScrapingFilters(zona='La Plata, La Plata', tipos_propiedad=['casa'])


@pytest.fixture()
def service() -> ApifyService:
    return ApifyService(api_token='dummy-token')


@pytest.fixture(autouse=True)
def no_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, 'ZONAPROP_MAX_RESULTS', 0)


class TestTheSample:
    def test_it_holds_distinct_labels(self):
        page = ZonaPropPage(page=1, raw=3)
        for hood in ('Plaza Italia', 'Plaza Italia', 'Barrio Norte'):
            page.note_rejected(hood)
        assert page.rejected_zonas == {'Plaza Italia': 2, 'Barrio Norte': 1}

    def test_an_unlabelled_item_is_recorded_as_such(self):
        """An empty `neighborhood` is itself a finding — it means the guard
        fell back to free text."""
        page = ZonaPropPage(page=1, raw=1)
        page.note_rejected('')
        assert page.rejected_zonas == {'(sin barrio)': 1}

    def test_it_is_bounded(self):
        """A nationwide dump has hundreds of distinct localities; the log line
        must stay readable."""
        page = ZonaPropPage(page=1, raw=500)
        for i in range(500):
            page.note_rejected(f'Barrio {i}')
        assert len(page.rejected_zonas) <= 12


class TestItReachesTheLog:
    async def test_the_summary_names_what_was_thrown_away(
        self, service: ApifyService, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The exact open question: 8 kept out of 91. Are the other 83 from
        City Bell (wrong page) or from Plaza Italia (right page, wrong
        label)? The line now says which."""
        page = (
            [_item(i, 'La Plata') for i in range(8)]
            + [_item(100 + i, 'Plaza Italia') for i in range(12)]
        )

        async def fake_run(src: str, actor: str, input_data: dict) -> list:
            return page if '-pagina-' not in input_data['searchUrl'] else []

        monkeypatch.setattr(service, '_run_actor', fake_run)

        with caplog.at_level('INFO', logger='app.services.apify'):
            results, funnel = await service._scrape_zonaprop_paginated('actor', _filters())

        assert len(results) == 8
        assert funnel.zona_rejected == 12
        blob = ' '.join(r.getMessage() for r in caplog.records)
        assert 'rechazados=[' in blob
        assert 'Plaza Italia' in blob

    async def test_a_clean_page_adds_no_noise(
        self, service: ApifyService, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        async def fake_run(src: str, actor: str, input_data: dict) -> list:
            return ([_item(i, 'La Plata') for i in range(20)]
                    if '-pagina-' not in input_data['searchUrl'] else [])

        monkeypatch.setattr(service, '_run_actor', fake_run)

        with caplog.at_level('INFO', logger='app.services.apify'):
            await service._scrape_zonaprop_paginated('actor', _filters())

        assert 'rechazados=[' not in ' '.join(r.getMessage() for r in caplog.records)


def test_the_funnel_merges_the_sample_across_pages() -> None:
    f = ZonaPropFunnel(search_url='https://z/x')
    p1 = ZonaPropPage(page=1, raw=2)
    p1.note_rejected('Plaza Italia')
    p2 = ZonaPropPage(page=2, raw=2)
    p2.note_rejected('Plaza Italia')
    p2.note_rejected('Villa Elvira')
    f.pages.extend([p1, p2])

    assert f.rejected_zonas == {'Plaza Italia': 2, 'Villa Elvira': 1}
