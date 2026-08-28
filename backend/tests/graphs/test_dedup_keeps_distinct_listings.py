"""Two listings from the SAME portal are two properties, not one.

Measured on a real run: ZonaProp returned 54 unique listings for "casas en la
plata, la plata entre 350000 y 400000" and dedup delivered 21 —
`dropped=33 by_fuente=[zonaprop=33]`. Every one of those 33 was a ZonaProp
listing killed by another ZonaProp listing.

The cause is the address anchor meeting La Plata's numbered grid.
`address_fingerprint` is documented as a `street number` — a BUILDING — but a
La Plata address is usually written between cross streets, and there the
trailing number is the second cross street, not an altura:

    'Calle 47 e/ 12 y 13'  ->  '47 e 12 y 13'

That is a CITY BLOCK, forty houses. `_dedup_key` leans on price to tell units
at one address apart, which works for a tower and fails for a block inside a
50k price band where asking prices are round.

The portal already deduplicates its own catalogue, so two distinct
`url_origen` on one `fuente` are two distinct properties — whatever their
addresses look like. Merging ACROSS portals, which is what the anchor exists
for, is untouched.
"""
from app.graphs.extraction.nodes import deduplicate_properties
from app.models.property import NormalizedProperty


def _prop(direccion: str, *, precio: float = 380_000.0,
          fuente: str = 'zonaprop', url: str | None = None) -> NormalizedProperty:
    return NormalizedProperty(
        direccion=direccion, precio=precio, moneda='USD',
        tipo_operacion='venta', tipo_propiedad='casa',
        fuente=fuente, url_origen=url,
    )


def _dedup(*props: NormalizedProperty) -> list[NormalizedProperty]:
    return deduplicate_properties(
        {'normalized_properties': list(props)}
    )['normalized_properties']


class TestTheReportedLoss:
    def test_two_houses_on_one_block_both_survive(self):
        """THE regression: same block, same asking price, different listings."""
        out = _dedup(
            _prop('Calle 47 e/ 12 y 13', url='https://zp/casa-1'),
            _prop('Calle 47 e/ 12 y 13', url='https://zp/casa-2'),
        )
        assert len(out) == 2

    def test_a_whole_block_survives(self):
        out = _dedup(*[
            _prop('Calle 47 e/ 12 y 13', url=f'https://zp/casa-{i}')
            for i in range(12)
        ])
        assert len(out) == 12

    def test_differing_only_by_url_is_enough(self):
        """Everything else identical — address, price, currency, type."""
        out = _dedup(
            _prop('Calle 7 500', url='https://zp/a'),
            _prop('Calle 7 500', url='https://zp/b'),
        )
        assert len(out) == 2


class TestWhatDedupIsActuallyFor:
    def test_the_same_property_across_portals_still_merges(self):
        """The anchor exists for this and it must keep working: each portal
        writes the same address its own way, all reducing to `7 500`."""
        out = _dedup(
            _prop('Calle 7 500', fuente='zonaprop', url='https://zp/x'),
            _prop('Calle 7 nro 500', fuente='argenprop', url='https://ap/y'),
        )
        assert len(out) == 1
        assert out[0].fuente == 'zonaprop'

    def test_one_listing_seen_twice_still_collapses(self):
        """Same portal, same URL — a genuine repeat, e.g. across pages."""
        out = _dedup(
            _prop('Calle 7 500', url='https://zp/a'),
            _prop('Calle 7 500', url='https://zp/a'),
        )
        assert len(out) == 1

    def test_three_portals_collapse_to_one(self):
        out = _dedup(
            _prop('Calle 7 500', fuente='zonaprop', url='https://zp/x'),
            _prop('Calle 7 nro 500', fuente='argenprop', url='https://ap/y'),
            _prop('7 500, La Plata', fuente='mudafy', url='https://mu/z'),
        )
        assert len(out) == 1

    def test_a_different_price_was_never_a_duplicate(self):
        out = _dedup(
            _prop('Calle 7 500', precio=380_000.0, url='https://zp/a'),
            _prop('Calle 7 500', precio=390_000.0, url='https://zp/b'),
        )
        assert len(out) == 2


class TestWithoutAUrlWeCannotTellThemApart:
    def test_it_stays_conservative(self):
        """No `url_origen` means no evidence they are distinct, so the old
        collapsing behaviour stands rather than inventing duplicates."""
        out = _dedup(
            _prop('Calle 7 500', url=None),
            _prop('Calle 7 500', url=None),
        )
        assert len(out) == 1

    def test_a_urlless_copy_does_not_block_a_real_one(self):
        out = _dedup(
            _prop('Calle 7 500', url='https://zp/a'),
            _prop('Calle 7 500', url=None),
        )
        assert len(out) == 1
