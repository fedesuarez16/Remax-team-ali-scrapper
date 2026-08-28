"""MercadoLibre never writes the partido, so the guard must not demand it.

A card's location reads `calle, LOCALIDAD, PROVINCIA`:

    'C. 13 470, City Bell, Buenos Aires'
    'Calle 7 500, La Plata, Buenos Aires'

A composite zona ("City Bell, La Plata") asks for BOTH parts, and `la-plata`
is simply not in that string — so a legitimate City Bell listing was rejected
for failing to spell a partido the portal does not publish. Measured against
the real card shape; the reported symptom was "trae pocas".

The localidad is what identifies the place here, and the search URL is already
scoped to it (`/casas/venta/city-bell`). The province still guards the
homonyms the composite existed for: "Palermo, Salta" does not answer a
"Palermo, CABA" search.
"""
import pytest

from app.models.property import ScrapingFilters
from app.services.apify import _ml_matches_zona


def _phrases(zona: str) -> ScrapingFilters:
    return ScrapingFilters(zona=zona, zona_pedida=zona)


class TestRealCardShapes:
    @pytest.mark.parametrize('location', [
        'C. 13 470, City Bell, Buenos Aires',
        'Av. Centenario 1200, City Bell, Buenos Aires',
        'Calle 473, City Bell, La Plata',
    ])
    def test_a_city_bell_card_is_kept(self, location: str) -> None:
        assert _ml_matches_zona(location, _phrases('City Bell, La Plata'))

    def test_another_localidad_is_still_rejected(self) -> None:
        assert not _ml_matches_zona(
            'Calle 7 500, La Plata, Buenos Aires', _phrases('City Bell, La Plata'))

    def test_gonnet_does_not_answer_a_city_bell_search(self) -> None:
        assert not _ml_matches_zona(
            'Calle 495, Manuel B Gonnet, Buenos Aires', _phrases('City Bell, La Plata'))


class TestTheHomonymProtectionSurvives:
    def test_the_province_still_has_to_agree_when_it_is_written(self) -> None:
        """The reason composites exist: a Palermo in Salta is not Palermo."""
        assert not _ml_matches_zona(
            'Balcarce 100, Palermo, Salta', _phrases('Palermo, CABA'))

    def test_the_right_province_passes(self) -> None:
        assert _ml_matches_zona(
            'Thames 1500, Palermo, Capital Federal', _phrases('Palermo, CABA'))


class TestBareZonasAreUnchanged:
    def test_a_single_phrase_matches_anywhere_in_the_string(self) -> None:
        assert _ml_matches_zona('Calle 7 500, La Plata, Buenos Aires', _phrases('La Plata'))

    def test_and_still_rejects_elsewhere(self) -> None:
        assert not _ml_matches_zona('Av. Colón 100, Córdoba, Córdoba', _phrases('La Plata'))

    def test_no_zona_keeps_everything(self) -> None:
        assert _ml_matches_zona('cualquier cosa', ScrapingFilters())
