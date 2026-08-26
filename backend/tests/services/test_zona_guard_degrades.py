"""The zona guard accepts ONLY the zona that was requested — degradation is
the candidate chain's job, not the guard's.

This file used to assert the opposite: `_guard_phrases` expanded every seed
through `zona_candidates`, so a "Casco Urbano, La Plata" search also carried
the bare phrase "La Plata". The intent was to keep listings whose address
never names the barrio ("calle 47 e/ 12 y 13, La Plata").

That backfired. `_item_matches_zona` passes on ANY phrase, so the degraded
phrase also admitted every unrelated listing from the containing partido — a
ZonaProp nationwide redirect came back looking like a successful search, and
the composite-slug retry gated on `not results` never fired. Reported
symptom: a City Bell search returning two City Bell listings and forty from
La Plata.

A tight guard also makes the barrio pass return zero, which is precisely the
signal `scrape_source` needs to walk to the next candidate — so the SLUG still
widens. What no longer widens with it is the guard: `zona_pedida` pins it to
the original request for every pass. `TestTheLocalidadOnlyListingIsNowDropped\
Everywhere` below records what that costs.
"""
from typing import Any

from app.models.property import RawProperty, ScrapingFilters
from app.services.apify import (
    ApifyService, ZonaPropFunnel, _guard_phrases, _item_matches_zona,
)


def _item(**kw) -> dict:
    return {'neighborhood': '', 'city': '', 'address': '', 'title': '',
            'description': '', **kw}


class TestGuardPhrasesAreExactlyWhatWasRequested:
    def test_chat_path_does_not_add_the_containing_localidad(self):
        f = ScrapingFilters(zona='Casco Urbano, La Plata')
        assert _guard_phrases(f) == {'Casco Urbano, La Plata'}

    def test_bare_zona_is_unchanged(self):
        """Pre-existing single-phrase behaviour must not move."""
        assert _guard_phrases(ScrapingFilters(zona='Palermo')) == {'Palermo'}

    def test_map_path_unions_seeds_without_expanding_them(self):
        """Across seeds the map path stays wide on purpose — the polygon is the
        precision gate there. What stops is each seed's fallback chain."""
        f = ScrapingFilters(zona='Villa Elisa, La Plata',
                            zonas=['Villa Elisa, La Plata'],
                            localidades=['Villa Elisa, La Plata'])
        assert _guard_phrases(f) == {'Villa Elisa, La Plata'}


class TestPrecisionTheWideGuardWasLosing:
    def test_an_unrelated_listing_from_the_partido_is_rejected(self):
        """THE regression that motivated the change: a plain La Plata listing
        had been passing a City Bell search."""
        f = ScrapingFilters(zona='City Bell, La Plata')
        item = _item(neighborhood='La Plata', city='La Plata',
                     address='Calle 7 e/ 49 y 50')
        assert _item_matches_zona(item, _guard_phrases(f)) is False

    def test_barrio_naming_listing_is_still_kept(self):
        f = ScrapingFilters(zona='Casco Urbano, La Plata')
        item = _item(neighborhood='Casco Urbano', city='La Plata')
        assert _item_matches_zona(item, _guard_phrases(f)) is True

    def test_other_partido_is_still_rejected(self):
        f = ScrapingFilters(zona='Casco Urbano, La Plata')
        item = _item(address='Av. Rivadavia 100', city='Ituzaingó')
        assert _item_matches_zona(item, _guard_phrases(f)) is False

    def test_homonym_in_another_province_is_still_rejected(self):
        """Argenprop's autocomplete really does answer "Casco Urbano" with a
        barrio in San Luis — the composite phrase is what keeps it out."""
        f = ScrapingFilters(zona='Casco Urbano, La Plata')
        item = _item(neighborhood='Casco Urbano / Historico',
                     city='San Fco del Monte de Oro', description='San Luis')
        assert _item_matches_zona(item, _guard_phrases(f)) is False


class TestTheCostOfTightening:
    def test_a_localidad_only_address_no_longer_passes_the_barrio_pass(self):
        """Stated plainly, because it is a real loss on this one pass: a casco
        address that never names the barrio is now rejected while the search
        is still scoped to the barrio. The next test shows it coming back."""
        f = ScrapingFilters(zona='Casco Urbano, La Plata')
        item = _item(address='calle 47 e/ 12 y 13', city='La Plata')
        assert _item_matches_zona(item, _guard_phrases(f)) is False


class TestTheLocalidadOnlyListingIsNowDroppedEverywhere:
    async def test_the_degraded_pass_no_longer_rescues_it(self):
        """The FULL cost of the tightening, end to end.

        The chain still widens the SLUG all the way to "La Plata" — that part
        is unchanged — but `zona_pedida` keeps the guard on "Casco Urbano,
        La Plata" for every pass. So a listing that names only the localidad
        is now rejected on the degraded pass too, and the search returns zero.

        This is the deliberate trade: "si le digo city bell, quiero las de
        city bell". A barrio the portals DO name (City Bell, Villa Elisa,
        Gonnet) gains precision. A barrio they never name in listing text
        ('Casco Urbano' is an informal name for the La Plata centre) returns
        nothing instead of the whole partido — an honest zero rather than a
        wrong answer, but a zero all the same."""
        casco = _item(address='calle 47 e/ 12 y 13', city='La Plata')
        service = ApifyService(api_token='dummy-token')
        attempted: list[str] = []

        async def fake(actor_id: str, filters: ScrapingFilters) -> Any:
            zona = filters.zona or ''
            attempted.append(zona)
            # The portal returns the same casco listing every time; only the
            # guard decides whether this pass may keep it.
            kept = [
                RawProperty(
                    fuente='zonaprop', titulo='Depto en La Plata',
                    direccion='calle 47 e/ 12 y 13', precio=120000.0,
                    moneda='USD', tipo_operacion='venta',
                    tipo_propiedad='departamento',
                )
            ] if _item_matches_zona(casco, _guard_phrases(filters)) else []
            return kept, ZonaPropFunnel(search_url=f'https://z/{zona}')

        service._scrape_zonaprop_paginated = fake  # type: ignore[method-assign]

        async def _noop(source: str, status: str, count: int) -> None:
            return None

        results = await service.scrape_source(
            'zonaprop', ScrapingFilters(zona='Casco Urbano, La Plata'), _noop,
        )

        assert attempted == ['Casco Urbano, La Plata', 'Casco Urbano', 'La Plata']
        assert len(results) == 0
