"""Push the ambientes floor into the ZonaProp URL.

ZonaProp throttles after ~10 pages, so a search gets ~300 of a 1999-listing
set no matter what the cap says. Which 300 is therefore the whole game, and
today they are drawn from everything: ask for "2 dormitorios" and the quota
fills with monoambientes.

Grammar verified live on `departamentos-venta-la-plata-la-plata-…-60000-90000-dolar`
(1999 listings unfiltered):

    -2-ambientes-           → rooms min=2 max=2   → 1012   exact
    -mas-de-2-ambientes-    → rooms min=2, no max → 1703   open floor
    -mas-de-3-ambientes-    → rooms min=3, no max →  691

A RANGE does not exist, and pretending it does is the trap: `-1-3-habitaciones-`
returns 122 and `appliedFilters` shows `min=3, max=3` — the portal silently
drops the "1-" and reads the last number. It looks like a filter and is a
different one, exactly like `_PriceRange_…-*USD` did.

`ambientes` is also NOT `habitaciones`: the same page offers `-2-habitaciones-`
(bedrooms, 523) alongside `-2-ambientes-` (rooms, 1012). Our
`ScrapingFilters.ambientes_min` is documented as a floor on AMBIENTES — the
extractor turns "3 dormitorios" into `ambientes_min=3` precisely because a
3-bedroom home has 4+ ambientes — so `rooms` is the matching dimension.
"""
from app.models.property import ScrapingFilters
from app.services.apify import _zonaprop_search_url

_BASE = 'https://www.zonaprop.com.ar/departamentos-venta-la-plata'


def _url(**kw) -> str:
    kw.setdefault('zona', 'La Plata')
    kw.setdefault('tipo_operacion', 'venta')
    kw.setdefault('tipos_propiedad', ['departamento'])
    return _zonaprop_search_url(ScrapingFilters(**kw))


class TestTheFloorReachesTheUrl:
    def test_a_minimum_becomes_an_open_floor(self):
        assert _url(ambientes_min=2) == f'{_BASE}-mas-de-2-ambientes.html'

    def test_it_sits_before_the_price(self):
        """Order taken from the portal's own URLs."""
        assert _url(ambientes_min=3, precio_min=60_000, precio_max=90_000) == (
            f'{_BASE}-mas-de-3-ambientes-60000-90000-dolar.html')

    def test_an_exact_count_uses_the_exact_form(self):
        assert _url(ambientes_min=2, ambientes_max=2) == f'{_BASE}-2-ambientes.html'


class TestWhatThePortalCannotExpress:
    def test_a_range_degrades_to_its_floor(self):
        """`-1-3-ambientes-` is not a range — the portal reads only the last
        number. The floor is WIDER than asked, so nothing wanted is lost."""
        assert _url(ambientes_min=1, ambientes_max=3) == f'{_BASE}-mas-de-1-ambientes.html'

    def test_a_ceiling_alone_is_dropped(self):
        """No "up to N" form exists; inventing one would filter by something
        else entirely."""
        assert _url(ambientes_max=3) == f'{_BASE}.html'

    def test_no_rooms_leaves_the_url_alone(self):
        assert _url() == f'{_BASE}.html'


class TestBadInputIsNotSent:
    def test_an_inverted_range_is_dropped(self):
        assert _url(ambientes_min=5, ambientes_max=2) == f'{_BASE}.html'

    def test_zero_is_not_a_filter(self):
        assert _url(ambientes_min=0) == f'{_BASE}.html'

    def test_a_float_renders_as_an_integer(self):
        """`2.0` in the slug is a 404."""
        assert '.0' not in _url(ambientes_min=2.0)


class TestDormitoriosUseTheBedroomsFilter:
    """Verified live on the same 1999-listing page:

        -2-habitaciones-          bedrooms min=2 max=2  →  523
        -mas-de-2-habitaciones-   bedrooms min=2, open  →  653
        -2-ambientes-             rooms    min=2 max=2  → 1012

    "2 dormitorios" asked as ambientes is three times wider than the question:
    a 1-bedroom, 2-ambiente flat comes back as a match.
    """

    def test_an_exact_count_of_dormitorios(self):
        assert _url(dormitorios_min=2, dormitorios_max=2) == f'{_BASE}-2-habitaciones.html'

    def test_a_floor_of_dormitorios_is_open_ended(self):
        assert _url(dormitorios_min=2) == f'{_BASE}-mas-de-2-habitaciones.html'

    def test_a_range_degrades_to_its_floor(self):
        """No range form exists here either — `-1-3-habitaciones-` returns what
        `-3-habitaciones-` returns. The floor is wider, never narrower."""
        assert _url(dormitorios_min=1, dormitorios_max=3) == (
            f'{_BASE}-mas-de-1-habitaciones.html')

    def test_it_sits_before_the_price(self):
        assert _url(dormitorios_min=2, dormitorios_max=2,
                    precio_min=60_000, precio_max=90_000) == (
            f'{_BASE}-2-habitaciones-60000-90000-dolar.html')

    def test_dormitorios_win_over_ambientes(self):
        """Both filters exist on the portal but only one slug fits the URL.
        The user asked for dormitorios; that is the more specific question."""
        assert _url(dormitorios_min=2, dormitorios_max=2, ambientes_min=3) == (
            f'{_BASE}-2-habitaciones.html')

    def test_ambientes_alone_still_work(self):
        assert _url(ambientes_min=3) == f'{_BASE}-mas-de-3-ambientes.html'

    def test_a_ceiling_of_dormitorios_alone_is_dropped(self):
        assert _url(dormitorios_max=3) == f'{_BASE}.html'
