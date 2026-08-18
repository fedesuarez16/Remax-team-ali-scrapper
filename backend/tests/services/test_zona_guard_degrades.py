"""The zona guard accepts every link of the zona's candidate chain.

A barrio-level guard was the amplifier behind the "casco de La Plata" zero:
even on the portals that DID return the right La Plata listings, nothing in a
listing body says "Casco Urbano" — a real address reads "calle 47 e/ 12 y 13,
La Plata". Requiring the literal barrio phrase rejected all of them.

Widening the guard to the chain (`Casco Urbano, La Plata` → also `La Plata`)
is what lets a degraded search actually keep its results. Precision is not
lost outright: the composite phrase still requires EVERY comma-part, so a
homonym barrio in another province stays rejected.
"""
from app.models.property import ScrapingFilters
from app.services.apify import _guard_phrases, _item_matches_zona


def _item(**kw) -> dict:
    return {'neighborhood': '', 'city': '', 'address': '', 'title': '',
            'description': '', **kw}


class TestGuardPhrasesIncludeTheChain:
    def test_chat_path_adds_the_containing_localidad(self):
        f = ScrapingFilters(zona='Casco Urbano, La Plata')
        assert _guard_phrases(f) == {'Casco Urbano, La Plata', 'La Plata'}

    def test_bare_zona_is_unchanged(self):
        """Pre-existing single-phrase behaviour must not move."""
        assert _guard_phrases(ScrapingFilters(zona='Palermo')) == {'Palermo'}

    def test_does_not_degrade_into_a_whole_province(self):
        f = ScrapingFilters(zona='Los Hornos, Buenos Aires')
        assert _guard_phrases(f) == {'Los Hornos, Buenos Aires'}

    def test_map_path_degrades_every_seed(self):
        f = ScrapingFilters(zona='Villa Elisa, La Plata',
                            zonas=['Villa Elisa, La Plata'],
                            localidades=['Villa Elisa, La Plata'])
        assert _guard_phrases(f) == {'Villa Elisa, La Plata', 'La Plata'}


class TestListingsSurviveTheDegradedGuard:
    def test_localidad_only_address_is_kept(self):
        """The exact shape that was being dropped: a real casco address that
        never names the barrio."""
        f = ScrapingFilters(zona='Casco Urbano, La Plata')
        item = _item(address='calle 47 e/ 12 y 13', city='La Plata')
        assert _item_matches_zona(item, _guard_phrases(f)) is True

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
