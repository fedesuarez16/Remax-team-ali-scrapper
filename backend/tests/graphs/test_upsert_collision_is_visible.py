"""The DB has a dedup of its own, and it was silent.

`properties_dedup_idx` is `unique (direccion, precio, tipo_operacion)` and
`_upsert_properties` writes with `ignore_duplicates=True`, so rows that share
that triple are dropped by Postgres without a word. A real search ended with
the graph handing over 54 properties, the UI announcing 54, and the results
view showing 11 — the gap happened entirely inside that write.

`direccion` is not an identity. `_norm_zonaprop` falls back to
`neighborhood`, then to the zona, whenever the portal publishes no street
address, so a whole barrio's listings can share the string "La Plata" and
collapse on price alone.

This logs the collapse BEFORE the write, so the loss is attributable without
having to query the database.
"""
import logging

import pytest

from app.graphs.extraction.nodes import _upsert_properties
from app.models.property import NormalizedProperty


def _prop(direccion: str, precio: float, *, url: str | None = None) -> NormalizedProperty:
    return NormalizedProperty(
        direccion=direccion, precio=precio, moneda='USD',
        tipo_operacion='venta', tipo_propiedad='casa',
        fuente='zonaprop', url_origen=url,
    )


class _FakeQuery:
    """Permissive chainable stand-in: every builder call returns self and every
    execute answers empty, so the collision log is what the test observes."""
    def __getattr__(self, _name):
        return lambda *a, **kw: self

    async def execute(self):
        return type('R', (), {'data': []})()


class _FakeSb:
    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery()


@pytest.fixture(autouse=True)
def _no_geocode_backfill(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_upsert_properties` fires geocoding off as a background task."""
    async def _noop(*a, **kw):
        return {}
    import app.services.geocode as geocode
    monkeypatch.setattr(geocode, 'run_backfill', _noop)


async def _run(props, caplog) -> str:
    with caplog.at_level(logging.WARNING, logger='app.graphs.extraction.nodes'):
        await _upsert_properties(_FakeSb(), props, 'job-1')
    return ' '.join(r.getMessage() for r in caplog.records)


async def test_distinct_urls_at_one_address_no_longer_collide(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Since `properties_dedup_idx` gained `url_origen`, five listings sharing
    an address and a price are five rows. The log measured the OLD triple and
    kept announcing a collapse that no longer happens — instrumentation that
    lies is worse than none."""
    props = [
        _prop('La Plata', 350_000.0, url='https://zp/1'),
        _prop('La Plata', 350_000.0, url='https://zp/2'),
        _prop('La Plata', 350_000.0, url='https://zp/3'),
        _prop('La Plata', 400_000.0, url='https://zp/4'),
        _prop('La Plata', 400_000.0, url='https://zp/5'),
    ]

    blob = await _run(props, caplog)

    assert 'upsert collision' not in blob


async def test_the_same_listing_twice_is_still_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A real collision under the CURRENT index: same URL, same everything."""
    props = [
        _prop('La Plata', 350_000.0, url='https://zp/1'),
        _prop('La Plata', 350_000.0, url='https://zp/1'),
        _prop('Calle 7 500', 360_000.0, url='https://zp/2'),
    ]

    blob = await _run(props, caplog)

    assert 'upsert collision' in blob
    assert 'filas=3' in blob
    assert 'distintas=2' in blob
    assert 'La Plata' in blob
    assert 'Calle 7 500' not in blob   # it collides with nothing


async def test_rows_without_a_url_still_collide_on_the_triple(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Default NULL semantics let null-URL rows repeat in the index, but they
    are indistinguishable to us, so the warning still earns its place."""
    props = [
        _prop('La Plata', 350_000.0, url=None),
        _prop('La Plata', 350_000.0, url=None),
    ]

    blob = await _run(props, caplog)

    assert 'upsert collision' in blob


async def test_a_clean_write_says_nothing(caplog: pytest.LogCaptureFixture) -> None:
    props = [
        _prop('Calle 7 500', 350_000.0, url='https://zp/1'),
        _prop('Calle 13 470', 360_000.0, url='https://zp/2'),
    ]

    blob = await _run(props, caplog)

    assert 'upsert collision' not in blob
