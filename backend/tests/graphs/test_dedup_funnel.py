"""Dedup is the last place a scraped listing can vanish, and it does so
silently: `deduplicate_properties` returns a shorter list and nothing records
how much shorter, or which portal paid for it.

That matters because the key is deliberately wide — same street number, price,
currency, operation and type collapse into one row — so a portal that
publishes several units of one building at one price loses them here, not at
the scraper. Counting the drop per `fuente` is what separates "ZonaProp
returned few" from "ZonaProp returned plenty and dedup ate them".
"""
import pytest

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


def _run(props: list[NormalizedProperty], caplog: pytest.LogCaptureFixture) -> str:
    with caplog.at_level('INFO', logger='app.graphs.extraction.nodes'):
        deduplicate_properties({'normalized_properties': props})
    return ' '.join(r.getMessage() for r in caplog.records)


def test_logs_the_drop_with_totals(caplog: pytest.LogCaptureFixture) -> None:
    props = [_prop('Av. Santa Fe 1234'), _prop('Av. Santa Fe 1234'), _prop('Calle 47 500')]

    blob = _run(props, caplog)

    assert 'dedup funnel' in blob
    assert 'in=3' in blob
    assert 'out=2' in blob
    assert 'dropped=1' in blob


def test_attributes_the_drop_to_the_portal_that_lost_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two ZonaProp units of one building at one price collapse; the argenprop
    row survives. The log must say ZonaProp lost one, not just "1 dropped"."""
    props = [
        _prop('Av. Santa Fe 1234', fuente='zonaprop'),
        _prop('Av. Santa Fe 1234', fuente='zonaprop'),
        _prop('Calle 47 500', fuente='argenprop'),
    ]

    blob = _run(props, caplog)

    assert 'zonaprop=1' in blob
    assert 'argenprop' not in blob.split('by_fuente')[-1].split(']')[0]


def test_stays_quiet_when_nothing_is_dropped(caplog: pytest.LogCaptureFixture) -> None:
    """A no-op dedup must not add noise to every single search."""
    props = [_prop('Av. Santa Fe 1234'), _prop('Calle 47 500')]

    blob = _run(props, caplog)

    assert 'dedup funnel' not in blob


def test_dedup_still_returns_the_unique_props(caplog: pytest.LogCaptureFixture) -> None:
    """Instrumentation must not change the node's contract."""
    props = [_prop('Av. Santa Fe 1234'), _prop('Av. Santa Fe 1234')]

    out = deduplicate_properties({'normalized_properties': props})

    assert len(out['normalized_properties']) == 1
