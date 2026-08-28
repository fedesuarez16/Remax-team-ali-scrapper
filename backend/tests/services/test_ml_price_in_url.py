"""Let MercadoLibre filter by price, like ZonaProp already does.

Without it we paged the ENTIRE unfiltered listing — MercadoLibre caps around
2000 items, 48 per page, so ~42 pages of roughly 2 MB each through a metered
residential proxy — and then threw nearly all of it away downstream. The
matching listings sit wherever they sit, so a search reported "muy pocos"
while the portal showed ten pages of them.

The portal's own URL for the same search, pasted from the browser:

    /casas/venta/bsas-gba-sur/la-plata/la-plata/_PriceRange_99000USD-150000USD_NoIndex_True
    /casas/venta/.../_Desde_49_PriceRange_99000USD-150000USD_NoIndex_True
    /casas/venta/.../_Desde_97_PriceRange_99000USD-150000USD_NoIndex_True

`_Desde_` comes FIRST and `_PriceRange_` after it, in one path element.
"""
from app.models.property import ScrapingFilters
from app.services.apify import _ml_search_urls


def _filters(**kw) -> ScrapingFilters:
    kw.setdefault('zona', 'La Plata')
    kw.setdefault('tipo_operacion', 'venta')
    kw.setdefault('tipos_propiedad', ['casa'])
    return ScrapingFilters(**kw)


def _urls(n: int, **kw) -> list[str]:
    gen = _ml_search_urls(_filters(**kw), max_pages=n)
    return [next(gen) for _ in range(n)]


_BASE = 'https://inmuebles.mercadolibre.com.ar/casas/venta/la-plata'


class TestTheRangeReachesTheUrl:
    def test_page_one_carries_the_price(self):
        assert _urls(1, precio_min=99_000, precio_max=150_000) == [
            f'{_BASE}/_PriceRange_99000USD-150000USD']

    def test_later_pages_keep_it_after_the_offset(self):
        """`_Desde_49` first, `_PriceRange_` next, `_NoIndex_True` last — the
        portal's own order. The trailing token is REQUIRED: without it the
        offset is ignored and page 1 is served again."""
        urls = _urls(3, precio_min=99_000, precio_max=150_000)
        assert urls[1] == f'{_BASE}/_Desde_49_PriceRange_99000USD-150000USD_NoIndex_True'
        assert urls[2] == f'{_BASE}/_Desde_97_PriceRange_99000USD-150000USD_NoIndex_True'

    def test_the_offsets_match_the_portal(self):
        """48 cards a page: 49, 97 — copied off the real pagination links."""
        urls = _urls(3)
        assert urls[1] == f'{_BASE}/_Desde_49_NoIndex_True'
        assert urls[2] == f'{_BASE}/_Desde_97_NoIndex_True'

    def test_floats_are_rendered_as_integers(self):
        """`99000.0USD` is not a filter the portal understands."""
        url = _urls(1, precio_min=99_000.0, precio_max=150_000.0)[0]
        assert '_PriceRange_99000USD-150000USD' in url
        assert '.0' not in url


class TestPartialAndAbsentRanges:
    def test_no_price_leaves_the_url_alone(self):
        assert _urls(2) == [_BASE, f'{_BASE}/_Desde_49_NoIndex_True']

    def test_a_ceiling_alone_starts_at_zero(self):
        assert '_PriceRange_0USD-150000USD' in _urls(1, precio_max=150_000)[0]

    def test_a_floor_alone_has_no_second_bound(self):
        """Verified live on a 179-listing page: `_PriceRange_99000USD` → 82.
        Writing it `99000USD-*USD` returns all 179 — it does not filter, it
        only looks like it does."""
        assert _urls(1, precio_min=99_000)[0].endswith('_PriceRange_99000USD')

    def test_an_inverted_range_is_dropped(self):
        """A floor above the ceiling is an upstream parse error, not a filter
        — sending it would return nothing at all."""
        assert _urls(1, precio_min=200_000, precio_max=150_000)[0] == _BASE
