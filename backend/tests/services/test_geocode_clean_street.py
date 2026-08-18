"""Test-first: `_clean_street` must survive the address shapes the portals
actually emit, taken verbatim from rows that geocoding failed on.

Every literal below is a real `properties.direccion` that Nominatim rejected —
649 rows had `geocoded_at` set with `lat` still NULL, and these are the
recurring shapes among them.
"""
import pytest

from app.services.geocode import _clean_street


@pytest.mark.parametrize(('raw', 'expected'), [
    # --- between-streets: the "e/" separator has no canonical spelling ---
    # Slash glued to the next number — the original regex demanded whitespace
    # on BOTH sides of "e/", so these were passed to Nominatim untouched.
    ('19 E/41 y 42 0', 'Calle 19'),
    ('Calle 487 E/16 y 16 Bis 2100', 'Calle 487'),
    # Space between the "e" and the slash.
    ('29 E / 418 y 419 900', 'Calle 29'),
    # No slash at all — bare "E" standing in for "entre".
    ('509 E 14 y 15 1900', 'Calle 509'),
    # Canonical spelling must keep working.
    ('502 e/ 17 y 18', 'Calle 502'),
])
def test_between_streets_variants_are_stripped(raw: str, expected: str) -> None:
    assert _clean_street(raw) == expected


def test_between_streets_keeps_the_locality_after_the_comma() -> None:
    """The old regex ran to end-of-string, so it ate the city along with the
    cross streets. Losing "La Plata" sends the numbered-grid address to the
    Buenos Aires viewbox and it resolves to the wrong district (or nowhere)."""
    assert _clean_street('48 e/ 7 y 8, La Plata') == 'Calle 48, La Plata'


def test_entre_rios_is_not_a_between_streets_marker() -> None:
    """The province shares the prefix; stripping it would destroy the address."""
    assert _clean_street('Entre Ríos 450, Paraná') == 'Entre Ríos 450, Paraná'
    assert _clean_street('Calle Entre Ríos 450') == 'Calle Entre Ríos 450'


@pytest.mark.parametrize(('raw', 'expected'), [
    ('Camino General Belgrano 800, Piso 0', 'Camino General Belgrano 800'),
    ('473 bis e/15 a y 17 , Piso 0', 'Calle 473 bis'),
    ('Av. Rivadavia 1234, Piso 3', 'Av. Rivadavia 1234'),
])
def test_piso_suffix_is_dropped(raw: str, expected: str) -> None:
    """Argenprop appends the floor; Nominatim has no concept of it and the
    trailing token poisons the match."""
    assert _clean_street(raw) == expected


def test_property_type_prefix_is_dropped() -> None:
    """Inmobusqueda prefixes the listing type onto the address."""
    cleaned = _clean_street(
        'Oficina en 48 e/ 7 y 8 Centro calle 8, La Plata (Casco Urbano), Pdo. de La Plata'
    )
    assert cleaned.startswith('Calle 48,')
    assert 'Oficina' not in cleaned
    assert 'La Plata' in cleaned


def test_remax_zero_altura_sentinel_is_dropped() -> None:
    """RE/MAX writes a bare trailing 0 when the street number is unknown."""
    assert _clean_street('Calle 14 Esq. 405 - Villa Elisa 0') == 'Calle 14 y 405 - Villa Elisa'


def test_portal_breadcrumb_pipes_become_commas() -> None:
    cleaned = _clean_street('19 y 45, Argentina | G.B.A. Zona Sur | La Plata')
    assert '|' not in cleaned
    assert cleaned.startswith('Calle 19 y 45')
    assert cleaned.endswith('La Plata')


@pytest.mark.parametrize('raw', [
    'Av. Rivadavia 1234',
    'Av. Santa Fe 3253, Palermo',
    'Camino General Belgrano 1500',
])
def test_plain_addresses_are_left_alone(raw: str) -> None:
    """Regression guard: the cleaner must not touch addresses that are fine."""
    assert _clean_street(raw) == raw


@pytest.mark.parametrize(('raw', 'expected'), [
    ('48 esq 6', 'Calle 48 y 6'),
    ('48 esq. 6', 'Calle 48 y 6'),
    # Spelled out in full — 68 of the 649 failures, the second-largest shape
    # after the "e/" variants, and the abbreviation-only rule never saw them.
    ('133 Esquina 82', 'Calle 133 y 82'),
    ('138 esquina 434, City Bell', 'Calle 138 y 434, City Bell'),
])
def test_corner_notation_is_normalised(raw: str, expected: str) -> None:
    assert _clean_street(raw) == expected


@pytest.mark.parametrize(('raw', 'expected'), [
    # La Plata's grid, written with the "e/" marker simply left out.
    ('11 43 y 44', 'Calle 11'),
    ('11 43 y 44, La Plata', 'Calle 11, La Plata'),
    ('26 55 y 56, La Plata', 'Calle 26, La Plata'),
    ('14 502 y 503', 'Calle 14'),
])
def test_implicit_between_streets_is_stripped(raw: str, expected: str) -> None:
    assert _clean_street(raw) == expected


def test_corner_is_not_mistaken_for_implicit_between_streets() -> None:
    """"3 y 42" is a corner (two streets), not "<street> <a> y <b>"."""
    assert _clean_street('3 y 42 0') == 'Calle 3 y 42'


@pytest.mark.parametrize(('raw', 'expected'), [
    # "sin número" sentinel — not part of the address.
    ('162 y 475 S/N', 'Calle 162 y 475'),
    ('136 Esquina 442 Villa Elisa S/N', 'Calle 136 y 442 Villa Elisa'),
    # Street-number markers: keep the number, drop the marker.
    ('121 N°380, La Plata', 'Calle 121 380, La Plata'),
    ('25 506 y 507 al 2500', 'Calle 25'),
    ('Av. Mitre nro 1301', 'Av. Mitre 1301'),
])
def test_altura_markers_and_sentinels_are_normalised(raw: str, expected: str) -> None:
    assert _clean_street(raw) == expected


def test_empty_address_stays_empty() -> None:
    assert _clean_street('   ') == ''


@pytest.mark.parametrize(('raw', 'expected'), [
    # InmoBusqueda's format: "<tipo> en <calle> [N°alt] [e/ a y b] <barrio>, Pdo. de <partido>".
    # "Pdo." (partido) is an abbreviation Nominatim does not know — it reads as a
    # street-name token and poisons the whole query. 316 of the 963 rows still
    # unlocated carry it, and inmobusqueda alone failed on 93.5% of its rows.
    ('Casa en 121 e/ 73 y 74 Villa Elvira, Pdo. de La Plata', 'Calle 121, La Plata'),
    ('Cochera en 34 e/ 12 y 13 La Plata (Casco Urbano), Pdo. de La Plata',
     'Calle 34, La Plata'),
    ('Casa en 115 N°829 e/ 523 y 524 Tolosa, Pdo. de La Plata', 'Calle 115 829, La Plata'),
    # Spelled out in full, same meaning.
    ('Casa en 12 e/ 3 y 4, Partido de Ensenada', 'Calle 12, Ensenada'),
])
def test_partido_abbreviation_is_unwrapped(raw: str, expected: str) -> None:
    assert _clean_street(raw) == expected


def test_camino_abbreviation_is_expanded() -> None:
    assert _clean_street('Cno. Rivadavia 1200') == 'Camino Rivadavia 1200'
