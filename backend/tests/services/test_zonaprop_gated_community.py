"""A gated community must survive ZonaProp's union slug.

ZonaProp models a country as a `subZone` under its localidad — Grand Bell
sits under City Bell, under La Plata. The chat path slugifies the parsed
zona whole, so "Grand Bell, La Plata" becomes `grand-bell-la-plata`, and
BOTH halves resolve. The portal reads that as a UNION and throws the entire
partido in. Verified live (2026-09-02, appliedFilters → result count):

    casas-venta-grand-bell-la-plata  → [La Plata (city), Grand Bell (subZone)] 5.951
    casas-venta-grand-bell           → [Grand Bell (subZone)]                     40

So the union is not a near miss: it is 148x the answer, and every extra
listing is a house the user did not ask about.

`_zonaprop_canonical_zona` already collapses it — this pins that it does so
for a gated community, whose `subZone` type had no coverage. The states below
are the real `appliedFilters` captured from each page.
"""
from app.models.property import ScrapingFilters
from app.services.apify import (
    _zonaprop_canonical_zona,
    _zonaprop_requested_zone_ids,
    _zonaprop_search_url,
)

_GRAND_BELL_ID = '208745'

# Real `appliedFilters` from `casas-venta-grand-bell-la-plata.html`.
_UNION = {'listStore': {'appliedFilters': [{'type': 'location', 'options': [
    {'label': 'La Plata', 'min': '1001361', 'type': 'city'},
    {'label': 'Grand Bell', 'min': _GRAND_BELL_ID, 'type': 'subZone'},
]}]}}

# Real `appliedFilters` from the canonical page the bare slug redirects to.
_NARROW = {'listStore': {'appliedFilters': [{'type': 'location', 'options': [
    {'label': 'Grand Bell', 'min': _GRAND_BELL_ID, 'type': 'subZone'},
]}]}}


def _filters(zona: str) -> ScrapingFilters:
    return ScrapingFilters(
        zona=zona, tipo_operacion='venta', tipos_propiedad=['casa'],
    )


class TestTheUnionIsCollapsedOntoTheCountry:
    def test_chat_path_builds_the_union_slug(self):
        """Not a bug in itself — it is the premise the retry exists for."""
        assert _zonaprop_search_url(_filters('Grand Bell, La Plata')).endswith(
            '/casas-venta-grand-bell-la-plata.html')

    def test_canonical_zona_is_the_country_not_the_partido(self):
        assert _zonaprop_canonical_zona(_UNION, 'Grand Bell, La Plata') == 'Grand Bell'

    def test_the_retry_url_drops_the_partido(self):
        canonical = _zonaprop_canonical_zona(_UNION, 'Grand Bell, La Plata')
        retry = _zonaprop_search_url(
            _filters('Grand Bell, La Plata').model_copy(
                update={'zona': canonical, 'localidades': []}),
        )
        assert retry.endswith('/casas-venta-grand-bell.html')


class TestTheNarrowPageIsLeftAlone:
    def test_no_further_collapse_is_attempted(self):
        """A single applied option is already the request — retrying it would
        loop."""
        assert _zonaprop_canonical_zona(_NARROW, 'Grand Bell, La Plata') is None

    def test_the_country_counts_as_the_requested_zone(self):
        """`subZone` must be recognised as the answer; reading it as "the
        portal served somewhere else" would discard all 40 listings."""
        assert _zonaprop_requested_zone_ids(_NARROW, 'Grand Bell, La Plata') == {
            _GRAND_BELL_ID}

    def test_the_partido_is_never_what_the_union_resolves_to(self):
        """The regression: taking the union's city id would accept the whole
        partido."""
        assert _zonaprop_requested_zone_ids(_UNION, 'Grand Bell, La Plata') == {
            _GRAND_BELL_ID}
