"""Reading ZonaProp's own page instead of paying an actor to do it.

The `crawlerbros/zonaprop-scraper` actor pulls the listing in ~7 s and then
spends ~85 s opening one detail page per result — which is where its Playwright
driver crashes (`TypeError: Cannot read properties of undefined (reading
'url')`), taking pagination down with it and reporting "Reached last page (1)".
Its input schema has no switch to turn that enrichment off.

Everything we normalise already rides in `window.__PRELOADED_STATE__` on the
listing page, which a residential proxy (`SCRAPER_PROXY_URL`, already
configured and already used by MercadoLibre) fetches with a plain 200.

The fixture is REAL captured output for
`casas-venta-city-bell-450000-500000-dolar.html`, trimmed to three postings —
two in City Bell and one in Grand Bell, the sub-barrio the name-matching guard
used to throw away.
"""
import json
import pathlib

from app.services.apify import (
    _norm_zonaprop_posting,
    _zonaprop_applied_zone_ids,
    _zonaprop_paging,
    _zonaprop_posting_zone_ids,
    _zonaprop_state,
)

_FIXTURE = json.loads(
    (pathlib.Path(__file__).parent.parent / 'fixtures' / 'zonaprop_city_bell.json')
    .read_text()
)


def _postings() -> list[dict]:
    return _FIXTURE['listStore']['listPostings']


def _by_id(posting_id: str) -> dict:
    return next(p for p in _postings() if p['postingId'] == posting_id)


class TestPullingTheStateOutOfTheHtml:
    def test_a_regex_cannot_do_this_but_brace_matching_can(self):
        """More script follows the object, so a greedy/lazy regex either
        overshoots or truncates — the live page failed with
        `Extra data: line 1 column 354022`."""
        html = (
            '<html><script>window.__PRELOADED_STATE__ = '
            '{"listStore": {"paging": {"total": 2}}};'
            'window.somethingElse = {"also": "json"};</script></html>'
        )
        assert _zonaprop_state(html) == {'listStore': {'paging': {'total': 2}}}

    def test_braces_inside_strings_do_not_confuse_it(self):
        html = 'x window.__PRELOADED_STATE__ = {"t": "a { b } c", "n": 1}; more'
        assert _zonaprop_state(html) == {'t': 'a { b } c', 'n': 1}

    def test_escaped_quotes_do_not_confuse_it(self):
        html = r'window.__PRELOADED_STATE__ = {"t": "say \"hi\" {", "n": 2}; tail'
        assert _zonaprop_state(html) == {'t': 'say "hi" {', 'n': 2}

    def test_a_page_without_the_marker_returns_none(self):
        """A WAF challenge or an error page — not something to parse."""
        assert _zonaprop_state('<html>Acceso denegado</html>') is None


class TestTheZoneThePortalActuallyApplied:
    def test_it_reads_the_applied_filter(self):
        """This is the honest replacement for guessing from listing text: the
        portal states which zone it filtered on."""
        assert _zonaprop_applied_zone_ids(_FIXTURE) == {'1001379'}

    def test_a_posting_carries_its_whole_location_chain(self):
        ids = _zonaprop_posting_zone_ids(_by_id('59792482'))
        assert '1001379' in ids     # City Bell, the zone itself
        assert '1001361' in ids     # La Plata, the containing CIUDAD

    def test_a_sub_barrio_belongs_to_the_zone_that_was_asked_for(self):
        """Grand Bell sits inside City Bell. The portal returned it on purpose;
        matching listing text against the word "City Bell" threw it away."""
        assert '1001379' in _zonaprop_posting_zone_ids(_by_id('57624359'))


class TestNormalising:
    def test_a_posting_becomes_a_property(self):
        prop = _norm_zonaprop_posting(_by_id('59792482'))

        assert prop is not None
        assert prop.fuente == 'zonaprop'
        assert prop.precio == 450_000
        assert prop.moneda == 'USD'
        assert prop.tipo_operacion == 'venta'
        assert prop.tipo_propiedad == 'casa'
        assert prop.direccion == 'SAN Efrén. Calle 144 y Arroyo Carnaval.'
        assert prop.m2_total == 1000
        assert prop.url_origen == (
            'https://www.zonaprop.com.ar/propiedades/clasificado/'
            'veclcain-casa-en-venta-city-bell.-barrio-cerrado-59792482.html'
        )

    def test_the_url_is_absolute(self):
        """The payload stores a site-relative path; a relative `url_origen`
        would break dedup and every link in the UI."""
        for p in _postings():
            assert _norm_zonaprop_posting(p).url_origen.startswith('https://')

    def test_pictures_come_through(self):
        assert _norm_zonaprop_posting(_by_id('59792482')).imagenes

    def test_every_fixture_posting_normalises(self):
        assert all(_norm_zonaprop_posting(p) is not None for p in _postings())

    def test_a_posting_with_no_price_is_dropped(self):
        """"Consultar precio" is not a search result we can rank or filter."""
        p = dict(_by_id('59792482'))
        p['priceOperationTypes'] = []
        assert _norm_zonaprop_posting(p) is None


class TestPaging:
    def test_the_portal_declares_its_own_pagination(self):
        """No more inferring the page size from how many items came back."""
        paging = _zonaprop_paging(_FIXTURE)
        assert paging['total'] == 20
        assert paging['totalPages'] == 1
        assert paging['currentPage'] == 1

    def test_a_missing_paging_block_is_not_a_crash(self):
        assert _zonaprop_paging({'listStore': {}}) == {}
