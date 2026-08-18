"""Zona → ordered candidate chain, most specific first.

Motivating failure (verified live against every portal's own autocomplete):
a chat query for "el casco de La Plata" reached the scrapers as the bare
barrio "Casco Urbano", and NO two portals model that barrio the same way —
inmobusqueda has it exactly ("La Plata (Casco Urbano)"), RE/MAX's only
literal match is a *gated community* ("Los Eucaliptus Casco Urbano"),
Argenprop's only literal match is a barrio in SAN LUIS, and ZonaProp/Mudafy
have no such concept at all. Four of six portals returned 0.

There is therefore no single canonical phrase to rewrite the zona INTO. What
callers need is a chain to walk: try the qualified barrio, and on a miss fall
back to the localidad that contains it. The chain is also the guard's phrase
set, so a listing that only names the localidad still survives the filter.

Degradation stops before province/city-wide qualifiers: falling back from
"Palermo, CABA" to "CABA" would hand back the whole city, which is not a
useful answer to a barrio search.
"""
import pytest

from app.services.zona import zona_candidates


class TestDegradesToTheContainingLocalidad:
    def test_barrio_localidad_falls_back_to_localidad(self):
        assert zona_candidates('Casco Urbano, La Plata') == [
            'Casco Urbano, La Plata',
            'La Plata',
        ]

    def test_the_real_failing_query(self):
        """What the extractor SHOULD produce for "el casco de la plata"."""
        chain = zona_candidates('Casco Urbano, La Plata')
        # Argenprop/ZonaProp/Mudafy only ever resolve the tail; without it in
        # the chain they redirect nationwide and the guard rejects everything.
        assert chain[-1] == 'La Plata'

    def test_three_components_drop_one_at_a_time(self):
        assert zona_candidates('Villa Elisa, La Plata, Buenos Aires') == [
            'Villa Elisa, La Plata, Buenos Aires',
            'La Plata, Buenos Aires',
        ]


class TestStopsBeforeWideJurisdictions:
    """Never degrade to a province/city-wide term — that is not a barrio
    search any more, it is the whole jurisdiction."""

    @pytest.mark.parametrize('zona', [
        'Palermo, CABA',
        'Palermo, Capital Federal',
        'Los Hornos, Buenos Aires',
        'Belgrano, Ciudad Autonoma de Buenos Aires',
        'Tolosa, Provincia de Buenos Aires',
        'City Bell, Argentina',
    ])
    def test_single_entry_when_tail_is_a_jurisdiction(self, zona):
        assert zona_candidates(zona) == [zona]

    def test_case_and_accent_insensitive(self):
        assert zona_candidates('Núñez, capital federal') == ['Núñez, capital federal']


class TestSingleComponentAndEdges:
    def test_bare_localidad_has_nothing_to_degrade_to(self):
        assert zona_candidates('La Plata') == ['La Plata']

    def test_blank_yields_nothing(self):
        assert zona_candidates('') == []
        assert zona_candidates('   ') == []

    def test_empty_components_are_dropped(self):
        assert zona_candidates('Casco Urbano,, La Plata,') == [
            'Casco Urbano, La Plata',
            'La Plata',
        ]

    def test_whitespace_is_normalised_not_preserved(self):
        assert zona_candidates('  Casco   Urbano ,  La Plata ') == [
            'Casco Urbano, La Plata',
            'La Plata',
        ]

    def test_chain_never_repeats_a_phrase(self):
        chain = zona_candidates('La Plata, La Plata')
        assert len(chain) == len(set(chain))
