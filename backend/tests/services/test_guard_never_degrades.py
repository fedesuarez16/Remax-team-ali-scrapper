"""Widening the QUERY must not widen the FILTER.

`scrape_source` walks a candidate chain — "City Bell, La Plata" → "City Bell"
→ "La Plata" — rewriting `filters.zona` before each attempt. The URL slug is
built from that rewrite, and so was the zona guard. So the moment the chain
degraded to the partido, the guard degraded with it and the search happily
returned every La Plata listing as the answer to "departamentos en City Bell".

The slug is a RETRIEVAL STRATEGY: widening it to find a listing page the
portal actually serves is legitimate. The guard is the CORRECTNESS CONTRACT:
what the user asked for. `zona_pedida` carries the original request through
the walk untouched, so a degraded pass either finds listings from the barrio
the user named, or honestly returns nothing.
"""
from typing import Any

import pytest

from app.models.property import ScrapingFilters
from app.services.apify import (
    ApifyService, ZonaPropFunnel, ZonaPropPage, _guard_phrases, _item_matches_zona,
)


def _item(**kw) -> dict[str, Any]:
    return {'neighborhood': '', 'city': '', 'address': '', 'title': '',
            'description': '', **kw}


class TestGuardPhrasesHonourTheOriginalRequest:
    def test_a_degraded_zona_still_guards_on_what_was_asked(self):
        """Mid-walk: `zona` has been widened to the partido, `zona_pedida`
        still holds the barrio."""
        f = ScrapingFilters(zona='La Plata', zona_pedida='City Bell, La Plata')
        assert _guard_phrases(f) == {'City Bell, La Plata'}

    def test_without_it_nothing_changes(self):
        f = ScrapingFilters(zona='City Bell, La Plata')
        assert _guard_phrases(f) == {'City Bell, La Plata'}

    def test_map_path_is_untouched(self):
        """`localidades` means the polygon is the precision gate; that branch
        keeps its deliberate union across seeds."""
        f = ScrapingFilters(zona='City Bell', zonas=['City Bell', 'Gonnet'],
                            localidades=['City Bell, La Plata'],
                            zona_pedida='City Bell, La Plata')
        assert _guard_phrases(f) == {'City Bell', 'Gonnet', 'City Bell, La Plata'}


class TestTheWholePointOfIt:
    def test_a_la_plata_listing_is_rejected_on_the_degraded_pass(self):
        """THE complaint: "si le digo city bell, quiero las de city bell"."""
        f = ScrapingFilters(zona='La Plata', zona_pedida='City Bell, La Plata')
        casco = _item(neighborhood='La Plata', city='La Plata',
                      address='Calle 7 e/ 49 y 50')
        assert not _item_matches_zona(casco, _guard_phrases(f))

    def test_a_city_bell_listing_found_on_the_wider_page_is_kept(self):
        """The upside of widening the slug: the partido's listing page still
        contains the barrio's properties, and those we keep."""
        f = ScrapingFilters(zona='La Plata', zona_pedida='City Bell, La Plata')
        cb = _item(neighborhood='City Bell', city='La Plata',
                   address='Calle 13 e/ 470 y 471')
        assert _item_matches_zona(cb, _guard_phrases(f))

    def test_villa_elisa_does_not_leak_into_a_city_bell_search(self):
        f = ScrapingFilters(zona='La Plata', zona_pedida='City Bell, La Plata')
        ve = _item(neighborhood='Villa Elisa', city='La Plata',
                   address='Calle 7 nro 100')
        assert not _item_matches_zona(ve, _guard_phrases(f))


@pytest.fixture()
def service() -> ApifyService:
    return ApifyService(api_token='dummy-token')


async def _noop(source: str, status: str, count: int) -> None:
    return None


class TestScrapeSourceStampsIt:
    async def test_every_candidate_carries_the_original_request(
        self, service: ApifyService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen: list[tuple[str, str]] = []

        async def fake(actor_id: str, filters: ScrapingFilters) -> Any:
            seen.append((filters.zona or '', filters.zona_pedida or ''))
            f = ZonaPropFunnel(search_url='https://z/x')
            f.pages.append(ZonaPropPage(page=1, raw=0))
            return [], f

        monkeypatch.setattr(service, '_scrape_zonaprop_paginated', fake)

        await service.scrape_source(
            'zonaprop', ScrapingFilters(zona='City Bell, La Plata'), _noop,
        )

        assert [z for z, _ in seen] == ['City Bell, La Plata', 'City Bell', 'La Plata']
        assert {p for _, p in seen} == {'City Bell, La Plata'}

    async def test_an_explicit_zona_pedida_is_not_overwritten(
        self, service: ApifyService, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A caller that already scoped the guard keeps its scoping."""
        seen: list[str] = []

        async def fake(actor_id: str, filters: ScrapingFilters) -> Any:
            seen.append(filters.zona_pedida or '')
            f = ZonaPropFunnel(search_url='https://z/x')
            f.pages.append(ZonaPropPage(page=1, raw=0))
            return [], f

        monkeypatch.setattr(service, '_scrape_zonaprop_paginated', fake)

        await service.scrape_source(
            'zonaprop',
            ScrapingFilters(zona='City Bell, La Plata', zona_pedida='Gonnet'),
            _noop,
        )

        assert set(seen) == {'Gonnet'}
