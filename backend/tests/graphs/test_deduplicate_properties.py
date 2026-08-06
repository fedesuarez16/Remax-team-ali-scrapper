"""Test-first for cross-portal deduplication in `deduplicate_properties`.

Written BEFORE the node stops keying on the raw `direccion` string, so every
cross-portal assertion here MUST fail until it lands.

Why this matters: the same listing reaches us from several portals at once
(Zonaprop and Argenprop publish it directly, aggregators republish it), and
each portal writes the address its own way — "Av. Santa Fe 1234",
"Santa Fe Av. 1234", "Santa Fe 1234, Palermo". The old key was the exact
`(direccion, precio, tipo_operacion)` triple, so none of those collapsed and
one property landed in the DB once per portal.

The rule under test: two rows collapse when they point at the SAME street and
street number AND agree on price, currency, operation and property type.
Price stays in the key on purpose — it is what tells apart two different units
inside one building, which share an address but are NOT the same listing.
"""
from app.graphs.extraction.nodes import deduplicate_properties
from app.models.property import NormalizedProperty


def _prop(direccion: str, **overrides) -> NormalizedProperty:
    data = {
        'direccion': direccion,
        'precio': 120_000.0,
        'moneda': 'USD',
        'tipo_operacion': 'venta',
        'tipo_propiedad': 'departamento',
        'fuente': 'zonaprop',
    }
    data.update(overrides)
    return NormalizedProperty(**data)


def _dedup(*props: NormalizedProperty) -> list[NormalizedProperty]:
    out = deduplicate_properties({'normalized_properties': list(props)})
    return out['normalized_properties']


# ── Regression: what already worked must keep working ────────────────────────

def test_identical_rows_still_collapse():
    kept = _dedup(_prop('Calle 7 1234'), _prop('Calle 7 1234'))
    assert len(kept) == 1


def test_first_occurrence_is_the_one_kept():
    kept = _dedup(
        _prop('Calle 7 1234', fuente='zonaprop'),
        _prop('Calle 7 1234', fuente='argenprop'),
    )
    assert [p.fuente for p in kept] == ['zonaprop']


def test_empty_input_is_handled():
    assert _dedup() == []


# ── The actual fix: same listing, different portal spelling ──────────────────

def test_street_type_abbreviation_collapses():
    """"Av." and "Avenida" are the same avenue."""
    kept = _dedup(_prop('Av. Santa Fe 1234'), _prop('Avenida Santa Fe 1234'))
    assert len(kept) == 1


def test_trailing_zona_does_not_block_the_match():
    """One portal appends the barrio/city, another does not."""
    kept = _dedup(_prop('Santa Fe 1234, Palermo, Capital Federal'), _prop('Santa Fe 1234'))
    assert len(kept) == 1


def test_accents_and_casing_collapse():
    kept = _dedup(_prop('Avenida MITRE 500'), _prop('avenida mitré 500'))
    assert len(kept) == 1


def test_punctuation_and_altura_noise_collapse():
    kept = _dedup(_prop('Calle 7 N° 1234'), _prop('calle 7 1234'))
    assert len(kept) == 1


def test_abbreviated_honorifics_collapse():
    kept = _dedup(_prop('Gral. Paz 742'), _prop('General Paz 742'))
    assert len(kept) == 1


# ── The guard rails: things that must NOT be merged ──────────────────────────

def test_same_address_different_price_stays_split():
    """Two units in one building share the street address but are two listings."""
    kept = _dedup(_prop('Santa Fe 1234', precio=120_000.0), _prop('Santa Fe 1234', precio=95_000.0))
    assert len(kept) == 2


def test_same_address_different_currency_stays_split():
    kept = _dedup(_prop('Santa Fe 1234', moneda='USD'), _prop('Santa Fe 1234', moneda='ARS'))
    assert len(kept) == 2


def test_same_address_different_operation_stays_split():
    kept = _dedup(
        _prop('Santa Fe 1234', tipo_operacion='venta'),
        _prop('Santa Fe 1234', tipo_operacion='alquiler'),
    )
    assert len(kept) == 2


def test_same_address_different_property_type_stays_split():
    kept = _dedup(
        _prop('Santa Fe 1234', tipo_propiedad='departamento'),
        _prop('Santa Fe 1234', tipo_propiedad='local'),
    )
    assert len(kept) == 2


def test_different_street_number_stays_split():
    kept = _dedup(_prop('Santa Fe 1234'), _prop('Santa Fe 1236'))
    assert len(kept) == 2


def test_different_street_same_number_stays_split():
    kept = _dedup(_prop('Santa Fe 1234'), _prop('Corrientes 1234'))
    assert len(kept) == 2


def test_addresses_without_a_street_number_are_not_over_merged():
    """A vague address ("Gonnet", "City Bell centro") carries no house number.

    Canonicalizing down to a street would make every such row collapse into one
    per price, silently deleting real listings — so with no number we fall back
    to comparing the full address, exactly as before.
    """
    kept = _dedup(_prop('Gonnet'), _prop('Gonnet centro'))
    assert len(kept) == 2


def test_vague_addresses_that_are_truly_equal_still_collapse():
    kept = _dedup(_prop('Gonnet'), _prop('  gonnet  '))
    assert len(kept) == 1
