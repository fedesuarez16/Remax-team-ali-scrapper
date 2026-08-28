"""A composite zona is `localidad, partido` — and the partido must not be
allowed to satisfy the localidad.

`_item_matches_zona` mashed neighborhood, city, address, title and description
into ONE haystack and asked whether every comma-part appeared somewhere in it.
For "La Plata, La Plata" — the casco, whose localidad and partido share a name
— both parts are the same string, so the test collapsed to "does 'la plata'
appear anywhere". ZonaProp puts the PARTIDO in `city`, so every listing in the
partido matched: a casco search returned City Bell, Villa Elisa, Gonnet and
Tolosa.

The portal already tells us the locality in `neighborhood`. So the localidad
part is checked against THAT, and only the partido part gets to roam the whole
record.
"""
from typing import Any

from app.models.property import ScrapingFilters
from app.services.apify import _guard_phrases, _item_matches_zona


def _zp(neighborhood: str, *, city: str = 'La Plata',
        address: str = 'Calle 13 nro 470') -> dict[str, Any]:
    """ZonaProp's shape: barrio/localidad in `neighborhood`, PARTIDO in `city`."""
    return {'neighborhood': neighborhood, 'city': city, 'address': address,
            'title': f'Casa en {neighborhood}', 'description': 'con patio'}


def _guard(zona: str) -> set[str]:
    return _guard_phrases(ScrapingFilters(zona=zona, zona_pedida=zona))


class TestElCascoNoEsElPartido:
    def test_the_casco_keeps_its_own_listings(self):
        assert _item_matches_zona(_zp('La Plata'), _guard('La Plata, La Plata'))

    def test_city_bell_is_not_the_casco(self):
        assert not _item_matches_zona(_zp('City Bell'), _guard('La Plata, La Plata'))

    def test_villa_elisa_is_not_the_casco(self):
        assert not _item_matches_zona(_zp('Villa Elisa'), _guard('La Plata, La Plata'))

    def test_gonnet_and_tolosa_are_not_the_casco(self):
        guard = _guard('La Plata, La Plata')
        assert not _item_matches_zona(_zp('Gonnet'), guard)
        assert not _item_matches_zona(_zp('Tolosa'), guard)

    def test_the_partido_in_the_address_does_not_smuggle_one_in(self):
        """`neighborhood` is authoritative when the portal filled it — an
        address that happens to spell out the partido must not override it."""
        item = _zp('City Bell', address='Calle 13 e/ 470 y 471, City Bell, La Plata')
        assert not _item_matches_zona(item, _guard('La Plata, La Plata'))


class TestTheBarrioSearchesStillWork:
    def test_city_bell_finds_city_bell(self):
        assert _item_matches_zona(_zp('City Bell'), _guard('City Bell, La Plata'))

    def test_city_bell_does_not_find_the_casco(self):
        assert not _item_matches_zona(_zp('La Plata'), _guard('City Bell, La Plata'))

    def test_city_bell_does_not_find_villa_elisa(self):
        assert not _item_matches_zona(_zp('Villa Elisa'), _guard('City Bell, La Plata'))

    def test_the_partido_still_has_to_match(self):
        """The San Luis homonym: right barrio name, wrong province."""
        item = _zp('Casco Urbano', city='San Fco del Monte de Oro')
        item['description'] = 'San Luis'
        assert not _item_matches_zona(item, _guard('Casco Urbano, La Plata'))


class TestWhenThePortalDidNotLabelTheLocality:
    def test_it_falls_back_to_the_rest_of_the_record(self):
        """No `neighborhood`: the localidad may still be named in the address
        or the title, and those we accept."""
        item = _zp('', address='Calle 13 e/ 470 y 471, City Bell')
        item['title'] = 'Casa'
        assert _item_matches_zona(item, _guard('City Bell, La Plata'))

    def test_the_partido_field_is_still_not_a_localidad(self):
        """THE hole: with `neighborhood` empty, `city` (the partido) must not
        stand in for the localidad either."""
        item = _zp('', address='Calle 7 e/ 49 y 50')
        item['title'] = 'Casa'
        assert not _item_matches_zona(item, _guard('City Bell, La Plata'))


class TestBareZonasAreUnchanged:
    def test_a_single_phrase_still_roams_the_whole_record(self):
        """No comma, no localidad/partido split — the pre-existing behaviour
        for "Palermo" or a bare "La Plata" must not move."""
        assert _item_matches_zona(_zp('City Bell'), _guard('La Plata'))
        assert _item_matches_zona(_zp('Palermo', city='CABA'), _guard('Palermo'))

    def test_an_empty_guard_still_accepts_everything(self):
        assert _item_matches_zona(_zp('City Bell'), set())
