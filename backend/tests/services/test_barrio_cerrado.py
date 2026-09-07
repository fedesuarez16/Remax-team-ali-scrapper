"""Test-first for `app.services.barrio_cerrado` — the catalogue layer that
turns a gated community into something the zona pipeline can already carry.

WHY THIS EXISTS. `zona_candidates` derives its fallback chain from the comma
string the user typed: "City Bell, La Plata" degrades to "La Plata". A gated
community has no such string — the user says "Grand Bell", and nothing in the
system knows that it sits in City Bell, in La Plata. So a country search that
a portal cannot resolve degrades to NOTHING and comes back empty, instead of
degrading to its localidad and filtering by name.

The catalogue supplies exactly the missing fact: the containment. Everything
else here is about the second half of the problem — once we DO degrade to the
localidad, the guard has to recognise the barrio in a listing's free text, and
the portals never spell it the same way twice. Measured live (2026-09-04):

    Argenprop      "Los Ceibos, Partido de La Plata"          → LOS-CEIBOS-LP
    Argenprop      "Barrio Los Ceibos  - Km.32, González Catan"
    InmoBusqueda   "Club de campo Los Ceibos, City Bell, ..."  → barrio_id 129
    InmoBusqueda   "Countries y Barrios Cerrados Nordelta, ..."
    ZonaProp       h1 "42 Casas en venta en Grand Bell, City Bell"

Same four places, four spellings. A raw `in` on the stored name matches none
of the prefixed ones, which is why `strip_kind_prefix` and `barrio_aliases`
are the load-bearing pieces and not cosmetics.
"""
from __future__ import annotations

import pytest

from app.services.barrio_cerrado import (
    barrio_aliases,
    barrio_matches,
    barrio_zona,
    strip_kind_prefix,
)


class TestStripKindPrefix:
    """The portals prepend a KIND to the name. The kind is not the identity."""

    @pytest.mark.parametrize(('label', 'esperado'), [
        ('Club de campo Los Ceibos', 'Los Ceibos'),
        ('Barrio Cerrado Grand Bell', 'Grand Bell'),
        ('Barrio Privado Haras del Sur', 'Haras del Sur'),
        ('Country Los Ceibos', 'Los Ceibos'),
        ('Country Club Los Ceibos', 'Los Ceibos'),
        ('Barrio Los Ceibos', 'Los Ceibos'),
        ('B° Los Ceibos', 'Los Ceibos'),
        ('Bo. Los Ceibos', 'Los Ceibos'),
        ('Countries y Barrios Cerrados Nordelta', 'Nordelta'),
        ('Complejo Grand Bell', 'Grand Bell'),
    ])
    def test_kind_prefix_is_dropped(self, label: str, esperado: str) -> None:
        assert strip_kind_prefix(label) == esperado

    def test_a_bare_name_is_untouched(self) -> None:
        assert strip_kind_prefix('Grand Bell') == 'Grand Bell'

    def test_the_kind_is_not_stripped_out_of_the_middle(self) -> None:
        """"Chacras del Country" is a NAME. Only a leading kind is a kind."""
        assert strip_kind_prefix('Chacras del Country') == 'Chacras del Country'

    def test_a_name_that_is_only_a_kind_survives(self) -> None:
        """Stripping "Barrio" out of a place literally called "El Barrio"
        would leave nothing to match on — better a useless alias than none."""
        assert strip_kind_prefix('Barrio') == 'Barrio'

    def test_trailing_punctuation_from_the_portal_is_cleaned(self) -> None:
        """Argenprop ships "Barrio Los Ceibos  - Km.32" with a double space."""
        assert strip_kind_prefix('Barrio Los Ceibos  - Km.32') == 'Los Ceibos - Km.32'


class TestBarrioAliases:
    """The phrase set the zona guard matches a listing's free text against."""

    def test_the_name_itself_is_always_an_alias(self) -> None:
        assert 'grand bell' in barrio_aliases('Grand Bell')

    def test_kind_prefixed_forms_are_generated(self) -> None:
        """A listing titled "Casa en Barrio Cerrado Grand Bell" has to match a
        catalogue that only stores "Grand Bell"."""
        aliases = barrio_aliases('Grand Bell')
        assert 'barrio cerrado grand bell' in aliases
        assert 'club de campo grand bell' in aliases
        assert 'country grand bell' in aliases

    def test_a_stored_name_that_already_carries_its_kind_is_not_doubled(self) -> None:
        """"Club de Campo Los Ceibos" must not yield "barrio cerrado club de
        campo los ceibos" — nobody writes that, and it is dead weight in the
        haystack scan."""
        aliases = barrio_aliases('Club de Campo Los Ceibos')
        assert 'los ceibos' in aliases
        assert 'club de campo los ceibos' in aliases
        assert not any(a.startswith('barrio cerrado club') for a in aliases)

    def test_extra_aliases_from_the_operator_are_included(self) -> None:
        """The hand-loaded field: the operator knows "Grand Bell II" is listed
        as "Grand Bell 2" on one portal, and no algorithm will guess it."""
        aliases = barrio_aliases('Grand Bell', extra=['Grand Bell II'])
        assert 'grand bell ii' in aliases

    def test_accents_are_folded(self) -> None:
        """Portals disagree on the tilde — "Las Cañitas" / "Las Canitas"."""
        assert 'las canitas' in barrio_aliases('Las Cañitas')

    def test_aliases_are_deduped_and_stable(self) -> None:
        """Same input, same order — the set is persisted and diffed."""
        assert barrio_aliases('Grand Bell') == barrio_aliases('Grand Bell')
        assert len(set(barrio_aliases('Grand Bell'))) == len(barrio_aliases('Grand Bell'))

    def test_an_empty_name_yields_nothing(self) -> None:
        """An empty alias set would match EVERY listing — the guard reads an
        empty set as "keep everything". Never emit one from a blank name."""
        assert barrio_aliases('  ') == ()


class TestBarrioMatches:
    """The guard predicate, run against a listing's concatenated free text."""

    def test_the_bare_name_in_a_title_matches(self) -> None:
        aliases = barrio_aliases('Grand Bell')
        assert barrio_matches('Casa en venta en Grand Bell, City Bell', aliases)

    def test_a_kind_prefixed_title_matches(self) -> None:
        aliases = barrio_aliases('Los Ceibos')
        assert barrio_matches('Lote en Club de Campo Los Ceibos', aliases)

    def test_accent_mismatch_still_matches(self) -> None:
        assert barrio_matches('Depto en Las Canitas', barrio_aliases('Las Cañitas'))

    def test_a_different_barrio_does_not_match(self) -> None:
        assert not barrio_matches(
            'Casa en Haras del Sur, La Plata', barrio_aliases('Grand Bell'))

    def test_a_prefix_of_a_longer_word_does_not_match(self) -> None:
        """"Grand Bell" must not match "Grand Belleville" — RE/MAX's own
        autocomplete ranks that decoy FIRST for the query "grand bell"
        (`test_remax_gated_community.py`), so it is a live shape, not a
        hypothetical. Aliases match on WORD boundaries."""
        assert not barrio_matches(
            'Casa en Grand Belleville, Monte Grande', barrio_aliases('Grand Bell'))

    def test_a_homonym_in_another_province_is_NOT_this_function_s_job(self) -> None:
        """Argenprop serves "El Rodeo" (Córdoba) and "Rodeo de la Cruz"
        (Mendoza). No alias check can separate them — both genuinely contain
        the word. What separates them is the LOCALIDAD half of the composite
        zona (`barrio_zona`), which the existing zona guard checks against a
        different field. Pinned so nobody later "fixes" this by tightening the
        alias match into something that stops matching real listings."""
        assert barrio_matches('Casa en Rodeo de la Cruz', barrio_aliases('Rodeo'))

    def test_an_empty_alias_set_matches_nothing(self) -> None:
        """The inverse of the zona guard's convention, on purpose: an empty
        BARRIO set means "we could not build a barrio filter", and answering a
        country search with the whole localidad is the failure we are fixing."""
        assert not barrio_matches('Casa en Grand Bell', ())


class TestBarrioZona:
    """The composite zona string the existing pipeline already knows how to
    walk. This is the whole point of the catalogue: supplying the containment
    that the user's own words never carry."""

    def test_the_barrio_leads_its_localidad(self) -> None:
        assert barrio_zona('Grand Bell', 'City Bell, La Plata') == (
            'Grand Bell, City Bell, La Plata')

    def test_it_degrades_through_the_existing_candidate_chain(self) -> None:
        """The payoff: `zona_candidates` now has something to degrade THROUGH.
        Without the catalogue, "Grand Bell" is a one-link chain that dead-ends
        on any portal that cannot resolve it."""
        from app.services.zona import zona_candidates

        assert zona_candidates(barrio_zona('Grand Bell', 'City Bell, La Plata')) == [
            'Grand Bell, City Bell, La Plata',
            'City Bell, La Plata',
            'La Plata',
        ]

    def test_a_bare_name_is_a_dead_end_chain(self) -> None:
        """Pinning the problem the catalogue solves, so a regression that
        drops the localidad is visible as a behaviour change, not a subtlety."""
        from app.services.zona import zona_candidates

        assert zona_candidates('Grand Bell') == ['Grand Bell']

    def test_the_kind_never_reaches_the_zona(self) -> None:
        """This string becomes a URL slug on every portal, and none of them
        carry the kind. Measured: `/casas-venta-grand-bell.html` → 42 listings,
        `/casas-venta-barrio-grand-bell.html` → the NATIONWIDE 172.141."""
        assert barrio_zona('Club de Campo Los Ceibos', 'City Bell, La Plata') == (
            'Los Ceibos, City Bell, La Plata')

    def test_a_missing_localidad_yields_just_the_barrio(self) -> None:
        assert barrio_zona('Grand Bell', '') == 'Grand Bell'

    def test_whitespace_around_the_parts_is_normalised(self) -> None:
        assert barrio_zona('  Grand Bell ', ' City Bell ,  La Plata ') == (
            'Grand Bell, City Bell, La Plata')


class TestBarrioProbeForms:
    """The shapes to ask a portal about ONE barrio, most specific first.

    Not `zona_candidates`: that chain degrades the BARRIO AWAY ("Grand Bell,
    City Bell, La Plata" → "City Bell, La Plata"), which is right for a search
    and useless for a probe — a probe that resolves the localidad has learned
    nothing about the barrio. These forms keep the barrio head and shorten the
    TAIL instead.

    Measured live (2026-09-07), and this is the bug that motivated it:
    Argenprop files gated communities under the PARTIDO, labelling Grand Bell
    "Grand Bell, Partido de La Plata" — no City Bell anywhere. Its resolver
    requires every comma part of the query to appear in the label, so the
    three-part composite matched nothing and the probe wrote down `localidad`
    for a portal that has `CodigoBarrio=GRAND-BELL`.
    """

    def test_the_composite_comes_first(self) -> None:
        from app.services.barrio_cerrado import barrio_probe_forms

        assert barrio_probe_forms('Grand Bell', 'City Bell, La Plata')[0] == (
            'Grand Bell, City Bell, La Plata')

    def test_the_intermediate_localidad_is_dropped_next(self) -> None:
        """"Grand Bell, La Plata" is the shape Argenprop actually stores."""
        from app.services.barrio_cerrado import barrio_probe_forms

        assert 'Grand Bell, La Plata' in barrio_probe_forms('Grand Bell', 'City Bell, La Plata')

    def test_the_bare_barrio_is_the_last_resort(self) -> None:
        from app.services.barrio_cerrado import barrio_probe_forms

        assert barrio_probe_forms('Grand Bell', 'City Bell, La Plata')[-1] == 'Grand Bell'

    def test_the_barrio_never_degrades_away(self) -> None:
        """The whole difference from `zona_candidates`. A form without the
        barrio would resolve the localidad and be recorded as the barrio's own
        ref — "all of City Bell", filed as a precise Grand Bell search."""
        from app.services.barrio_cerrado import barrio_probe_forms

        formas = barrio_probe_forms('Grand Bell', 'City Bell, La Plata')
        assert all(f.startswith('Grand Bell') for f in formas)

    def test_the_kind_is_stripped(self) -> None:
        from app.services.barrio_cerrado import barrio_probe_forms

        assert barrio_probe_forms('Club de Campo Los Ceibos', 'City Bell, La Plata')[-1] == (
            'Los Ceibos')

    def test_a_one_part_localidad_gives_two_forms(self) -> None:
        from app.services.barrio_cerrado import barrio_probe_forms

        assert barrio_probe_forms('Haras del Sur', 'La Plata') == [
            'Haras del Sur, La Plata', 'Haras del Sur']

    def test_no_localidad_gives_just_the_barrio(self) -> None:
        from app.services.barrio_cerrado import barrio_probe_forms

        assert barrio_probe_forms('Grand Bell', '') == ['Grand Bell']

    def test_forms_are_deduped(self) -> None:
        """A localidad that IS its partido ("La Plata, La Plata") must not
        produce the same query twice — each form costs a live request."""
        from app.services.barrio_cerrado import barrio_probe_forms

        formas = barrio_probe_forms('Grand Bell', 'La Plata, La Plata')
        assert len(formas) == len(set(formas))
