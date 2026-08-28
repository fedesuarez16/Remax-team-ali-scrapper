"""One MercadoLibre listing, one URL — whatever link led us to it.

Reported live: MercadoLibre "trae pocas y muchas repetidas mas de 2 o 3 veces".
The card's `href` is stored raw, and MercadoLibre decorates every link with the
context it was clicked from: `#position=3`, `?searchVariation=...`,
`tracking_id=...`. `_ml_zona_slugs` deliberately tries several slugs
(`city-bell-la-plata`, then `city-bell`), so the SAME listing comes back under
each one wearing a different URL.

That defeats both dedup layers at once — the scraper's own `seen` set is keyed
on the URL, and graph dedup treats two distinct `url_origen` from one `fuente`
as two properties. The listing id is the identity; the tracking is noise.
"""
import pytest

from app.services.apify import _ml_canonical_url

_ID = 'MLA-1234567890'


class TestStrippingTheTracking:
    def test_a_fragment_is_dropped(self):
        url = (f'https://casa.mercadolibre.com.ar/{_ID}-casa-en-city-bell-_JM'
               '#position=3&search_layout=stack&type=item&tracking_id=abc-123')
        assert _ml_canonical_url(url) == (
            f'https://casa.mercadolibre.com.ar/{_ID}-casa-en-city-bell-_JM')

    def test_a_query_string_is_dropped(self):
        url = (f'https://articulo.mercadolibre.com.ar/{_ID}-casa-_JM'
               '?searchVariation=987&pdp_filters=category')
        assert _ml_canonical_url(url) == (
            f'https://articulo.mercadolibre.com.ar/{_ID}-casa-_JM')

    def test_both_at_once(self):
        url = f'https://casa.mercadolibre.com.ar/{_ID}-x-_JM?a=1#position=9'
        assert _ml_canonical_url(url) == f'https://casa.mercadolibre.com.ar/{_ID}-x-_JM'


class TestTheSameListingCollapses:
    def test_two_slugs_yield_one_url(self):
        """THE bug: the same property found under two zona slugs."""
        a = (f'https://casa.mercadolibre.com.ar/{_ID}-casa-_JM'
             '#position=1&search_layout=stack&tracking_id=aaa')
        b = (f'https://casa.mercadolibre.com.ar/{_ID}-casa-_JM'
             '#position=14&search_layout=grid&tracking_id=bbb')
        assert _ml_canonical_url(a) == _ml_canonical_url(b)

    def test_a_trailing_slash_does_not_split_a_listing_in_two(self):
        a = f'https://casa.mercadolibre.com.ar/{_ID}-casa-_JM'
        b = f'https://casa.mercadolibre.com.ar/{_ID}-casa-_JM/'
        assert _ml_canonical_url(a) == _ml_canonical_url(b)

    def test_different_listings_stay_different(self):
        a = f'https://casa.mercadolibre.com.ar/{_ID}-casa-_JM'
        b = 'https://casa.mercadolibre.com.ar/MLA-9999999999-casa-_JM'
        assert _ml_canonical_url(a) != _ml_canonical_url(b)


class TestItDoesNotBreakOnOddInput:
    def test_a_relative_href_becomes_absolute(self):
        """A bare path as `url_origen` breaks dedup and every link in the UI."""
        out = _ml_canonical_url(f'/{_ID}-casa-_JM')
        assert out.startswith('https://')
        assert _ID in out

    def test_a_url_without_an_id_just_loses_its_tracking(self):
        url = 'https://inmuebles.mercadolibre.com.ar/casas/venta/city-bell/?x=1'
        assert _ml_canonical_url(url) == (
            'https://inmuebles.mercadolibre.com.ar/casas/venta/city-bell')

    @pytest.mark.parametrize('value', ['', None])
    def test_nothing_in_nothing_out(self, value):
        assert _ml_canonical_url(value) is None
