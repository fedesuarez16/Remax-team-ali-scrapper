"""MercadoLibre nests a zona under a REGION, and there is more than one.

`/casas/venta/la-plata` redirects to `buenos-aires-interior/la-plata` — 179
listings — while the La Plata the user means is `bsas-gba-sur/la-plata`, with
2202. Measured live; the filtered search showed 58 against the portal's 455.
The flat page also renders no pagination links, so `_Desde_49` silently
re-served page 1 and the search capped at 48.

There is no way to know the region from the name, so each candidate is asked
and the one with the most listings wins. The probe uses the REAL search URL,
price filter included, so the winner's response IS page 1 and nothing is
fetched twice.
"""

import pytest

from app.services.apify import _ML_REGIONS, _ml_result_count, _ml_zona_path_candidates


class TestTheCandidatePaths:
    def test_a_bare_zona_sits_at_the_partido_level(self):
        assert _ml_zona_path_candidates('La Plata') == ['la-plata']

    def test_a_composite_nests_localidad_under_partido(self):
        """The portal's own URL is `bsas-gba-sur/la-plata/la-plata`: region,
        then PARTIDO, then localidad — the reverse of how we write it."""
        assert _ml_zona_path_candidates('City Bell, La Plata') == [
            'la-plata/city-bell', 'city-bell']

    def test_the_bare_head_is_kept_as_a_fallback(self):
        """`gonnet-la-plata` 404s while `gonnet` works — the same reason the
        flat fallback existed before."""
        assert 'gonnet' in _ml_zona_path_candidates('Gonnet, La Plata')

    def test_every_known_region_is_tried(self):
        assert 'bsas-gba-sur' in _ML_REGIONS
        assert 'buenos-aires-interior' in _ML_REGIONS
        assert 'capital-federal' in _ML_REGIONS


class TestReadingTheCount:
    @pytest.mark.parametrize('text,expected', [
        ('455 resultados', 455),
        ('2.202 resultados', 2202),
        ('1 resultado', 1),
    ])
    def test_it_parses_the_portal_counter(self, text: str, expected: int):
        html = f'<span class="ui-search-search-result__quantity-results">{text}</span>'
        assert _ml_result_count(html) == expected

    def test_no_counter_falls_back_to_counting_cards(self):
        """Some pages omit the counter; the cards are still countable."""
        html = ('<ol class="ui-search-layout">'
                + '<li class="ui-search-layout__item"></li>' * 7 + '</ol>')
        assert _ml_result_count(html) == 7

    def test_an_empty_page_is_zero(self):
        assert _ml_result_count('<html></html>') == 0
