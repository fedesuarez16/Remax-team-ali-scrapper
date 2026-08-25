"""The zona guard must require the zona that was ASKED FOR, not its fallbacks.

`_guard_phrases` expanded every seed through `zona_candidates`, so a search for
"City Bell, La Plata" carried the degraded phrase "La Plata" in its guard — and
`_item_matches_zona` passes on ANY phrase. A ZonaProp nationwide redirect
therefore sailed through: every La Plata listing in the dump matched, the
caller saw a non-empty result, and the composite-slug retry that exists for
exactly that redirect never fired because it is gated on `not results`.

Degradation is not lost by tightening this: `scrape_source` already walks the
candidate chain one candidate at a time, and each walk rebuilds the guard from
the candidate it actually requested. Expanding inside the guard was redundant
with that walk, and harmful on top of it.
"""
from typing import Any

from app.models.property import ScrapingFilters
from app.services.apify import _guard_phrases, _item_matches_zona


def _casco_de_la_plata() -> dict[str, Any]:
    """A real La Plata listing that has nothing to do with City Bell."""
    return {
        'neighborhood': 'La Plata', 'city': 'La Plata',
        'address': 'Calle 7 e/ 49 y 50',
        'title': 'Departamento 2 ambientes en La Plata',
        'description': 'Excelente ubicacion en el casco urbano',
    }


def _en_city_bell() -> dict[str, Any]:
    """ZonaProp puts the barrio in `neighborhood` and the PARTIDO in `city`."""
    return {
        'neighborhood': 'City Bell', 'city': 'La Plata',
        'address': 'Calle 13 e/ 470 y 471',
        'title': 'Departamento 3 ambientes en City Bell',
        'description': 'A metros del centro de City Bell',
    }


def test_guard_does_not_carry_the_degraded_phrase() -> None:
    filters = ScrapingFilters(zona='City Bell, La Plata')

    assert _guard_phrases(filters) == {'City Bell, La Plata'}


def test_a_la_plata_listing_is_rejected_by_a_city_bell_search() -> None:
    filters = ScrapingFilters(zona='City Bell, La Plata')

    assert not _item_matches_zona(_casco_de_la_plata(), _guard_phrases(filters))


def test_a_real_city_bell_listing_still_passes() -> None:
    """The composite phrase needs BOTH parts, and ZonaProp supplies them in
    separate fields — barrio in `neighborhood`, partido in `city`."""
    filters = ScrapingFilters(zona='City Bell, La Plata')

    assert _item_matches_zona(_en_city_bell(), _guard_phrases(filters))


def test_a_bare_zona_is_unchanged() -> None:
    filters = ScrapingFilters(zona='City Bell')

    assert _guard_phrases(filters) == {'City Bell'}


def test_the_degraded_candidate_guards_itself_when_it_is_the_one_requested() -> None:
    """`scrape_source` rewrites `zona` to the candidate before each attempt, so
    the La Plata pass legitimately accepts La Plata listings."""
    filters = ScrapingFilters(zona='La Plata')

    assert _item_matches_zona(_casco_de_la_plata(), _guard_phrases(filters))


def test_map_path_still_unions_its_seeds() -> None:
    """With `localidades` set the guard is deliberately wide across the seeds
    (barrios ∪ localidades ∪ zona) — the polygon is the precision gate there.
    What must stop is expanding each seed through its fallback chain."""
    filters = ScrapingFilters(
        zona='City Bell', zonas=['City Bell', 'Gonnet'],
        localidades=['City Bell, La Plata'],
    )

    assert _guard_phrases(filters) == {'City Bell', 'Gonnet', 'City Bell, La Plata'}


def test_empty_zona_still_accepts_everything() -> None:
    assert _guard_phrases(ScrapingFilters()) == set()
    assert _item_matches_zona(_casco_de_la_plata(), set())
